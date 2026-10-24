"""Entry points: plan a job from an SVG, open a transport, cut, or probe the registration.

``cut`` can stop between the scan and the cut for proof cuts over the marks (``proof``), which the
operator checks by eye before anything else is cut. ``probe`` is an experiment, not a cut: it scans the marks and asks the machine every query that
might report where it found them, before and after the scan. Run it twice with the sheet moved a
known distance; a reply that moves with the sheet is a readback we can check against tolerances.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import geometry, proof, protocol
from .session import CutError, Frame, Job, Session, SessionLog, check_bounds, low_corner
from .transport import BleTransport, RecordingTransport, Transport, UsbTransport

FIRMWARE = b"CAMEO 5 ALPHA V1.04    \x03"
DRY_ANSWERS = {
    b"\x1b\x05": b"0\x03",  # ready
    b"FG": FIRMWARE,
    b"TB124": b"    0\x03",  # marks found (a scan result is padded to five characters)
    b"TB123": b"    0\x03",
}

# Queries that might report registration state; the probe logs each reply before and after a
# scan. On firmware V1.04 FQ5 changes with the scan (0 → -64 on a straight sheet) and [ / U report
# the cutting area's corners; the GP-GL O* outputs get no reply at all, so they are left out.
PROBE_QUERIES = ("FQ0", "FQ1", "FQ2", "FQ3", "FQ4", "FQ5", "FQ6", "FQ7", "FQ8", "FQ9", "[", "U", "TB71", "FA")


SEARCH_STEP = 2.0  # mm between neighbouring scan starts: about the width of the window that works
# The 3 x 3 neighbourhood of the first start, likeliest first: across, then down and up, then the
# diagonals. On 2026-10-20 the sheets that registered needed 2 mm across or 2 mm down.
SEARCH_ORDER = ((0, 0), (0, -1), (0, 1), (1, 0), (-1, 0), (1, -1), (1, 1), (-1, -1), (-1, 1))
LOWEST = 1.0  # mm: no start above or left of this


def default_start(frame: Frame) -> tuple[float, float]:
    """Where a sheet's mark search starts, (top, left) mm from the origin, before any sheet with
    these marks has registered (``cutting`` remembers the last start that did).

    The sensor travels down from here onto the top-left mark's horizontal leg. It registers only
    from a window about 2 mm wide each way, and from one load to the next the marks move about
    that much: the hand's millimetre on the mat, and where the machine takes the mat in. So no
    single start always works, and ``search_starts`` tries the ones around it.

    At the card sheets' 10 mm inset the start is (7.5, 18), from the laminated sheets of
    2026-10-20: they registered from (7.5, 20), (7.5, 18) and (9.5, 18) and missed from
    (7.5, 22), (7.5, 16), (5.5, 18) and, on most sheets that night, from the old default
    (6, 17). Studio's (2.5, 11.5) failed on laminate now and then. The 5 mm deck box keeps its
    proven (2.5, 11.5): starting 0.5 mm past the vertical leg skewed its cut and 11.5 did not
    (calibration sheets, 2026-09-28).

    A 3-mark sheet (TB123) starts 2 mm above and 6 mm left of its square's corner: (8, 4) at a
    10 mm inset, which registered first time on 2026-10-24. The machine finds the square by an
    area search, not one line down: rows 3 mm apart from the start to 30 mm below it, each
    sweeping 30 mm to its right, for a dark run 3.5-9 mm long (firmware V1.04, cameo-re
    NOTES.md). The sweep has to cross the square's left edge from white paper: starts 2-6 mm in
    from the left all found it that night, and starts at 11-13 mm, on or past the square, never
    did (reply 1). The square must print exactly 5 mm (the engine fork's cd4166a): at 6 mm the
    machine found it and then failed to measure it (reply -1) from every start. On the 3x3
    layout's 3.5 mm inset the start clamps to (1.5, 1), 2 mm above and 2.5 mm left of a square
    printed at 3.5-8.5 mm, with only that much paper between the start and the edge. Untested on
    the machine."""
    if frame.marks == 3:
        return max(LOWEST, frame.inset - 2), max(LOWEST, frame.inset - 6)
    if frame.inset <= 5:
        return frame.inset / 2, min(11.5, frame.inset + frame.length - 2)
    return frame.inset - 2.5, frame.inset + 8


def search_starts(first: tuple[float, float]) -> list[tuple[float, float]]:
    """``first``, then the starts SEARCH_STEP around it, in SEARCH_ORDER: each one the machine
    tries in turn until it finds the marks (a miss costs 15-20 s)."""
    top, left = first
    return [
        (max(LOWEST, top + dy * SEARCH_STEP), max(LOWEST, left + dx * SEARCH_STEP)) for dy, dx in SEARCH_ORDER
    ]


def default_starts(frame: Frame) -> list[tuple[float, float]]:
    """The search around ``default_start``: what a sheet tries when no start is remembered."""
    return search_starts(default_start(frame))


def plan(svg: Path, frame: Frame, blade: protocol.Blade, passes: int, bias: tuple[float, float]) -> Job:
    lines = geometry.load_svg(svg)
    if not lines:
        raise CutError(f"{svg} has no paths to cut")
    ordered = geometry.order(lines, start=(frame.inset, frame.inset))
    job = Job(geometry.passes(ordered, passes), frame, blade, bias, shapes=ordered)
    check_bounds(job)
    return job


def open_transport(connection: str, ble_name: str, log: SessionLog, dry_run: bool) -> Transport:
    if dry_run:
        return RecordingTransport(log, DRY_ANSWERS)
    if connection == "usb":
        return UsbTransport(log)
    return BleTransport.connect(ble_name, log)


@dataclass
class Result:
    firmware: str
    start: tuple[float, float]
    extent: tuple[float, float, float, float]


# The operator's answer to a round of proof cuts: (round, rescan offered) → "y" cut, "r" rescan,
# anything else stops.
Confirm = Callable[[int, bool], str]


def cut(
    job: Job, t: Transport, log: SessionLog, starts: list[tuple[float, float]], confirm: Confirm | None = None
) -> Result:
    """The whole job. Once anything moved the machine, it always ends back at the origin.

    With ``confirm``, proof cuts go down over the marks between the scan and the cut, and nothing
    else is cut until the operator says so. A rescan returns to the origin and repeats a fresh run's start."""
    s = Session(t, log)
    try:
        extent = reach = check_bounds(job)
        if confirm:  # the area must hold the proof cuts too, which may reach past the job's own
            marks = [cut for _, cut in proof.cuts(job.frame, starts[0])]
            reach = check_bounds(replace(job, lines=[*job.lines, *marks]))
        low = low_corner(reach)
        rounds = proof.ROUNDS if confirm else 1
        for attempt in range(rounds):
            if attempt:
                s.finish()
                s.wait_ready(60)
            firmware = s.preflight()
            log.say(f"connected: {firmware} via {t.name}, mat loaded")
            s.prepare()
            start = s.register(job.frame, starts)
            if low != (0.0, 0.0) and attempt == 0:
                log.say(
                    f"cut reaches {max(0.0, -reach[1]):.2f} mm above / {max(0.0, -reach[0]):.2f} mm left of the "
                    f"top-left mark: cutting area widened to {low[0]:g}, {low[1]:g} mm"
                )
            s.setup(job.blade, job.frame, low)
            if confirm is None or prove(s, job, log, confirm, start, attempt, rescan=attempt + 1 < rounds):
                break
        s.cut(job)
        return Result(firmware, start, extent)
    finally:
        wrap_up(s, t, log)


def prove(
    s: Session,
    job: Job,
    log: SessionLog,
    confirm: Confirm,
    start: tuple[float, float],
    attempt: int,
    rescan: bool,
) -> bool:
    """Score the proof cuts over the marks, park the head out of the way and ask. True: cut the
    job; False: rescan. Anything but y or r stops the job (CutError)."""
    kept, skipped = [], []
    for corner, piece in proof.cuts(job.frame, start):
        if proof.on_job(piece, job.shapes or job.lines):
            skipped.append(corner)
        else:
            kept.append(piece)
    for corner in sorted(set(skipped)):
        log.say(
            f"proof: {skipped.count(corner)} {corner} proof cut(s) would touch the job, so they are left out"
        )
    s.cut(replace(job, lines=kept))
    s.send(protocol.move(0, job.frame.width / 2))  # the head to the top edge's middle, off every corner
    s.wait_ready(60)
    log("proof", round=attempt + 1, cuts=len(kept), skipped=skipped)
    answer = ask(s, confirm, attempt, rescan)
    log("proof_answer", round=attempt + 1, answer=answer)
    if answer == "y":
        return True
    if answer == "r" and rescan:
        log.say(f"rescanning (proof round {attempt + 2} of {proof.ROUNDS})")
        return False
    raise CutError("stopped at the proof cuts: nothing else was cut")


def ask(s: Session, confirm: Confirm, attempt: int, rescan: bool, poll_s: float = 10.0) -> str:
    """The operator's answer; meanwhile a status poll every ``poll_s`` s, so the machine hears
    from us while they look. No answer (stdin closed) means stop."""
    answer: list[str] = []

    def wait() -> None:
        with contextlib.suppress(Exception):  # EOF on stdin, a closed terminal: treated as q
            answer.append(confirm(attempt, rescan))

    th = threading.Thread(target=wait, name="proof-answer", daemon=True)
    th.start()
    while th.is_alive():
        th.join(poll_s)
        if th.is_alive():
            s.status()
    return answer[0] if answer else "q"


def probe(frame: Frame, t: Transport, log: SessionLog, starts: list[tuple[float, float]]) -> dict:
    """Scan without cutting; every probe query's reply before and after the scan."""
    s = Session(t, log)
    replies: dict[str, list[str | None]] = {}
    try:
        firmware = s.preflight()
        log.say(f"connected: {firmware} via {t.name}, mat loaded")
        s.prepare()

        def ask(phase: int) -> None:
            for q in PROBE_QUERIES:
                r = s.query(protocol.cmd(q), timeout=2.0)
                text = r.decode("latin1").rstrip("\x03") if r is not None else None
                replies.setdefault(q, [None, None])[phase] = text
                log("probe", query=q, phase=("before", "after")[phase], reply=text)

        ask(0)
        start = s.register(frame, starts)
        ask(1)
        return {"firmware": firmware, "start": start, "replies": replies}
    finally:
        wrap_up(s, t, log)


def wait_for_reload(
    t: Transport, log: SessionLog, interrupted: Callable[[float], bool], poll_s: float = 0.5
) -> bool:
    """Poll the status until the mat has gone out ("unloaded") and come back in ("ready"): True.
    ``interrupted(poll_s)`` waits between polls and returns True to give up (False is returned).
    Only the status query is sent, so nothing moves and nothing needs undoing.

    On the machine (2026-10-20) a reload reads unloaded → CONTROL "0" → ready, with no "moving"
    in between at this poll rate."""
    s = Session(t, log)
    gone = False
    while True:
        state = s.status()
        if state == "unloaded":
            gone = True
        elif state == "ready" and gone:
            log("reloaded")
            return True
        if interrupted(poll_s):
            return False


def wrap_up(s: Session, t: Transport, log: SessionLog) -> None:
    """Back to the origin if anything moved, then disconnect. A failure here is reported but
    never replaces the error that got us here."""
    try:
        if s.touched:
            s.finish()
            s.wait_ready(60)
    except Exception as e:  # the link may already be gone
        log.say(
            f"WARNING: couldn't send the return to the origin ({e}); power-cycle the Cameo before the next job"
        )
    finally:
        t.close()
