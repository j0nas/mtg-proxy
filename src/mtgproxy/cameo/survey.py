"""Registering a sheet with the sensor alone: find each mark where it lies, fit the page to them,
and cut through that fit instead of the scan's frame.

The scan (TB123) finds the square by an area search, then walks two fixed passes from it: down
to the bottom-left L half a leg right of its upright leg, and across to the top-right L half a
leg below its level leg, each starting 10 mm short of the L (cameo-re NOTES.md). On a 3x3 sheet
those legs are 6 mm, so the passes cross them 3 mm from their corners, and a sheet turned on the
mat moves the far Ls sideways off the passes: about 0.4° is all the scan takes either way. The
passes also ran 2 mm beside the bottom-left and top-right cards' print, which the simulator says a
sheet turned 0.2-0.45° clockwise brings onto the bottom-left pass (``driver.run_ups`` now starts
them past it). A laminated sheet goes on the mat by its pouch, which has to reach the tack, and the
paper inside the pouch is as crooked as it lies. All of this is from the simulator and the
firmware, not an observed failure: the misreads of 2026-10-31 that prompted it came from sheets
printed with another layout (docs/lessons.md).

The sensor (TB63) reads the one spot under the head wherever it is sent, so here each mark is
looked for where it actually is: the square by its right and bottom edges and, where the paper
beside them reads clean, its left and top ones; each L's legs by both edges of each, halfway along
the leg. Edges are pinned by halving between ink and blank paper and then
interpolating between the last two readings; a line's centre is the middle of its two edges, so
the ink's spread and the spot's blur cancel. The bottom-left L's upright leg is looked for up to
TILT_REACH either side (about 1.2° of turn), and it says how the sheet is turned, which places
the other legs' searches. A reading takes about 2.5 s on paper and 4.5 s on ink, so a survey is
about 30 readings and 100 s, against the scan's 12 s: a cut falls back to it when the scan finds
nothing, or a frame the print disagrees with (``driver.cut``).

Every reading is in the machine's frame as it stands, and so is the fit: whatever a scan left
installed, the cut goes through the same frame the readings did. A line that isn't a mark (a
pouch's dark band, a card's print, glare) shows as the wrong width and is passed over; the fit is
refused unless the page's two edges come out square to each other and the marks as far apart as
printed, within what a printer and a pouch do to a sheet.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from . import proof, sensing
from .session import CutError, Fit, Frame, SessionLog

# A page point (x, y) mm → (y, x) mm as a move takes it, where the mark is expected (roughly).
Where = Callable[[float, float], tuple[float, float]]
# (y, x) mm as a move takes it → the level TB63 reads there (None: no reading).
Read = Callable[[float, float], int | None]

STEP = 0.75  # mm between search readings: a 1 mm leg reads as ink within about 0.4 mm of its centre
# A search gives up once this many readings have none that reads as paper: on 2026-11-01 a sheet
# whose bottom-left corner didn't lie flat read 221-255 all round its L, for 4.5 minutes of searching.
BLIND = 5
TILT_REACH = 6.0  # mm either side the bottom-left L's upright leg is looked for: about 1.2° of turn
REACH = 2.5  # mm either side the top-right L's level leg is looked for, once the turn is known
LOST_REACH = 7.0  # mm either side an L's other leg is looked for when the first isn't where expected
LEG_EDGE = 1.75  # mm from an ink reading on a leg to where blank paper is taken to lie, either side
LEG_HALVINGS = 2  # each edge of a leg to 0.44 mm, then interpolated
SQUARE_EDGE = proof.SQUARE + 1.0  # mm from an ink reading in the square to blank paper past it
SQUARE_HALVINGS = 4  # the square's far edge to 0.38 mm, then interpolated
SQUARE_HUNT = 3.0  # mm between the readings that look for the square when its middle isn't ink
FAR_EDGE = 0.75  # mm either side of where the square's size puts its near edge
FAR_HALVINGS = 2
LEG_WIDTH = (0.4, 2.4)  # mm a leg may measure: 1 mm printed, blurred by the spot either side
SQUARENESS = 0.4  # degrees the page's left edge may differ from square to its top edge
STRETCH = 0.015  # the marks' spacing may differ from the print's by this fraction
# Those limits for readings taken through a frame a scan misread: they see the page through that
# frame's own unknown shear and stretch, which a misread of up to 10 mm makes 2.8° and 3.6 %.
THROUGH_SCAN = (3.0, 0.05)


@dataclass(frozen=True)
class Line:
    """A mark's line found across one axis: its centre and width, mm, along that axis."""

    centre: float
    width: float


def hit(level: int | None, paper: int | None) -> bool:
    """A reading that stands out as ink against ``paper``: ink by ``sensing.thresholds``, or a
    narrow line's blur at least CONTRAST above the paper."""
    if level is None:
        return False
    ink, _ = sensing.thresholds(paper)
    return level >= ink or (paper is not None and level >= paper + sensing.CONTRAST)


def edge(
    read: Callable[[float], int | None],
    ink_t: float,
    ink: int,
    paper_t: float,
    paper: int,
    cut: float,
    halvings: int,
) -> float | None:
    """Where the level crosses ``cut`` between ink reading ``ink`` at ``ink_t`` and paper taken to
    read ``paper`` at ``paper_t`` (not read): halved ``halvings`` times, then interpolated between
    the two readings either side. None when a reading fails, or none of them reads as paper: the
    ink runs on past ``paper_t``, so this is no edge of the line looked for."""
    li, lp, bounded = ink, paper, False
    for _ in range(halvings):
        mid = (ink_t + paper_t) / 2
        level = read(mid)
        if level is None:
            return None
        if level >= cut:
            ink_t, li = mid, level
        else:
            paper_t, lp, bounded = mid, level, True
    if not bounded:
        return None
    if li <= lp:
        return (ink_t + paper_t) / 2
    f = min(1.0, max(0.0, (li - cut) / (li - lp)))
    return ink_t + f * (paper_t - ink_t)


def line(
    read: Callable[[float], int | None],
    t: float,
    level: int,
    paper: int,
    reach: float,
    halvings: int,
) -> Line | None:
    """Both edges of the line that reads ``level`` at ``t``, each looked for within ``reach``, where
    the level crosses halfway from ``paper`` to full ink. A reading short of halfway is the line's
    blurred edge: the reading half a STEP to either side that passes it stands in, and with none,
    None. A side whose readings never reach paper gives the line the whole reach on that side and
    an infinite width, so it is passed over as too wide."""
    cut = (sensing.FULL + paper) / 2
    if level < cut:
        for t2 in (t - STEP / 2, t + STEP / 2):
            l2 = read(t2)
            if l2 is not None and l2 >= cut:
                t, level = t2, l2
                break
        else:
            return None
    lo = edge(read, t, level, t - reach, paper, cut, halvings)
    hi = edge(read, t, level, t + reach, paper, cut, halvings)
    if lo is None or hi is None:
        a, b = t - reach if lo is None else lo, t + reach if hi is None else hi
        return Line((a + b) / 2, math.inf)
    return Line((lo + hi) / 2, hi - lo)


def search(
    read: Callable[[float], int | None],
    guess: float,
    reach: float,
    paper: int | None,
    width: tuple[float, float],
    edge_reach: float,
    halvings: int,
) -> Line | None:
    """The first line of ``width`` within ``reach`` of ``guess``, readings STEP apart from the guess
    outwards, nearest first; a line of the wrong width is passed over."""
    n = math.floor(reach / STEP + 1e-9)
    order = [0.0, *(k * s * STEP for k in range(1, n + 1) for s in (1, -1))]
    passed: list[tuple[float, float]] = []
    base = sensing.PAPER if paper is None else paper
    cut = (sensing.FULL + base) / 2
    levels: list[int | None] = []

    def tracked(t: float) -> int | None:
        level = read(t)
        levels.append(level)
        return level

    for off in order:
        if len(levels) >= BLIND and not any(v is not None and v < cut for v in levels):
            return None  # nothing here reads as paper: no line can be told from it
        t = guess + off
        if any(a <= t <= b for a, b in passed):
            continue
        level = tracked(t)
        if not hit(level, paper):
            continue
        assert level is not None
        found = line(tracked, t, level, min(base, level - sensing.CONTRAST), edge_reach, halvings)
        if found is None:
            continue
        if width[0] <= found.width <= width[1]:
            return found
        half = edge_reach if math.isinf(found.width) else found.width / 2
        passed.append((found.centre - half, found.centre + half))
    return None


def _cross(p: tuple[float, float], d: tuple[float, float], q: tuple[float, float], e: tuple[float, float]):
    """Where the line through ``p`` along ``d`` meets the line through ``q`` along ``e``."""
    den = d[0] * e[1] - d[1] * e[0]
    s = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return p[0] + s * d[0], p[1] + s * d[1]


def _unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def fit_lines(
    frame: Frame,
    left_edge: tuple[float, float],
    top_edge: tuple[float, float],
    upright_bl: tuple[float, float],
    level_bl: tuple[float, float],
    level_tr: tuple[float, float],
    upright_tr: tuple[float, float],
) -> Fit:
    """The page's fit from points (x, y, machine mm) on the marks' lines, as the firmware builds its
    frame (cameo-re NOTES.md, "Origin and model"): the page's left edge runs through the square's
    left edge and the bottom-left L's upright leg, its top edge through the square's top edge and
    the top-right L's level leg; the far corners are where the other two legs meet those."""
    down = _unit(left_edge, upright_bl)
    across = _unit(top_edge, level_tr)
    origin = _cross(left_edge, down, top_edge, across)
    bl = _cross(left_edge, down, level_bl, across)
    tr = _cross(top_edge, across, upright_tr, down)
    w, h = frame.width, frame.height
    return Fit(
        (frame.inset, frame.top),
        origin,
        ((tr[0] - origin[0]) / w, (tr[1] - origin[1]) / w),
        ((bl[0] - origin[0]) / h, (bl[1] - origin[1]) / h),
    )


def judge(
    fit: Fit, scale: tuple[float, float] = (1.0, 1.0), limits: tuple[float, float] = (SQUARENESS, STRETCH)
) -> str | None:
    """Why the fit can't be the sheet's: its edges not square to each other, or the marks not as
    far apart as printed, ``scale`` (across, down) being what a printed mm measures in the frame the
    readings were in. ``limits``: (degrees off square, fraction of stretch). None when it can be."""
    (ax, ay), (dx, dy) = fit.across, fit.down
    off_square = 90 - math.degrees(math.atan2(ax * dy - ay * dx, ax * dx + ay * dy))
    if abs(off_square) > limits[0]:
        return f"its left edge comes out {abs(off_square):.2f}° off square to its top edge"
    for name, (vx, vy), k in (("across", fit.across, scale[0]), ("down", fit.down, scale[1])):
        stretch = math.hypot(vx, vy) / k - 1
        if abs(stretch) > limits[1]:
            more = "further apart" if stretch > 0 else "closer"
            return f"the marks come out {abs(stretch) * 100:.1f} % {more} {name} than printed"
    return None


def square_side(read: Callable[[float], int | None], t: float, level: int, paper: int) -> float | None:
    """The square's middle along one axis, from ``t`` inside it: its far edge (right, or bottom),
    past which only blank paper lies on every sheet, then its near one within FAR_EDGE of where
    the 5 mm square puts it. Past the near edge lies the paper's own edge, which a pouch reads as
    dark as ink, as close as 3.5 mm on the first 3x3 layout: when no reading there comes out
    paper, the near edge is taken from the square's size. None when the far edge isn't found
    (the ink runs on: a pouch's band, a card), or the near one isn't where the size says."""
    cut = (sensing.FULL + paper) / 2
    if level < cut:
        return None
    far = edge(read, t, level, t + SQUARE_EDGE, paper, cut, SQUARE_HALVINGS)
    if far is None:
        return None
    guess = far - proof.SQUARE
    near = edge(read, guess + FAR_EDGE, level, guess - FAR_EDGE, paper, cut, FAR_HALVINGS)
    return (guess + far) / 2 if near is None else (near + far) / 2


def survey(
    frame: Frame,
    read_at: Read,
    where: Where,
    log: SessionLog,
    paper: Callable[[str], int | None] = lambda mark: None,
    limits: tuple[float, float] = (SQUARENESS, STRETCH),
) -> Fit:
    """Find the square and both edges of each L's legs with the sensor, starting where ``where``
    expects each, and fit the page to them. ``paper(mark)`` is blank paper's level beside that mark
    (``sensing.Flatness.paper_at``). Raises CutError naming the mark it couldn't find, or the fit's
    flaw (``judge``)."""
    count = [0]

    def at(mark: str, what: str, axis: str, fixed: float) -> Callable[[float], int | None]:
        def read(t: float) -> int | None:
            y, x = (fixed, t) if axis == "x" else (t, fixed)
            level = read_at(y, x)
            count[0] += 1
            log("survey", mark=mark, what=what, x=round(x, 3), y=round(y, 3), level=level)
            return level

        return read

    def missing(what: str, where_: tuple[float, float], reach: float) -> CutError:
        y, x = where_
        return CutError(
            f"the sensor found no {what} within {reach:g} mm of {y:.1f} mm down, {x:.1f} mm in, where the "
            "other marks put it: nothing was cut. Check that mark is clean and the sheet lies flat there, "
            "then rerun"
        )

    # The square: its middle where expected, else a grid round it; both edges across, then down.
    x0, y0 = frame.inset, frame.top
    half = proof.SQUARE / 2
    gy, gx = where(x0 + half, y0 + half)
    sq_paper = paper("top-left")
    found = None
    for dx, dy in sorted(
        ((a * SQUARE_HUNT, b * SQUARE_HUNT) for a in (-1, 0, 1) for b in (-1, 0, 1)),
        key=lambda d: (math.hypot(*d), d[1], d[0]),
    ):
        hy, hx = gy + dy, gx + dx
        level = at("top-left", "the square", "x", hy)(hx)
        if not hit(level, sq_paper):
            continue
        assert level is not None
        base = sensing.PAPER if sq_paper is None else min(sq_paper, level - sensing.CONTRAST)
        across = square_side(at("top-left", "the square across", "x", hy), hx, level, base)
        if across is None:
            continue
        down = square_side(at("top-left", "the square down", "y", across), hy, level, base)
        if down is None:
            continue
        found = (across, hy, down)
        break
    if found is None:
        raise missing("square", (gy, gx), SQUARE_HUNT + half)
    cx, row, cy = found
    left_edge, top_edge = (cx - half, row), (cx, cy - half)

    # Where the rest should be: ``where`` moved onto the square, then turned about it.
    shift = (cx - gx, cy - gy)
    turn = [0.0]

    def expect(px: float, py: float) -> tuple[float, float]:
        y, x = where(px, py)
        x, y = x + shift[0], y + shift[1]
        c, s = math.cos(turn[0]), math.sin(turn[0])
        u, v = x - cx, y - cy
        return cy + u * s + v * c, cx + u * c - v * s

    def upright(mark: str, row: float, x: float, reach: float) -> tuple[float, float] | None:
        got = search(
            at(mark, "upright leg", "x", row), x, reach, paper(mark), LEG_WIDTH, LEG_EDGE, LEG_HALVINGS
        )
        return None if got is None else (got.centre, row)

    def level(mark: str, col: float, y: float, reach: float) -> tuple[float, float] | None:
        got = search(
            at(mark, "level leg", "y", col), y, reach, paper(mark), LEG_WIDTH, LEG_EDGE, LEG_HALVINGS
        )
        return None if got is None else (col, got.centre)

    along = frame.length / 2

    def ell(mark: str, upright_first: bool, reach: float) -> tuple[tuple[float, float], tuple[float, float]]:
        """The L's (upright, level) leg points. One leg is looked for halfway along it where expected,
        ``reach`` either side, and missing there, the other, LOST_REACH either side. The leg found
        puts the other within its own length: the other's column or row, and the corner at one end."""
        _, (i, j), (sx, sy) = next(m for m in proof.L_MARKS if m[0] == mark)
        cx_, cy_ = x0 + i * frame.width, y0 + j * frame.height
        uy, ux = expect(cx_, cy_ + sy * along)
        ly, lx = expect(cx_ + sx * along, cy_)
        within = along + 0.5
        for first in (upright_first, not upright_first):
            r = reach if first == upright_first else LOST_REACH
            if first:
                up = upright(mark, uy, ux, r)
                if up is None:
                    continue
                col, row = up[0] + sx * along, up[1] - sy * along
                lv = level(mark, col, row, within)
                if lv is None:
                    raise missing(f"{mark} L's level leg", (row, col), within)
                return up, lv
            lv = level(mark, lx, ly, r)
            if lv is None:
                continue
            row, col = lv[1] + sy * along, lv[0] - sx * along
            up = upright(mark, row, col, within)
            if up is None:
                raise missing(f"{mark} L's upright leg", (row, col), within)
            return up, lv
        raise missing(f"{mark} L", (uy, ux), LOST_REACH)

    ey, ex = expect(x0, y0 + frame.height - along)
    upright_bl, level_bl = ell("bottom-left", True, TILT_REACH)
    turn[0] = (ex - upright_bl[0]) / (ey - cy)  # found left of where expected: turned clockwise
    upright_tr, level_tr = ell("top-right", False, REACH)

    fit = fit_lines(frame, left_edge, top_edge, upright_bl, level_bl, level_tr, upright_tr)
    log(
        "surveyed",
        readings=count[0],
        square=[round(cx, 3), round(cy, 3)],
        upright_bl=[round(v, 3) for v in upright_bl],
        level_bl=[round(v, 3) for v in level_bl],
        level_tr=[round(v, 3) for v in level_tr],
        upright_tr=[round(v, 3) for v in upright_tr],
        origin=[round(v, 3) for v in fit.origin],
        across=[round(v, 6) for v in fit.across],
        down=[round(v, 6) for v in fit.down],
        turn=round(fit.turn(), 3),
    )
    (y0_, x0_), (y1_, x1_), (y2_, x2_) = (
        where(x0, y0),
        where(x0 + frame.width, y0),
        where(x0, y0 + frame.height),
    )
    scale = (math.hypot(x1_ - x0_, y1_ - y0_) / frame.width, math.hypot(x2_ - x0_, y2_ - y0_) / frame.height)
    flaw = judge(fit, scale, limits)
    if flaw:
        raise CutError(
            f"the marks the sensor found don't fit together: {flaw}, so one of them was misread. Nothing "
            "was cut; cut-proxies --diagnose shows what the sensor sees at each mark"
        )
    return fit
