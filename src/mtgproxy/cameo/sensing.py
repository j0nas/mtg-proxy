"""Reading the printed marks back with the machine's own sensor, after the scan.

The machine never says where it found the marks (cameo-re NOTES.md), so a misread only showed in
the cut: on 2026-10-25 one laminated 3x3 sheet registered "found" in a normal 11 s and cut shifted.
TB63 (protocol.sense) reads the light the spot under the head reflects. After the scan, a move goes
through the registered frame, so the head can be sent to where each mark should be and asked what
it sees: ink where the frame says a mark is, paper beside it. A frame that disagrees with the print
is a misread, found before the blade goes down.

``profiles`` was the first experiment (``cut-proxies --sense``): rows of readings across each mark's
edges. On a laminated 3x3 sheet (2026-10-25) ink read 255 every time, the most light TB63 can give
without the sensor passing half scale, and paper 151-242. Every mark read where the registered
frame put it, within 0.25 mm, and a 1 mm leg read 255 across its middle 0.5-0.75 mm.

``checks`` is what a cut reads before the blade goes down (``cut-proxies`` runs it unless told
not to): ink in the middle of the square and on each L leg's centre line, paper just outside
them. A frame off by about half a millimetre or more fails it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .proof import L_MARKS, SQUARE
from .session import Frame

SQUARE_STEP = 0.5  # mm between readings across the 5 mm square
SQUARE_REACH = 2.5  # mm read past each of its edges
LEG_STEP = 0.25  # mm between readings across a 1 mm L leg
LEG_REACH = 2.0  # mm read either side of its centre line

INK = 250  # at or above: ink. Ink read 255 on 2026-10-25 (253 where the spot was half on it)
PAPER = 245  # at or below: paper. Paper read 151-242, 242 only half a mm from a leg
OUTSIDE_SQUARE = 1.25  # mm from the square's edge to its paper checks (it read 0.5 mm wide each side)
OUTSIDE_LEG = 1.0  # mm from a leg's centre line to its paper checks
NUDGE = 0.25  # mm either side of a leg's centre line, read when the centre itself isn't ink


@dataclass(frozen=True)
class Point:
    """One reading's place: page mm, and how far along its profile it sits from the line it
    crosses (the square's near edge, or a leg's centre line), in the profile's direction."""

    profile: str
    x: float
    y: float
    offset: float


def _steps(start: float, stop: float, step: float) -> list[float]:
    n = round((stop - start) / step)
    return [round(start + i * step, 4) for i in range(n + 1)]


def profiles(frame: Frame) -> list[Point]:
    """Rows of readings across every mark, page mm: the square across x and across y through its
    middle, and each L's two legs across their centre lines halfway along them."""
    x0, y0 = frame.inset, frame.top
    mid = SQUARE / 2
    out = [
        Point("square across x", x0 + d, y0 + mid, d)
        for d in _steps(-SQUARE_REACH, SQUARE + SQUARE_REACH, SQUARE_STEP)
    ]
    out += [
        Point("square across y", x0 + mid, y0 + d, d)
        for d in _steps(-SQUARE_REACH, SQUARE + SQUARE_REACH, SQUARE_STEP)
    ]
    half = frame.length / 2
    for name, (i, j), (sx, sy) in L_MARKS:
        cx, cy = frame.inset + i * frame.width, frame.top + j * frame.height
        across = _steps(-LEG_REACH, LEG_REACH, LEG_STEP)
        out += [Point(f"{name} vertical leg across x", cx + d, cy + sy * half, d) for d in across]
        out += [Point(f"{name} horizontal leg across y", cx + sx * half, cy + d, d) for d in across]
    return out


def registered(frame: Frame, pt: Point | Check) -> tuple[float, float]:
    """(y, x) mm from the top-left mark, as a move takes them: no cut bias, which is the blade's."""
    return pt.y - frame.top, pt.x - frame.inset


@dataclass(frozen=True)
class Check:
    """One reading a cut takes: where (page mm), what it should see, and, for a leg's centre line,
    which way to nudge (dx, dy per mm) when the centre itself isn't ink."""

    mark: str
    what: str
    x: float
    y: float
    ink: bool
    nudge: tuple[float, float] | None = None

    def reads(self, level: int | None) -> bool | None:
        """True ink, False paper, None neither (a reading between the two, or none)."""
        if level is None:
            return None
        return True if level >= INK else False if level <= PAPER else None


def placement(frame: Frame) -> Check:
    """The square's middle, read before the scan in the loaded mat's own frame. The scan start is
    given in that frame as page millimetres too (``driver.default_start``), so a sheet placed where
    the marks expect it has ink here; within about 2 mm of it, the scan's own window."""
    return Check(
        "top-left", "the square, before the scan", frame.inset + SQUARE / 2, frame.top + SQUARE / 2, True
    )


def checks(frame: Frame) -> list[Check]:
    """The readings a cut takes after the scan, page mm: the square's middle and a paper point
    OUTSIDE_SQUARE beyond each of its edges, then each L leg's centre line halfway along it and a
    paper point OUTSIDE_LEG either side."""
    x0, y0 = frame.inset, frame.top
    mid, far = SQUARE / 2, SQUARE + OUTSIDE_SQUARE
    out = [
        Check("top-left", "the square", x0 + mid, y0 + mid, True),
        Check("top-left", "left of the square", x0 - OUTSIDE_SQUARE, y0 + mid, False),
        Check("top-left", "right of the square", x0 + far, y0 + mid, False),
        Check("top-left", "above the square", x0 + mid, y0 - OUTSIDE_SQUARE, False),
        Check("top-left", "below the square", x0 + mid, y0 + far, False),
    ]
    half, d = frame.length / 2, OUTSIDE_LEG
    for name, (i, j), (sx, sy) in L_MARKS:
        cx, cy = frame.inset + i * frame.width, frame.top + j * frame.height
        vy, hx = cy + sy * half, cx + sx * half
        out += [
            Check(name, "the vertical leg", cx, vy, True, nudge=(1, 0)),
            Check(name, "left of the vertical leg", cx - d, vy, False),
            Check(name, "right of the vertical leg", cx + d, vy, False),
            Check(name, "the horizontal leg", hx, cy, True, nudge=(0, 1)),
            Check(name, "above the horizontal leg", hx, cy - d, False),
            Check(name, "below the horizontal leg", hx, cy + d, False),
        ]
    return out
