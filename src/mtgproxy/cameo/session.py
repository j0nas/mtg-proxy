"""One cutting job end to end, over any transport: preflight, blade setup, registration scan,
cut, and — whatever happens after the machine was touched — a return to the origin.

Coordinates: the job is in page millimetres (y down). Before the scan the origin is the mat
grid's corner, and a page point is where the paper lies on the mat (``Frame.on_mat``). After the
scan the machine's origin is the top-left mark's corner, so a page point maps to
(x − inset + bias_x, y − top + bias_y). Nothing may land above or left of that corner (negative
registered coordinates are untested), and nothing may leave the paper.
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


SCAN_TIMEOUT_S = 90.0  # a whole scan, the firmware's own second try included (28 s on 2026-10-25)
CHUNK_BYTES = 1024
CHUNK_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class Frame:
    """The printed registration marks, in page millimetres: 3-mark registration, a 5 mm square
    top-left and L marks top-right and bottom-left. And where the paper lies on the loaded mat
    (``paper_at``), which everything before the scan moves by (``on_mat``)."""

    page_w: float
    page_h: float
    inset: float  # mark corners' distance from the left and right paper edges (and top/bottom, see inset_y)
    width: float  # top-left → top-right mark corner
    height: float  # top-left → bottom-left mark corner
    length: float = 9.4  # leg length
    thickness: float = 1.0
    inset_y: float | None = None  # from the top and bottom edges, when it differs from inset (3x3)
    # (right, down) mm from the mat grid's corner to the paper's top-left corner (--paper-at). About
    # 3 mm both ways for a laminated sheet laid with its whole pouch on the tack (the deck box).
    paper_at: tuple[float, float] = (0.0, 0.0)

    @property
    def top(self) -> float:
        """The mark corners' distance from the top (and bottom) paper edge."""
        return self.inset if self.inset_y is None else self.inset_y

    def on_mat(self, y: float, x: float) -> tuple[float, float]:
        """A page point (y, x mm) in the loaded mat's frame, (y, x) as a move takes it: where the
        head goes before the scan, for every reading and the default scan start. After the scan
        the registered frame (from the top-left mark) needs none of this."""
        right, down = self.paper_at
        return y + down, x + right

    def placed(self) -> str:
        """Where the paper's top-left corner goes, for the messages."""
        if self.paper_at == (0, 0):
            return "on the mat grid's"
        right, down = self.paper_at
        return f"{right:g} mm right and {down:g} mm down of the mat grid's"


@dataclass
class Job:
    lines: list[Polyline]  # page mm, already ordered and multiplied into passes
    frame: Frame
    blade: p.Blade
    bias: tuple[float, float] = (0.0, 0.0)  # (x, y) mm, the machine's measured cut offset
    shapes: list[Polyline] = field(default_factory=list)  # each path once, before passes


def registered(job: Job) -> list[list[tuple[float, float]]]:
    """Every polyline in the machine's registered frame (x, y mm from the top-left mark)."""
    f, (bx, by) = job.frame, job.bias
    return [[(x - f.inset + bx, y - f.top + by) for x, y in pl.points] for pl in job.lines]


def check_bounds(job: Job) -> tuple[float, float, float, float]:
    """The commanded extent (x0, y0, x1, y1) from the top-left mark. Raises if the design itself
    (page coordinates, before the machine's bias) leaves the paper. A cut may reach above or left
    of the top-left mark (negative coordinates, down to the paper's edge): the cutting area is
    widened to cover it (see ``low_corner``)."""
    pts = [pt for line in registered(job) for pt in line]
    x0, y0 = min(x for x, _ in pts), min(y for _, y in pts)
    x1, y1 = max(x for x, _ in pts), max(y for _, y in pts)
    f = job.frame
    px0, py0, px1, py1 = bbox(job.lines)
    if px0 < 0 or py0 < 0 or px1 > f.page_w or py1 > f.page_h:
        raise CutError(
            f"the cut runs off the {f.page_w:g} x {f.page_h:g} mm page "
            f"(it spans x {px0:.2f}..{px1:.2f}, y {py0:.2f}..{py1:.2f} mm)"
        )
    return x0, y0, x1, y1


def low_corner(extent: tuple[float, float, float, float]) -> tuple[float, float]:
    """The cutting area's near corner (y, x) for a job's commanded extent: the top-left mark (0, 0),
    Studio's, unless the cut reaches above or left of it; then 0.5 mm past the farthest point."""
    x0, y0 = extent[0], extent[1]
    return (min(0.0, y0 - 0.5) if y0 < 0 else 0.0, min(0.0, x0 - 0.5) if x0 < 0 else 0.0)


def far_corner(frame: Frame) -> tuple[float, float]:
    """The cutting area's far corner (y, x) from the top-left mark: the paper's far edges, +2 mm
    for the bias."""
    return frame.page_h - frame.top + 2, frame.page_w - frame.inset + 2


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
        """Poll until the machine stops moving: its status then. A pause on the machine waits for
        the operator to press pause again, however long, and doesn't count against ``timeout``.
        On 2026-10-26 the driver took a pause mid-cut for an error, sent the head home and let go,
        and the machine's pause button could no longer resume the cut."""
        end = time.monotonic() + timeout
        told = False
        while True:
            s = self.status()
            if s == "paused":
                if not told:
                    self.log.say("paused on the Cameo: press its pause button again to carry on")
                    told = True
                end = time.monotonic() + timeout
            elif s != "moving" or time.monotonic() > end:
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

    def setup(self, blade: p.Blade, frame: Frame, low: tuple[float, float] = (0.0, 0.0)) -> None:
        """The blade, inside a cutting area reaching the paper's far edges (+2 mm for the bias)
        and, when a cut reaches above or left of the top-left mark, back to ``low`` (y, x)."""
        self.send(*p.blade_setup(blade, *far_corner(frame), *low))

    def sense(self, y_mm: float, x_mm: float, slot: int) -> tuple[int | None, str | None]:
        """Move the head to (y, x) mm in the current frame and read the spot under it
        (protocol.sense): the level, and the reply as it came."""
        self.send(p.move(y_mm, x_mm))
        self.wait_ready(30)
        reply = self.query(p.cmd(p.sense(slot)), timeout=10)
        return p.number(reply), None if reply is None else reply.decode("latin1").rstrip(p.ETX)

    def register(
        self, frame: Frame, starts: list[tuple[float, float]], marks: int = 3
    ) -> tuple[float, float]:
        """Scan the marks, trying each (top, left) start in mm until the machine reports them found.

        The start is where the sensor begins its area search for the square, from the origin (the
        mat's loaded position): see ``driver.default_start``. Every attempt is logged with each
        reply and its timing.
        """
        for n, (top, left) in enumerate(starts, 1):
            stale = self.t.drain()
            if stale:
                self.log("stale", replies=[s.decode("latin1") for s in stale])
            self.send(*p.mark_setup(frame.length, frame.thickness))
            scan = p.scan(frame.height, frame.width, top, left, marks)
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
        top, left = starts[0]
        raise CutError(
            f"the Cameo couldn't find the marks from {len(starts)} scan start(s), the first at "
            f"({top:g}, {left:g}) mm. Check: printed at 100 %, the paper's top-left corner "
            f"{frame.placed()}, nothing printed within 6 mm of a mark, no glare. Or pass --scan-start "
            "TOP,LEFT (mm from the origin: about 2 mm below the square's top edge, a few mm left of it)."
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
