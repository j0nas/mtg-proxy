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


def default_starts(frame: Frame) -> list[tuple[float, float]]:
    """Where the mark search starts, (top, left) mm from the origin, tried twice.

    Studio's start for its 10 mm inset is (2.5, 11.5) on this machine (PacketLogger capture,
    docs/studio-capture.md): 7.5 mm above the top-left mark's horizontal leg, 0.5 mm past its
    vertical leg, so the sensor travels down onto a mark line. The old driver started at the
    paper's corner (0, 0), running the sensor along the paper edge instead. For a smaller inset
    the top stays on the paper (at most 2.5 mm, and never more than halfway to the mark), and the
    left stays at 11.5 mm, further along the leg: at a 5 mm inset, starting 1.5 mm past the
    vertical leg skewed the cut; starting at 11.5 did not (calibration sheets, 2026-09-28)."""
    top = max(frame.inset - 7.5, min(2.5, frame.inset / 2))
    left = max(frame.inset + frame.thickness + 0.5, min(11.5, frame.inset + frame.length - 1))
    return [(top, left), (top, left)]


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
        extent = check_bounds(job)
        low = low_corner(extent)
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
                    f"cut reaches {-min(extent[1], 0):.2f} mm above / {-min(extent[0], 0):.2f} mm left of the "
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
