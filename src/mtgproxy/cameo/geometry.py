"""Cut geometry: a page-sized SVG → polylines in page millimetres (y down), ordered for the blade.

Order: whatever lies inside a closed outline is cut before that outline (slits, holes and
perforations before the edge that frees the part, so the part never shifts while still being
cut), and within one nesting depth the nearest next start wins. Passes: a closed loop is cut as
continuous laps with a short overcut past its start, so the blade never lifts on the seam; an
open path (a perforation dash) is cut again from its start each pass.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

Point = tuple[float, float]

SAMPLE_MM = 0.25  # chord length when flattening arcs and curves
CLOSE_EPS = 1e-6
MM_PER_PX = 25.4 / 96  # svgelements works in CSS px at its default 96 ppi


@dataclass(frozen=True)
class Polyline:
    points: tuple[Point, ...]

    @property
    def closed(self) -> bool:
        return len(self.points) > 2 and _same(self.points[0], self.points[-1])

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def reversed(self) -> Polyline:
        return Polyline(tuple(reversed(self.points)))


def _same(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < CLOSE_EPS and abs(a[1] - b[1]) < CLOSE_EPS


def load_svg(path: Path) -> list[Polyline]:
    """Every visible shape's subpaths, flattened, in page mm (the SVG's own units, transforms applied)."""
    from svgelements import SVG, Arc, Close, CubicBezier, Line, Move, Path, QuadraticBezier, Shape

    svg = SVG.parse(str(path), reify=True)
    out: list[Polyline] = []
    for element in svg.elements():
        if not isinstance(element, Shape) or element.values.get("visibility") == "hidden":
            continue
        shape = element if isinstance(element, Path) else Path(element)
        subpaths: list[list[Point]] = [[]]
        for seg in shape.segments():
            current = subpaths[-1]
            if isinstance(seg, Move):
                subpaths.append([_mm(seg.end)])
            elif isinstance(seg, Close):
                if current and not _same(current[-1], current[0]):
                    current.append(current[0])
                subpaths.append([])
            elif isinstance(seg, Line):
                current.append(_mm(seg.end))
            elif isinstance(seg, Arc | CubicBezier | QuadraticBezier):
                n = max(2, math.ceil(seg.length(error=1e-4) * MM_PER_PX / SAMPLE_MM))
                current.extend(_mm(seg.point(i / n)) for i in range(1, n + 1))
        out.extend(Polyline(tuple(sp)) for sp in subpaths if len(sp) > 1)
    return out


def _mm(p) -> Point:
    # 0.1 µm: far below the machine's 50 µm step, and it drops the px round trip's float noise.
    return (round(float(p.x) * MM_PER_PX, 4), round(float(p.y) * MM_PER_PX, 4))


def _inside(p: Point, poly: tuple[Point, ...]) -> bool:
    """Even-odd point-in-polygon test."""
    x, y = p
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1], strict=True):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def depths(lines: list[Polyline]) -> list[int]:
    """How many closed outlines contain each polyline (tested at its midpoint vertex)."""
    closed = [(i, pl, pl.bbox()) for i, pl in enumerate(lines) if pl.closed]
    out = []
    for i, pl in enumerate(lines):
        p = pl.points[len(pl.points) // 2]
        x0, y0, x1, y1 = pl.bbox()
        out.append(
            sum(
                1
                for j, outer, (ox0, oy0, ox1, oy1) in closed
                if j != i and ox0 <= x0 and oy0 <= y0 and x1 <= ox1 and y1 <= oy1 and _inside(p, outer.points)
            )
        )
    return out


def order(lines: list[Polyline], start: Point = (0.0, 0.0)) -> list[Polyline]:
    """Deepest first; within a depth, greedy nearest start (open paths may run either way)."""
    d = depths(lines)
    pos, out = start, []
    for depth in sorted(set(d), reverse=True):
        todo = [pl for pl, k in zip(lines, d, strict=True) if k == depth]
        while todo:
            best = min(
                (
                    (math.dist(pos, cand.points[0]), i, cand)
                    for i, pl in enumerate(todo)
                    for cand in _entries(pl)
                ),
                key=lambda t: (t[0], t[1]),
            )
            _, i, chosen = best
            todo.pop(i)
            out.append(chosen)
            pos = chosen.points[-1]
    return out


def _entries(pl: Polyline) -> Iterable[Polyline]:
    yield pl
    if not pl.closed:
        yield pl.reversed()


def passes(lines: list[Polyline], n: int, overcut_mm: float = 0.5) -> list[Polyline]:
    """Each closed loop as n continuous laps plus ``overcut_mm`` past its start; open paths n times."""
    out: list[Polyline] = []
    for pl in lines:
        if pl.closed:
            laps = list(pl.points)
            for _ in range(1, n):
                laps.extend(pl.points[1:])
            laps.extend(_walk(pl.points, overcut_mm))
            out.append(Polyline(tuple(laps)))
        else:
            out.extend([pl] * n)
    return out


def _walk(points: tuple[Point, ...], dist: float) -> list[Point]:
    """The points reached walking ``dist`` mm along ``points`` from its start (not including it)."""
    out: list[Point] = []
    for a, b in pairwise(points):
        seg = math.dist(a, b)
        if seg >= dist:
            if seg > 0:
                t = dist / seg
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        dist -= seg
    return out


def bbox(lines: Iterable[Polyline]) -> tuple[float, float, float, float]:
    boxes = [pl.bbox() for pl in lines]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
