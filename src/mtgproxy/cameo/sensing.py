"""Reading the printed marks back with the machine's own sensor, after the scan.

The machine never says where it found the marks (cameo-re NOTES.md), so a misread only showed in
the cut: on 2026-10-25 one laminated 3x3 sheet registered "found" in a normal 11 s and cut shifted.
TB63 (protocol.sense) reads the light the spot under the head reflects. After the scan, a move goes
through the registered frame, so the head can be sent to where each mark should be and asked what
it sees: ink where the frame says a mark is, paper beside it. A frame that disagrees with the print
is a misread, found before the blade goes down.

``profiles`` is the first experiment: rows of readings across each mark's edges, to learn the
contrast through laminate, how sharp an edge reads and whether the readings land where the frame
says (the cut's 0.5 mm y offset may or may not apply to the sensor). The check a cut would run
comes from what they show.
"""

from __future__ import annotations

from dataclasses import dataclass

from .proof import L_MARKS, SQUARE
from .session import Frame

SQUARE_STEP = 0.5  # mm between readings across the 5 mm square
SQUARE_REACH = 2.5  # mm read past each of its edges
LEG_STEP = 0.25  # mm between readings across a 1 mm L leg
LEG_REACH = 2.0  # mm read either side of its centre line


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


def registered(frame: Frame, pt: Point) -> tuple[float, float]:
    """(y, x) mm from the top-left mark, as a move takes them: no cut bias, which is the blade's."""
    return pt.y - frame.top, pt.x - frame.inset
