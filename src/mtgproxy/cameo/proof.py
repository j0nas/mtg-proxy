"""Proof cuts: a registration check read by eye between the scan and the real cut.

The machine never reports where it found the marks: its firmware keeps them to itself (FQ5 reads
-64 after every scan). So after the scan we score every mark's own L along its centre lines, from
the corner to each leg's inked end. Registered right, every cut runs down the middle of the black;
a misread corner's cuts sit beside its lines, by the size of the misread, and a cut that stops
short of or overruns a leg's end shows the error along that leg. The operator answers y (cut the
job), r (scan again; the new cuts go on the same marks) or q (stop).

A rescan reads the same marks, and a scored line inside the black would sit exactly where the
sensor looks for the darkest point. So each leg is left uncut for EXCLUDE either side of wherever
the sensor crosses it (firmware V1.04, docs in cameo-re): half the machine's leg length along
every leg (it keeps whole millimetres), plus, on the top-left mark, the first search's line down
from the scan start and the 4 mm step it then takes down the vertical leg. What stays is an L at
each corner and a piece at each leg's end. A piece that would touch the job is left out.
"""

from __future__ import annotations

import math

from .geometry import Point, Polyline, inside
from .session import Frame

EXCLUDE = 1.5  # mm left uncut either side of a sensor crossing
MIN_PIECE = 1.0  # mm; shorter pieces are left out
FIRST_STEP = 4.0  # mm the first search steps down the top-left vertical leg before its X scan
ROUNDS = 3  # proof rounds; the last one offers no rescan
CLEARANCE = 0.5  # mm a cut keeps from every job path

CORNERS = (  # name, which frame corner, the legs' directions (x, y) from it
    ("top-left", (0, 0), (1, 1)),
    ("top-right", (1, 0), (-1, 1)),
    ("bottom-right", (1, 1), (-1, -1)),
    ("bottom-left", (0, 1), (1, -1)),
)


def crossings(frame: Frame, start: tuple[float, float]) -> dict[tuple[str, str], list[float]]:
    """Where the sensor crosses each leg, in mm from its corner: {(corner, "h" | "v"): [...]}."""
    half = min(20, max(5, math.floor(frame.length))) / 2  # TB51 keeps whole mm, 5..20
    out = {(name, leg): [half] for name, _, _ in CORNERS for leg in ("h", "v")}
    out[("top-left", "h")].append(start[1] - frame.inset)  # the first search, down from the start
    out[("top-left", "v")].append(FIRST_STEP)
    return out


def pieces(length: float, cross: list[float]) -> list[tuple[float, float]]:
    """[0, length] less EXCLUDE either side of every crossing, as (from, to) mm from the corner."""
    cuts = [(0.0, length)]
    for c in cross:
        lo, hi = c - EXCLUDE, c + EXCLUDE
        cuts = [part for a, b in cuts for part in ((a, min(b, lo)), (max(a, hi), b)) if part[1] > part[0]]
    return [(a, b) for a, b in cuts if b - a >= MIN_PIECE]


def cuts(frame: Frame, start: tuple[float, float]) -> list[tuple[str, Polyline]]:
    """(corner, cut) along every mark's centre lines, in page mm, the legs' pieces from
    ``pieces``; where both legs' first pieces reach the corner they join into one L."""
    cross = crossings(frame, start)
    out = []
    for name, (i, j), (sx, sy) in CORNERS:
        cx = frame.inset + i * frame.width
        cy = frame.inset + j * frame.height

        def at_h(d: float, cx=cx, cy=cy, sx=sx) -> Point:
            return (cx + sx * d, cy)

        def at_v(d: float, cx=cx, cy=cy, sy=sy) -> Point:
            return (cx, cy + sy * d)

        h = pieces(frame.length, cross[(name, "h")])
        v = pieces(frame.length, cross[(name, "v")])
        if h and v and h[0][0] == 0 and v[0][0] == 0:
            out.append((name, Polyline((at_h(h[0][1]), (cx, cy), at_v(v[0][1])))))
            h, v = h[1:], v[1:]
        out += [(name, Polyline((at_h(a), at_h(b)))) for a, b in h]
        out += [(name, Polyline((at_v(a), at_v(b)))) for a, b in v]
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
