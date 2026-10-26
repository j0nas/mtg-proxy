"""Proof targets: a registration check read by eye between the scan and the real cut.

The machine never reports where it found the marks: its firmware keeps them to itself (FQ5 reads
-64 after every scan). So after the scan, before anything else is cut, the blade traces the edge
of a proof target: a small filled black square printed with the marks, inside the mark frame and
clear of the marks and the job. Registered right, the cut runs along the edge of the black all
round. Registered off, it leaves a strip of white between the black and the cut on one side and
runs inside the black on the other: an error in x shows on the left and right edges, one in y on
the top and bottom, each with its direction, and the strip's width is the size of the error. The
operator answers y (cut the job), r (scan again; the next round cuts the next target) or q (stop).

A square traced along its edge is the easiest shape to judge by eye: the question at each edge is
whether white shows between the black and the cut, and one look at a square answers it for both
axes. A plus scored along its arms' centre lines asks whether a groove runs down the middle of a
1 mm bar, which is harder to judge, and a groove inside black barely shows on gloss. At 3 mm a
target leaves more than a millimetre of blank paper either side of it in the 5.5 mm strip a proxy
sheet has between its marks' row and its cards, and its four 3 mm edges are long enough to see a
quarter millimetre of white along.

Until 2026-10-26 the proof cuts went on the marks themselves: the square's outline and each L
along its centre lines, at the job's own blade. Every round left grooves on and around the marks
the next scan read, and after three rounds on one deck box sheet the firmware's area search, which
looks for one 3.5-9 mm dark run, no longer found the square: the sheet could not be registered
again. A target is cut once: each round takes the next one, so the marks stay as printed and no
round cuts on an earlier round's groove. A sheet carries four, for three rounds in a run
(``ROUNDS``) and a later run on the same sheet (``cut-proxies --proof-from``).

The targets' places come from what printed them: layouts.json for the proxy sheets
(``layout.build_cut_svg``), ``--proof-target`` for any other sheet (the paper deck box's job.ts).
A target whose cut would touch the job is left out. The cut is one light pass (``BLADE``): enough
to see on laminate, nowhere near through. The job's own blade is set again before the job
(``driver.prove``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .geometry import Point, Polyline, inside
from .protocol import Blade

if TYPE_CHECKING:
    from .session import Frame

SIZE = 3.0  # mm: a proof target's side, unless its sheet says otherwise
BLADE = Blade(force=10, speed=5, depth=2)  # one pass that scores laminate without cutting through
ROUNDS = 3  # proof rounds in one run; the last one offers no rescan
CLEARANCE = 0.5  # mm a cut keeps from every job path

# The marks' shape, which the sensor check (sensing) and the scan simulation (simulate) read.
L_MARKS = (  # name, which frame corner, the legs' directions (x, y) from it
    ("top-right", (1, 0), (-1, 1)),
    ("bottom-left", (0, 1), (1, -1)),
)
SQUARE = 5.0  # mm: the card-maker's 3-mark square, filled, no outline (engine fork cd4166a)


def crossing(frame: Frame) -> float:
    """Where the sensor crosses each L's legs, in mm from its corner: half the leg length the
    machine keeps (TB51 takes whole mm, 5..20)."""
    return min(20, max(5, math.floor(frame.length))) / 2


@dataclass(frozen=True)
class Target:
    """One printed proof target: its centre (page mm, y down), its side, and its number on the
    sheet, 1 for the first round's."""

    x: float
    y: float
    size: float = SIZE
    number: int = 1

    def outline(self) -> Polyline:
        """The square's edge, closed, clockwise from its top-left corner."""
        h = self.size / 2
        x0, y0, x1, y1 = self.x - h, self.y - h, self.x + h, self.y + h
        return Polyline(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)))

    def name(self, of: int) -> str:
        return f"target {self.number} of {of} ({self.x:g}, {self.y:g} mm)"


def targets(centres: Iterable[tuple[float, float]], size: float = SIZE) -> tuple[Target, ...]:
    """A sheet's targets from their centres, numbered in the order given."""
    return tuple(Target(x, y, size, n) for n, (x, y) in enumerate(centres, 1))


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


def clear_of(sheet: Iterable[Target], shapes: list[Polyline]) -> tuple[list[Target], list[Target]]:
    """The targets whose outline keeps clear of the job, in order, and those that would touch it."""
    kept: list[Target] = []
    touching: list[Target] = []
    for t in sheet:
        (touching if on_job(t.outline(), shapes) else kept).append(t)
    return kept, touching
