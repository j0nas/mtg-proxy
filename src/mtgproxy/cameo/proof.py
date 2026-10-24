"""Proof cuts: a registration check read by eye between the scan and the real cut.

The machine never reports where it found the marks: its firmware keeps them to itself (FQ5 reads
-64 after every scan). So after the scan we score every mark: the top-left square along its
outline, and the L marks top-right and bottom-left along their centre lines, from the corner to
each leg's inked end. Registered right, the outline traces the edge of the black and every L cut
runs down the middle of it; a misread mark's cuts sit beside it, by the size of the misread, and
a cut that stops short of or overruns a leg's end shows the error along that leg. The operator
answers y (cut the job), r (scan again; the new cuts go on the same marks) or q (stop).

A rescan reads the same marks, and a scored line inside the black would sit exactly where the
sensor looks for the darkest point. The machine locates the square by the centre of its dark
run, and a cut across the black could split that run below the 3.5 mm the search accepts, while
one along its edge leaves the run whole. It crosses each L half its leg length from the corner
(it keeps whole millimetres; firmware V1.04, docs in cameo-re), so each leg is left uncut for
EXCLUDE either side of that: what stays is an L at each corner and a piece at each leg's end. A
piece that would touch the job is left out.
"""

from __future__ import annotations

import math

from .geometry import Point, Polyline, inside
from .session import Frame

EXCLUDE = 1.5  # mm left uncut either side of a sensor crossing
MIN_PIECE = 1.0  # mm; shorter pieces are left out
ROUNDS = 3  # proof rounds; the last one offers no rescan
CLEARANCE = 0.5  # mm a cut keeps from every job path

L_MARKS = (  # name, which frame corner, the legs' directions (x, y) from it
    ("top-right", (1, 0), (-1, 1)),
    ("bottom-left", (0, 1), (1, -1)),
)
SQUARE = 5.0  # mm: the card-maker's 3-mark square, filled, no outline (engine fork cd4166a)


def square(frame: Frame) -> Polyline:
    """The 3-mark square's printed outline, page mm: SQUARE from the inset, so 10-15 mm at a
    10 mm inset. TB123 measures a 5 mm square; the 6 mm one the card-maker drew with a 1 mm
    stroke until cd4166a was found and never measured (2026-10-24)."""
    x0, y0 = frame.inset, frame.top
    x1, y1 = x0 + SQUARE, y0 + SQUARE
    return Polyline(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)))


def crossing(frame: Frame) -> float:
    """Where the sensor crosses each L's legs, in mm from its corner: half the leg length the
    machine keeps (TB51 takes whole mm, 5..20)."""
    return min(20, max(5, math.floor(frame.length))) / 2


def pieces(length: float, cross: float) -> list[tuple[float, float]]:
    """[0, length] less EXCLUDE either side of the crossing, as (from, to) mm from the corner."""
    parts = ((0.0, min(length, cross - EXCLUDE)), (max(0.0, cross + EXCLUDE), length))
    return [(a, b) for a, b in parts if b - a >= MIN_PIECE]


def cuts(frame: Frame) -> list[tuple[str, Polyline]]:
    """(corner, cut) in page mm: the square's outline first, then each L mark's legs along their
    centre lines in the pieces ``pieces`` leaves, the two pieces at the corner joined into one L."""
    out = [("top-left", square(frame))]
    for name, (i, j), (sx, sy) in L_MARKS:
        cx = frame.inset + i * frame.width
        cy = frame.top + j * frame.height

        def at_h(d: float, cx=cx, cy=cy, sx=sx) -> Point:
            return (cx + sx * d, cy)

        def at_v(d: float, cx=cx, cy=cy, sy=sy) -> Point:
            return (cx, cy + sy * d)

        legs = pieces(frame.length, crossing(frame))  # the same along both legs
        if legs and legs[0][0] == 0:
            out.append((name, Polyline((at_h(legs[0][1]), (cx, cy), at_v(legs[0][1])))))
            legs = legs[1:]
        out += [(name, Polyline((at_h(a), at_h(b)))) for a, b in legs]
        out += [(name, Polyline((at_v(a), at_v(b)))) for a, b in legs]
    return out


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _to_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """Between segments ab and cd (0 when they cross)."""
    d1, d2 = _cross(c, d, a), _cross(c, d, b)
    d3, d4 = _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4):
        return 0.0
    return min(_to_segment(a, c, d), _to_segment(b, c, d), _to_segment(c, a, b), _to_segment(d, a, b))


def on_job(cut: Polyline, shapes: list[Polyline]) -> bool:
    """Would this cut touch the job: a vertex or a segment's middle inside an odd number of
    closed outlines (a part, not its holes), or within CLEARANCE of any job path?"""
    segs = list(zip(cut.points, cut.points[1:], strict=False))
    probes = [*cut.points, *(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in segs)]
    closed = [s for s in shapes if s.closed]
    if any(sum(inside(p, s.points) for s in closed) % 2 for p in probes):
        return True
    x0, y0, x1, y1 = cut.bbox()
    x0, y0, x1, y1 = x0 - CLEARANCE, y0 - CLEARANCE, x1 + CLEARANCE, y1 + CLEARANCE
    for s in shapes:
        sx0, sy0, sx1, sy1 = s.bbox()
        if sx1 < x0 or sx0 > x1 or sy1 < y0 or sy0 > y1:
            continue
        for c, d in zip(s.points, s.points[1:], strict=False):
            if any(distance(a, b, c, d) < CLEARANCE for a, b in segs):
                return True
    return False
