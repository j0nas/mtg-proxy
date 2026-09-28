"""Entry points: plan a job from an SVG, open a transport, cut, or probe the registration.

``probe`` is an experiment, not a cut: it scans the marks and asks the machine every query that
might report where it found them, before and after the scan. Run it twice with the sheet moved a
known distance; a reply that moves with the sheet is a readback we can check against tolerances.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import geometry, protocol
from .session import CutError, Frame, Job, Session, SessionLog, check_bounds
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
    the top stays on the paper (at most 2.5 mm, and never more than halfway to the mark)."""
    top = max(frame.inset - 7.5, min(2.5, frame.inset / 2))
    left = frame.inset + frame.thickness + 0.5
    return [(top, left), (top, left)]


def plan(svg: Path, frame: Frame, blade: protocol.Blade, passes: int, bias: tuple[float, float]) -> Job:
    lines = geometry.load_svg(svg)
    if not lines:
        raise CutError(f"{svg} has no paths to cut")
    ordered = geometry.order(lines, start=(frame.inset, frame.inset))
    job = Job(geometry.passes(ordered, passes), frame, blade, bias)
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


def cut(job: Job, t: Transport, log: SessionLog, starts: list[tuple[float, float]]) -> Result:
    """The whole job. Once anything moved the machine, it always ends back at the origin."""
    s = Session(t, log)
    try:
        firmware = s.preflight()
        log.say(f"connected: {firmware} via {t.name}, mat loaded")
        s.prepare()
        start = s.register(job.frame, starts)
        s.setup(job.blade, job.frame)
        s.cut(job)
        return Result(firmware, start, check_bounds(job))
    finally:
        wrap_up(s, t, log)


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
