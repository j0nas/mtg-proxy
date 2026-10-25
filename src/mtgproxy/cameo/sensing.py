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
not to): ink in the middle of the square and on each L leg's centre line, paper just outside the
square. A frame off by about half a millimetre or more fails it. A leg needs no paper reading: it
is 1 mm wide, so its centre reads paper once the frame is off by about 0.4 mm across it, and
nothing else printed comes within 3 mm of it. The 5 mm square needs its four.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .proof import L_MARKS, SQUARE
from .session import Frame

SQUARE_STEP = 0.5  # mm between readings across the 5 mm square
SQUARE_REACH = 2.5  # mm read past each of its edges
LEG_STEP = 0.25  # mm between readings across a 1 mm L leg
LEG_REACH = 2.0  # mm read either side of its centre line

INK = 250  # at or above: ink. Ink read 255 on 2026-10-25 (253 where the spot was half on it)
PAPER = 245  # at or below: paper. Paper read 151-242, 242 only half a mm from a leg
OUTSIDE_SQUARE = 1.25  # mm from the square's edge to its paper checks (it read 0.5 mm wide each side)
NUDGE = 0.25  # mm either side of a leg's centre line, read when the centre itself isn't ink
# A leg that reads no ink at all still passes as a faint one when it is a narrow dark line: CONTRAST
# above the paper SIDE mm away on both sides. On double-sided glossy (2026-10-26) a correctly placed
# leg peaked at 239 with paper at 91-132 beside it, while a scan latched onto the edge of a shadow
# read dark on one side only.
CONTRAST = 40
SIDE = 1.25


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

    def nudged(self, k: float) -> Check:
        """This check moved ``k`` mm along its nudge direction (a leg's crossing)."""
        assert self.nudge is not None
        return replace(self, x=self.x + k * self.nudge[0], y=self.y + k * self.nudge[1])

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
    OUTSIDE_SQUARE beyond each of its edges, then each L leg's centre line halfway along it."""
    x0, y0 = frame.inset, frame.top
    mid, far = SQUARE / 2, SQUARE + OUTSIDE_SQUARE
    out = [
        Check("top-left", "the square", x0 + mid, y0 + mid, True),
        Check("top-left", "left of the square", x0 - OUTSIDE_SQUARE, y0 + mid, False),
        Check("top-left", "right of the square", x0 + far, y0 + mid, False),
        Check("top-left", "above the square", x0 + mid, y0 - OUTSIDE_SQUARE, False),
        Check("top-left", "below the square", x0 + mid, y0 + far, False),
    ]
    half = frame.length / 2
    for name, (i, j), (sx, sy) in L_MARKS:
        cx, cy = frame.inset + i * frame.width, frame.top + j * frame.height
        out += [
            Check(name, "the vertical leg", cx, cy + sy * half, True, nudge=(1, 0)),
            Check(name, "the horizontal leg", cx + sx * half, cy, True, nudge=(0, 1)),
        ]
    return out


# --- diagnose: rows across every mark before any scan (cut-proxies --diagnose) -------------------

DIAG_STEP = 0.5  # mm between readings
DIAG_SQUARE = (-2.5, SQUARE + 2.5)  # mm across the square from its near edge: its whole width at 2 mm off
DIAG_LEG = 2.0  # mm read either side of where a leg should be, after the square's shift


@dataclass(frozen=True)
class Row:
    """Readings across one mark in the loaded mat's frame (page mm, as the scan start is given).
    Each point's offset is from where a correctly placed sheet has the mark's near edge (square) or
    centre line (leg); ``middle`` is the offset of a correctly placed mark's middle."""

    mark: str
    what: str
    axis: str
    points: tuple[Point, ...]
    middle: float


def square_rows(frame: Frame) -> list[Row]:
    """The square across x and across y through its middle: where the sheet sits."""
    x0, y0, mid = frame.inset, frame.top, SQUARE / 2
    sq = _steps(*DIAG_SQUARE, DIAG_STEP)
    return [
        Row("square", "across x", "x", tuple(Point("square across x", x0 + d, y0 + mid, d) for d in sq), mid),
        Row("square", "across y", "y", tuple(Point("square across y", x0 + mid, y0 + d, d) for d in sq), mid),
    ]


def leg_rows(frame: Frame, dx: float = 0.0, dy: float = 0.0) -> list[Row]:
    """Each L leg across its centre line halfway along it, centred on where it sits if the whole
    sheet moved as the square did (``dx``, ``dy``), so a misplaced sheet's legs stay in their rows."""
    half = frame.length / 2
    rows = []
    for name, (i, j), (sx, sy) in L_MARKS:
        cx, cy = frame.inset + i * frame.width, frame.top + j * frame.height
        xs = [round(dx + d, 4) for d in _steps(-DIAG_LEG, DIAG_LEG, DIAG_STEP)]
        ys = [round(dy + d, 4) for d in _steps(-DIAG_LEG, DIAG_LEG, DIAG_STEP)]
        v = tuple(Point(f"{name} vertical leg", cx + o, cy + dy + sy * half, o) for o in xs)
        h = tuple(Point(f"{name} horizontal leg", cx + dx + sx * half, cy + o, o) for o in ys)
        rows += [
            Row(f"{name} L", "vertical leg", "x", v, 0.0),
            Row(f"{name} L", "horizontal leg", "y", h, 0.0),
        ]
    return rows


@dataclass(frozen=True)
class Seen:
    """What one row shows: the mark's peak and the paper's median and range beside it, and how far
    the mark's middle sits from a correctly placed one's (mm, + right or down; None: no mark found
    whole within the row)."""

    row: Row
    levels: tuple[int | None, ...]
    peak: int | None
    paper: int | None
    paper_range: tuple[int, int] | None
    shift: float | None


def see(row: Row, levels: list[int | None]) -> Seen:
    """The mark is the run of readings around the darkest one; each of its edges is where the
    readings fall below halfway to the paper on that side (the median of the row's last three
    readings there: on a good sheet the square had paper at 165 on its left and 232 on its right).
    Its middle is that run's middle; a run that reaches a row's end has none."""
    vals = [(p.offset, v) for p, v in zip(row.points, levels, strict=True) if v is not None]
    if len(vals) < 3:
        return Seen(row, tuple(levels), None, None, None, None)
    k = max(range(len(vals)), key=lambda i: vals[i][1])
    peak = vals[k][1]
    left = sorted(v for _, v in vals[:3])[1]
    right = sorted(v for _, v in vals[-3:])[1]
    if peak - min(left, right) < CONTRAST:  # nothing in this row stands out: no mark
        ranked = sorted(v for _, v in vals)
        return Seen(row, tuple(levels), peak, ranked[len(ranked) // 2], (ranked[0], ranked[-1]), None)
    lo = hi = k
    while lo > 0 and vals[lo - 1][1] >= (peak + left) / 2:
        lo -= 1
    while hi < len(vals) - 1 and vals[hi + 1][1] >= (peak + right) / 2:
        hi += 1
    away = sorted(v for i, (_, v) in enumerate(vals) if i < lo - 1 or i > hi + 1) or [min(left, right)]
    whole = lo > 0 and hi < len(vals) - 1
    shift = round((vals[lo][0] + vals[hi][0]) / 2 - row.middle, 2) if whole else None
    return Seen(row, tuple(levels), peak, away[len(away) // 2], (away[0], away[-1]), shift)


def verdict(seen: list[Seen], frame: Frame) -> list[str]:
    """Plain lines: where the sheet sits, how it is turned and stretched, and which marks will be
    hard for the scan."""
    by = {(s.row.mark, s.row.what): s for s in seen}
    out = []

    def shift(mark: str, what: str) -> float | None:
        s = by.get((mark, what))
        return None if s is None else s.shift

    sx, sy = shift("square", "across x"), shift("square", "across y")
    if sx is not None and sy is not None:
        side = "right" if sx >= 0 else "left"
        down = "down" if sy >= 0 else "up"
        out.append(
            f"the sheet sits {abs(sx):.2f} mm {side} and {abs(sy):.2f} mm {down} of where its marks should be"
        )
        if max(abs(sx), abs(sy)) > 1.5:
            out.append(
                "  that is more than the scan likes: put the paper's corner (not the laminate's) on the grid's"
            )
    blx, try_ = shift("bottom-left L", "vertical leg"), shift("top-right L", "horizontal leg")

    def turned(rad: float, by: str) -> str:  # page y runs down: turned clockwise moves the right side down
        deg = math.degrees(rad)
        if abs(deg) < 0.005:
            return f"not turned, by {by}"
        return f"turned {abs(deg):.2f}° {'clockwise' if deg > 0 else 'anticlockwise'} by {by}"

    if sx is not None and blx is not None:
        out.append(turned(math.atan2(-(blx - sx), frame.height), "the left edge (bottom-left L vs square)"))
    if sy is not None and try_ is not None:
        out.append(turned(math.atan2(try_ - sy, frame.width), "the top edge (top-right L vs square)"))
    trx, bly = shift("top-right L", "vertical leg"), shift("bottom-left L", "horizontal leg")
    if sx is not None and trx is not None:
        out.append(f"square to top-right L: {frame.width + trx - sx:.2f} mm across (printed {frame.width:g})")
    if sy is not None and bly is not None:
        out.append(
            f"square to bottom-left L: {frame.height + bly - sy:.2f} mm down (printed {frame.height:g})"
        )
    for s in seen:
        name = f"{s.row.mark}, {s.row.what}"
        if s.shift is None:
            out.append(f"{name}: no clear mark within the row: it may sit further off, or not read at all")
        elif s.peak is not None and s.peak < INK:
            out.append(
                f"{name}: never reads as full ink (peak {s.peak}, paper {s.paper}): hard for the scan to find"
            )
        if s.paper is not None and s.paper < 135:
            out.append(
                f"{name}: the paper reads unusually bright ({s.paper}): glare, a lifted sheet, or no print behind it"
            )
    return out
