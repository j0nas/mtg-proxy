"""Walk the Cameo's TB123 mark scan over a printed sheet before it is printed or cut.

The firmware's mark detector is relative (it fires when a reading drops below a fraction of the
running average of the last 16, cameo-re INVESTIGATION.md), so print near one of its passes can
make it latch onto the wrong thing. A scratch simulation of these passes over a sheet's image
flagged a failing 7 mm mark layout before it was printed (2026-10-24): the bottom-left search
began on the bottom-left card's corner, and on the machine it once read that.

The passes are cameo-re NOTES.md, "The three-mark sequence, TB123" (firmware V1.04):

1. Area search: 11 rows 3 mm apart from the scan start (top, left), even rows sweeping 30 mm to
   the right, odd rows back. A dark run 3.5-9.0 mm long ends the search.
2. Locating: the head backs up to (row - 7 mm, run centre + 1 mm), finds the dark run's centre
   along the feed, then sweeps along the carriage from 5 mm left of that centre. The square's
   centre less 2.5 mm becomes the top and left edge, the frame's top-left corner.
3. Bottom-left: from (top edge + h - 10 mm, left edge + half a leg) feed-wards across the
   horizontal leg, then half a leg up and carriage-wards (left) across the vertical leg.
4. Top-right: from (top edge + half a leg, left edge + w - 10 mm) carriage-wards across the
   vertical leg, then half a leg back and feed-wards (up) across the horizontal leg.

Half a leg is what TB51 keeps (whole mm, see ``proof.crossing``): 3 mm on a 3x3 sheet, 4.5 on a
4x2 one. The scratch script this came from started both crossing passes 2 mm past those points
and swept every pass out to the paper's edge. The firmware's passes start where the notes say and
stop at the first dark run, so this walks each pass only that far.

A pass fails when its first dark run is print that is not a mark, or when any such print comes
within ``SPOT`` mm of the line the sensor walks. The sensor reads a 1 mm leg about 2 mm wide
(``sensing.quiet``), so ``SPOT`` is a millimetre either side. The real 3x3 sheet's passes were
1.2-2.6 mm clear of the card print and registered four sheets in a row (2026-10-25).

Not modelled: the retry the firmware makes when a search fails (it starts w/3 or h/3 further on),
the paper's edge and the mat, and the sensor's gain. ``DARK`` stands in for what it calls black.
The renderer is pdfium, so vector PDFs (the deck box's) work as well as the engine's raster ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .cameo import driver, proof
from .cameo.session import Frame

PPI = 300  # render resolution; 0.085 mm a pixel
DARK = 128  # a grey level below this is black to the sensor
INK = 245  # below this there is print
SPOT = 1.0  # mm either side of a pass the sensor sees
REACH = 6.0  # mm: how far from a pass the nearest other print is looked for
MARK_PAD = 0.3  # mm: antialiasing around a mark's own ink is still the mark
ROWS, ROW_PITCH, SWEEP = 11, 3.0, 30.0  # the area search
RUN_MIN, RUN_MAX = 3.5, 9.0  # an area-search run the firmware accepts, mm
BACK_UP, DOWN, SIDE_BACK, SIDE_ON = (
    7.0,
    30.0,
    5.0,
    25.0,
)  # locating the square (the lengths past 'back up' are ours)
RUN_UP = 10.0  # the L marks' run-up before the corner line, from the corner
BEYOND = 6.0  # a pass past the corner line on its first crossing, mm


class SimulationError(Exception):
    pass


@dataclass
class Pass:
    name: str
    start: tuple[float, float]  # (x, y) page mm
    end: tuple[float, float]  # where the firmware's sweep would end if it crossed nothing
    runs: list[tuple[float, float]] = field(default_factory=list)  # dark runs walked, mm along the pass
    caught: str | None = None  # "mark", "other print" or None: what the first dark run was
    walked: tuple[float, float] = (0.0, 0.0)  # mm along the pass the head got to
    clearance: float | None = None  # nearest non-mark print to the walked line; None beyond REACH
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.caught == "mark" and (self.clearance is None or self.clearance >= SPOT)


@dataclass
class Report:
    passes: list[Pass]
    frame: Frame
    start: tuple[float, float]

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.passes)

    def lines(self) -> list[str]:
        out = []
        for p in self.passes:
            runs = " ".join(f"{a:.1f}-{b:.1f}" for a, b in p.runs) or "none"
            near = "-" if p.clearance is None else f"{p.clearance:.2f} mm"
            tag = "ok  " if p.ok else "FAIL"
            out.append(f"{tag} {p.name:<26} runs {runs:<14} print {near:>8}  {p.note}".rstrip())
        out.append(self.verdict())
        return out

    def verdict(self) -> str:
        bad = [p for p in self.passes if not p.ok]
        if not bad:
            return f"clean: every pass is on its mark with print at least {SPOT:g} mm clear"
        names = ", ".join(p.name for p in bad)
        return f"FAIL: print within {SPOT:g} mm of a pass, or latched on, at: {names}"


def render(pdf: Path, page: int, frame: Frame) -> np.ndarray:
    """The page as grey levels at ``PPI``, turned upright to the frame's orientation (the engine saves
    every 3x3 page landscape)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        if not 1 <= page <= len(doc):
            raise SimulationError(f"{pdf} has {len(doc)} page(s), not a page {page}")
        g = doc[page - 1].render(scale=PPI / 72, grayscale=True, no_smoothimage=True).to_numpy()
    finally:
        doc.close()
    if g.ndim == 3:
        g = g[..., 0]
    if (g.shape[1] > g.shape[0]) != (frame.page_w > frame.page_h):
        g = np.rot90(g, k=-1)  # quarter turn clockwise, as the sheet goes on the mat
    want = (frame.page_w * PPI / 25.4, frame.page_h * PPI / 25.4)
    if abs(g.shape[1] - want[0]) > 6 or abs(g.shape[0] - want[1]) > 6:
        raise SimulationError(
            f"the page is {g.shape[1] * 25.4 / PPI:.0f} x {g.shape[0] * 25.4 / PPI:.0f} mm, "
            f"the layout's {frame.page_w:g} x {frame.page_h:g}"
        )
    return np.ascontiguousarray(g)


def mark_mask(shape: tuple[int, int], frame: Frame) -> np.ndarray:
    """Where the frame's own marks print: the square and both L's, padded by ``MARK_PAD``."""
    s = PPI / 25.4
    m = np.zeros(shape, dtype=bool)

    def box(x0, y0, x1, y1):
        x0, y0, x1, y1 = x0 - MARK_PAD, y0 - MARK_PAD, x1 + MARK_PAD, y1 + MARK_PAD
        m[max(0, int(y0 * s)) : int(np.ceil(y1 * s)), max(0, int(x0 * s)) : int(np.ceil(x1 * s))] = True

    t, n = frame.thickness / 2, frame.length
    box(frame.inset, frame.top, frame.inset + proof.SQUARE, frame.top + proof.SQUARE)
    cx, cy = frame.inset + frame.width, frame.top  # top-right: legs run left and down
    box(cx - n, cy - t, cx + t, cy + t)
    box(cx - t, cy - t, cx + t, cy + n)
    cx, cy = frame.inset, frame.top + frame.height  # bottom-left: legs run right and up
    box(cx - t, cy - t, cx + n, cy + t)
    box(cx - t, cy - n, cx + t, cy + t)
    return m


class Sheet:
    def __init__(self, grey: np.ndarray, frame: Frame):
        self.frame = frame
        self.s = PPI / 25.4
        self.dark = grey < DARK
        self.marks = mark_mask(grey.shape, frame)
        self.other = (grey < INK) & ~self.marks

    def _at(self, arr, xs, ys):
        h, w = arr.shape
        return arr[np.clip((ys * self.s).astype(int), 0, h - 1), np.clip((xs * self.s).astype(int), 0, w - 1)]

    def samples(self, a, b):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * self.s) + 2
        return np.linspace(a[0], b[0], n), np.linspace(a[1], b[1], n)

    def runs(self, a, b):
        """Dark runs along a -> b as (from mm, to mm along it, on a mark)."""
        xs, ys = self.samples(a, b)
        d = self._at(self.dark, xs, ys)
        m = self._at(self.marks, xs, ys)
        total = float(np.hypot(b[0] - a[0], b[1] - a[1]))
        pos = np.linspace(0.0, total, len(xs))
        out, i = [], 0
        while i < len(d):
            if d[i]:
                j = i
                while j + 1 < len(d) and d[j + 1]:
                    j += 1
                out.append((float(pos[i]), float(pos[j]), bool(m[i : j + 1].any())))
                i = j + 1
            else:
                i += 1
        return out

    def clearance(self, a, b):
        xa, xb = sorted((a[0], b[0]))
        ya, yb = sorted((a[1], b[1]))
        x0, y0 = max(0.0, xa - REACH), max(0.0, ya - REACH)
        r = self.other[
            int(y0 * self.s) : int((yb + REACH) * self.s), int(x0 * self.s) : int((xb + REACH) * self.s)
        ]
        py, px = np.nonzero(r)
        if not len(px):
            return None
        px, py = px / self.s + x0, py / self.s + y0
        dx = np.maximum(0, np.maximum(xa - px, px - xb))
        dy = np.maximum(0, np.maximum(ya - py, py - yb))
        d = float(np.min(np.hypot(dx, dy)))
        return None if d > REACH else d

    def walk(self, name, a, b, accept=None) -> tuple[Pass, tuple[float, float] | None]:
        """Sweep a -> b until the first dark run (or the first one ``accept`` takes). Returns the
        pass and that run's (from, to) mm along it."""
        p = Pass(name, a, b)
        total = float(np.hypot(b[0] - a[0], b[1] - a[1]))
        hit = None
        for lo, hi, on_mark in self.runs(a, b):
            if accept is None or accept(hi - lo):
                hit = (lo, hi, on_mark)
                p.runs.append((lo, hi))
                break
            p.runs.append((lo, hi))
        end = hit[1] if hit else total
        p.walked = (0.0, end)
        p.caught = None if hit is None else ("mark" if hit[2] else "other print")
        if hit is None:
            p.note = "no dark run"
        elif not hit[2]:
            p.note = "latches onto print that is not a mark"
        f = end / total if total else 0.0
        stop = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        p.clearance = self.clearance(a, stop)
        if p.clearance is not None and p.clearance < SPOT and hit and hit[2]:
            p.note = "print inside the sensor's spot"
        return p, (hit[0], hit[1]) if hit else None


def simulate(grey: np.ndarray, frame: Frame, start: tuple[float, float] | None = None) -> Report:
    """Walk TB123's passes over ``grey`` (``render``'s output) and report each."""
    sh = Sheet(grey, frame)
    top, left = start or driver.default_start(frame)
    passes: list[Pass] = []
    x_edge, y_edge = frame.inset, frame.top  # the nominal corner, until the square is measured

    # 1. Area search. A row ends at the run it accepts; rows before it are swept whole.
    found = None
    walked: list[Pass] = []
    for i in range(ROWS):
        y = top + ROW_PITCH * i
        a, b = (left, y), (left + SWEEP, y)
        if i % 2:
            a, b = b, a
        p, run = sh.walk(f"area row {i}", a, b, lambda n: RUN_MIN <= n <= RUN_MAX)
        walked.append(p)
        if run:
            found = (i, y, run, a[0] + np.sign(b[0] - a[0]) * (run[0] + run[1]) / 2)
            break
    last = walked[-1]
    area = Pass(
        f"area search ({len(walked)} rows)",
        walked[0].start,
        last.end,
        runs=last.runs[-1:],
        caught=last.caught if found else None,
        walked=last.walked,
    )
    clear = [p.clearance for p in walked if p.clearance is not None]
    area.clearance = min(clear) if clear else None
    area.note = "" if found else "no run of 3.5-9 mm in 11 rows"
    if found and last.caught != "mark":
        area.note = "latches onto print that is not a mark"
    elif area.clearance is not None and area.clearance < SPOT:
        area.note = "print inside the sensor's spot"
    passes.append(area)
    if not found:
        return Report(passes, frame, (top, left))
    _, row_y, _, cx = found

    # 2. Locating the square: down at the run's centre + 1, then across from 5 mm left of its centre.
    p, run = sh.walk("square: down", (cx + 1, row_y - BACK_UP), (cx + 1, row_y - BACK_UP + DOWN))
    passes.append(p)
    cy = row_y - BACK_UP + (run[0] + run[1]) / 2 if run else y_edge + proof.SQUARE / 2
    p, run = sh.walk("square: across", (cx - SIDE_BACK, cy), (cx - SIDE_BACK + SIDE_ON, cy))
    passes.append(p)
    if run:
        x_edge = cx - SIDE_BACK + (run[0] + run[1]) / 2 - proof.SQUARE / 2
    y_edge = cy - proof.SQUARE / 2

    # 3. Bottom-left, then 4. top-right, from the measured edges.
    half = proof.crossing(frame)
    h, w = frame.height, frame.width
    xb = x_edge + half
    p, run = sh.walk("bottom-left: down", (xb, y_edge + h - RUN_UP), (xb, y_edge + h + BEYOND))
    passes.append(p)
    yc = y_edge + h - RUN_UP + (run[0] + run[1]) / 2 if run else y_edge + h
    passes.append(sh.walk("bottom-left: left", (xb, yc - half), (0.2, yc - half))[0])
    yt = y_edge + half
    p, run = sh.walk("top-right: right", (x_edge + w - RUN_UP, yt), (frame.page_w - 0.2, yt))
    passes.append(p)
    xc = x_edge + w - RUN_UP + (run[0] + run[1]) / 2 if run else x_edge + w
    passes.append(sh.walk("top-right: up", (xc - half, yt), (xc - half, 0.2))[0])
    return Report(passes, frame, (top, left))


def run(pdf: Path, frame: Frame, page: int = 1, start: tuple[float, float] | None = None) -> Report:
    return simulate(render(pdf, page, frame), frame, start)
