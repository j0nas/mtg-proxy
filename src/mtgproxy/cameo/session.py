"""One cutting job end to end, over any transport: preflight, blade setup, registration scan,
cut, and — whatever happens after the machine was touched — a return to the origin.

Coordinates: the job is in page millimetres (y down). After the scan the machine's origin is the
top-left mark's corner, so a page point maps to (x − inset + bias_x, y − inset + bias_y). Nothing
may land above or left of that corner (negative registered coordinates are untested), and
nothing may leave the paper.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import protocol as p
from .geometry import Polyline, bbox
from .transport import Transport


class CutError(Exception):
    pass


SCAN_TIMEOUT_S = 90.0  # a full four-mark search, including the machine's own outward search
CHUNK_BYTES = 1024
CHUNK_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class Frame:
    """The printed registration marks, in page millimetres."""

    page_w: float
    page_h: float
    inset: float  # mark corners' distance from the top and left paper edges
    width: float  # top-left → top-right mark corner
    height: float  # top-left → bottom-left mark corner
    length: float = 9.4  # leg length
    thickness: float = 1.0
    marks: int = 4  # 4 = L-marks in all four corners; 3 = square top-left + two L's


@dataclass
class Job:
    lines: list[Polyline]  # page mm, already ordered and multiplied into passes
    frame: Frame
    blade: p.Blade
    bias: tuple[float, float] = (0.0, 0.0)  # (x, y) mm, the machine's measured cut offset


def registered(job: Job) -> list[list[tuple[float, float]]]:
    """Every polyline in the machine's registered frame (x, y mm from the top-left mark)."""
    f, (bx, by) = job.frame, job.bias
    return [[(x - f.inset + bx, y - f.inset + by) for x, y in pl.points] for pl in job.lines]


def check_bounds(job: Job) -> tuple[float, float, float, float]:
    """The commanded extent (x0, y0, x1, y1) from the top-left mark. Raises if a commanded point
    lies above/left of that mark (negative coordinates are untested), or if the design itself
    (page coordinates, before the machine's bias) leaves the paper."""
    pts = [pt for line in registered(job) for pt in line]
    x0, y0 = min(x for x, _ in pts), min(y for _, y in pts)
    x1, y1 = max(x for x, _ in pts), max(y for _, y in pts)
    if x0 < 0 or y0 < 0:
        raise CutError(
            f"the cut reaches {-min(x0, 0):.2f} mm left / {-min(y0, 0):.2f} mm above the top-left mark's "
            "corner; keep the design right of and below it"
        )
    f = job.frame
    px0, py0, px1, py1 = bbox(job.lines)
    if px0 < 0 or py0 < 0 or px1 > f.page_w or py1 > f.page_h:
        raise CutError(
            f"the cut runs off the {f.page_w:g} x {f.page_h:g} mm page "
            f"(it spans x {px0:.2f}..{px1:.2f}, y {py0:.2f}..{py1:.2f} mm)"
        )
    return x0, y0, x1, y1


POINTS_PER_DRAW = 32  # keeps each D command under ~400 bytes


def cut_commands(job: Job) -> list[str]:
    """A move to each path's start, then its points as multi-point draws, in SU with repeats
    (points that round to the same 0.05 mm step) dropped."""
    cmds: list[str] = []
    for line in registered(job):
        pts: list[tuple[int, int]] = []
        for x, y in line:
            pt = (p.su(y), p.su(x))
            if not pts or pt != pts[-1]:
                pts.append(pt)
        if len(pts) < 2:
            continue
        cmds.append(f"M{pts[0][0]},{pts[0][1]}")
        rest = pts[1:]
        cmds.extend(p.draw_path(rest[i : i + POINTS_PER_DRAW]) for i in range(0, len(rest), POINTS_PER_DRAW))
    return cmds


class SessionLog:
    """JSON lines with a timestamp per event: the record of what the machine actually did."""

    def __init__(self, path: Path | None, echo: Callable[[str], None] | None = None):
        self.t0 = time.monotonic()
        self.fh = path.open("w") if path else None
        self.echo = echo

    def __call__(self, event: str, **fields) -> None:
        rec = {"t": round(time.monotonic() - self.t0, 3), "event": event}
        for k, v in fields.items():
            rec[k] = v.decode("latin1") if isinstance(v, bytes | bytearray) else v
        if self.fh:
            self.fh.write(json.dumps(rec) + "\n")
            self.fh.flush()

    def say(self, msg: str) -> None:
        self("note", msg=msg)
        if self.echo:
            self.echo(msg)

    def close(self) -> None:
        if self.fh:
            self.fh.close()


@dataclass
class Session:
    t: Transport
    log: SessionLog
    touched: bool = field(default=False)  # sent anything that moves the machine

    def send(self, *commands: str) -> None:
        self.touched = True
        self.t.write(p.cmd(*commands))

    def query(self, raw: bytes, timeout: float = 5.0) -> bytes | None:
        stale = self.t.drain()
        if stale:
            self.log("stale", replies=[s.decode("latin1") for s in stale])
        self.t.write(raw)
        return self.t.reply(timeout)

    def status(self) -> str:
        return p.status_of(self.query(p.raw(p.STATUS)))

    def wait_ready(self, timeout: float, poll: float = 0.5) -> str:
        end = time.monotonic() + timeout
        while True:
            s = self.status()
            if s != "moving" or time.monotonic() > end:
                return s
            time.sleep(poll)

    def preflight(self) -> str:
        """The machine's firmware string, once it reports a loaded mat and an idle head."""
        self.t.write(p.raw(p.INIT))
        version = self.query(p.cmd(p.VERSION), timeout=10)
        v = version.decode("latin1").rstrip(p.ETX).strip() if version else "?"
        self.log("version", version=v)
        s = self.status()
        if s == "unloaded":
            raise CutError("no mat loaded: load it (the Cameo's load button), then rerun")
        if s != "ready":
            raise CutError(f"the Cameo isn't ready (status {s}); wait for it to finish, or power-cycle it")
        return v

    def prepare(self) -> None:
        self.send(*p.PREPARE)

    def setup(self, blade: p.Blade, frame: Frame) -> None:
        """The blade, inside a cutting area reaching the paper's far edges (+2 mm for the bias)."""
        area_y = frame.page_h - frame.inset + 2
        area_x = frame.page_w - frame.inset + 2
        self.send(*p.blade_setup(blade, area_y, area_x))

    def register(self, frame: Frame, starts: list[tuple[float, float]]) -> tuple[float, float]:
        """Scan the marks, trying each (top, left) start in mm until the machine reports them found.

        The start is where the sensor begins searching, from the origin (the mat's loaded
        position). Studio puts it on white paper above the top-left mark's horizontal leg, just
        past its vertical leg, so the sensor crosses a mark line and not the paper edge. Every
        attempt is logged with each reply and its timing.
        """
        kind = 124 if frame.marks == 4 else 123
        for n, (top, left) in enumerate(starts, 1):
            stale = self.t.drain()
            if stale:
                self.log("stale", replies=[s.decode("latin1") for s in stale])
            self.send(*p.mark_setup(frame.length, frame.thickness))
            scan = p.scan(kind, frame.height, frame.width, top, left)
            t0 = time.monotonic()
            self.send(*scan)
            result, others = None, []
            while result is None and (left_s := SCAN_TIMEOUT_S - (time.monotonic() - t0)) > 0:
                reply = self.t.reply(left_s)
                if reply is None:
                    break
                result = p.scan_result(reply)
                if result is None:  # not a scan result (the padding tells them apart): log, keep waiting
                    others.append(reply.decode("latin1"))
            took = time.monotonic() - t0
            self.log("scan", command=scan[0], result=result, seconds=round(took, 2), other_replies=others)
            if result == "found":
                self.log.say(f"marks found in {took:.0f} s (scan start {top:g}, {left:g} mm)")
                return top, left
            self.log.say(
                f"scan {n}/{len(starts)} from ({top:g}, {left:g}) mm: {result or 'no reply'} after {took:.0f} s"
            )
            self.wait_ready(60)
        raise CutError(
            f"the Cameo couldn't find the marks ({len(starts)} scan start(s) tried). Check: printed at "
            f"100 %, sheet top-left on the mat grid, nothing printed within 6 mm of a mark, no glare."
        )

    def cut(self, job: Job) -> None:
        """Send the cut in ≤1 KB pieces split between commands, letting the machine finish each
        before the next (the old driver's pacing: slow, but it never overran the Cameo's buffer)."""
        data = p.cmd(*cut_commands(job))
        i = 0
        while i < len(data):
            if len(data) - i <= CHUNK_BYTES:
                end = len(data)
            else:  # the last whole command that fits (or one oversized command on its own)
                cut = data.rfind(p.ETX.encode(), i, i + CHUNK_BYTES)
                end = (cut if cut >= i else data.index(p.ETX.encode(), i)) + 1
            self.touched = True
            self.t.write(data[i:end])
            state = self.wait_ready(CHUNK_TIMEOUT_S, poll=0.05)
            if state != "ready":
                raise CutError(f"the Cameo stopped mid-cut (status {state})")
            i = end
        self.log("cut_done", bytes=len(data))

    def finish(self) -> None:
        """Return to the origin (never move it). Safe to call after an error."""
        self.send(*p.RETURN_TO_ORIGIN)
