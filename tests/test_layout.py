from __future__ import annotations

import itertools
import re

import pytest

from conftest import needs_engine
from mtgproxy.layout import build_cut_svg, fmt


@needs_engine
def test_unknown_sizes_are_clean_errors():
    from mtgproxy.layout import LayoutError, page_size_mm

    with pytest.raises(LayoutError, match="unknown paper size 'A4'"):
        page_size_mm("A4", "standard", "4x2")
    with pytest.raises(LayoutError, match="no 'poker_x' layout for 'a4'"):
        page_size_mm("a4", "poker_x", "4x2")


def test_fmt():
    assert fmt(10.0) == "10" and fmt(1.23456) == "1.2346" and fmt(0.5) == "0.5"


@needs_engine
def test_a4_standard_geometry_and_svg(tmp_path):
    svg = tmp_path / "cut.svg"
    g = build_cut_svg("a4", "standard", svg, "4x2")
    assert g.cards == 8
    assert {g.page_w_mm, g.page_h_mm} == {210.0, 297.0}
    assert g.reg_inset_mm == 10.0
    assert (g.reg_x_mm, g.reg_y_mm) == (g.page_w_mm - 20, g.page_h_mm - 20)
    x0, y0, x1, y1 = g.cut_bbox_mm
    assert 10 <= x0 < x1 <= g.page_w_mm - 10 and 10 <= y0 < y1 <= g.page_h_mm - 10
    text = svg.read_text()
    assert text.count("<path ") == 8
    assert len(re.findall(r"\bA[0-9.]+,[0-9.]+ 0 [01] [01]", text)) == 8 * 4  # rounded corners as true arcs
    assert build_cut_svg("a4", "standard", None, "4x2") == g  # geometry-only mode
    assert (g.reg_length_mm, g.layout) == (9.4, "4x2")
    assert "a4/standard 4x2: 8 cards" in text


# The 3x3 sheet's printed marks (page mm, portrait), from page_manager.generate_reg_mark: the square
# is 5 mm from the inset with no stroke; an L's legs are 1 mm lines centred on the inset, running
# 6 mm from the corner's centre lines to their (square-capped) ends. The inset is 3.5 mm from the
# sides and 9 mm from the top and bottom (inset_y). At 3.5 mm all round the bottom-left L read 1-2 mm
# left on a laminated sheet; at 7 mm with 10.5 mm legs its search began on the bottom-left card's
# corner and once read that instead (2026-10-24).
SQUARE_3X3 = (3.5, 9.0, 8.5, 14.0)  # 5 mm from the inset, no stroke (engine fork cd4166a)
TOP_RIGHT_L = [(200.5, 8.5, 207.0, 9.5), (206.0, 8.5, 207.0, 15.0)]
BOTTOM_LEFT_L = [(3.0, 287.5, 9.5, 288.5), (3.0, 282.0, 4.0, 288.5)]


def _gap(a, b) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return (dx * dx + dy * dy) ** 0.5


@needs_engine
def test_a4_standard_3x3_geometry_and_svg(tmp_path):
    from mtgproxy.cameo import geometry

    svg = tmp_path / "cut.svg"
    g = build_cut_svg("a4", "standard", svg, "3x3")
    assert (g.cards, g.page_w_mm, g.page_h_mm) == (9, 210.0, 297.0)  # portrait
    assert (g.reg_inset_mm, g.reg_inset_y_mm, g.reg_x_mm, g.reg_y_mm, g.reg_length_mm) == (
        3.5,
        9.0,
        203.0,
        279.0,
        6.0,
    )
    text = svg.read_text()
    assert text.count("<path ") == 9 and "a4/standard 3x3: 9 cards" in text
    assert len(re.findall(r"\bA[0-9.]+,[0-9.]+ 0 [01] [01]", text)) == 9 * 4
    outlines = sorted((line.bbox() for line in geometry.load_svg(svg)), key=lambda b: (round(b[1]), b[0]))
    assert len(outlines) == 9
    for x0, y0, x1, y1 in outlines:
        assert (round(x1 - x0, 2), round(y1 - y0, 2)) == (63.0, 88.0)
        assert x0 > 3.5 and x1 < 206.5 and y0 > 9.0 and y1 < 288.0  # inside the mark frame
    marks = {"square": [SQUARE_3X3], "top-right": TOP_RIGHT_L, "bottom-left": BOTTOM_LEFT_L}
    clear = {k: min(_gap(o, m) for o in outlines for m in parts) for k, parts in marks.items()}
    # Cut to ink: the L's 5.2 mm (a 4x2 sheet's: 6.1), the square 1.4 mm: 9 mm from the top it sits
    # beside the top-left card's corner. None of TB123's passes over the square reach a card: its
    # first row runs through the square, and it measures the square down at x 7 and across at y 11.5.
    assert clear == pytest.approx({"square": 1.44, "top-right": 5.25, "bottom-left": 5.24}, abs=0.05)


@needs_engine
def test_the_marks_the_engine_prints_are_the_frame_cut_proxies_scans():
    """generate_reg_mark's 3x3 marks, measured: the square and both L's where the frame says."""
    import numpy as np

    from mtgproxy import engine

    engine.ensure_engine()
    from enums import Registration
    from page_manager import generate_reg_mark

    with generate_reg_mark(
        "210mm", "297mm", "3.5mm", "1mm", "6mm", 300, Registration.THREE, inset_y="9mm"
    ) as im:
        dark = np.asarray(im.convert("L")) < 128
    px = 300 / 25.4

    def ink(x0, y0, x1, y1):
        ys, xs = np.nonzero(dark[int(y0 * px) : int(y1 * px), int(x0 * px) : int(x1 * px)])
        found = (x0 + xs.min() / px, y0 + ys.min() / px, x0 + (xs.max() + 1) / px, y0 + (ys.max() + 1) / px)
        return pytest.approx(found, abs=0.1)  # within a 300 ppi pixel

    assert dark.shape == (3507, 2480)  # portrait A4 at 300 ppi
    assert ink(0, 0, 14, 16) == SQUARE_3X3
    assert ink(190, 0, 210, 18) == (200.5, 8.5, 207.0, 15.0)  # the top-right L's two legs
    assert ink(0, 279, 20, 297) == (3.0, 282.0, 9.5, 288.5)  # the bottom-left L's
    assert not dark[int(279 * px) :, int(190 * px) :].any()  # nothing bottom-right
    assert not dark[int(18 * px) : int(279 * px)].any()  # and nothing else on the page

    # The frame cut-proxies describes to the machine for this layout. TB123 takes the square's
    # centre less 2.5 mm as its edges, which must land on the L's centre lines, the frame's corners.
    from mtgproxy.cameo import proof, session

    g = build_cut_svg("a4", "standard", None, "3x3")
    f = session.Frame(
        g.page_w_mm,
        g.page_h_mm,
        g.reg_inset_mm,
        g.reg_x_mm,
        g.reg_y_mm,
        g.reg_length_mm,
        1.0,
        g.reg_inset_y_mm,
    )
    cx, cy = (SQUARE_3X3[0] + SQUARE_3X3[2]) / 2, (SQUARE_3X3[1] + SQUARE_3X3[3]) / 2
    assert cx - 2.5 == f.inset == (BOTTOM_LEFT_L[1][0] + BOTTOM_LEFT_L[1][2]) / 2  # left corners, x
    assert cy - 2.5 == f.top == (TOP_RIGHT_L[0][1] + TOP_RIGHT_L[0][3]) / 2  # top corners, y
    assert f.inset + f.width == (TOP_RIGHT_L[1][0] + TOP_RIGHT_L[1][2]) / 2  # top-right corner, x
    assert f.top + f.height == (BOTTOM_LEFT_L[0][1] + BOTTOM_LEFT_L[0][3]) / 2  # bottom-left, y
    assert f.top + f.height - f.length == BOTTOM_LEFT_L[1][1]  # TB51's leg: corner line to inked end
    assert (f.inset, f.top, f.inset + proof.SQUARE, f.top + proof.SQUARE) == SQUARE_3X3


def test_layout_names_papers_and_marks():
    from mtgproxy import layout

    assert layout.default("a4", "standard") == "3x3"  # nine to a sheet unless told
    assert layout.default("letter", "standard") == layout.default("a4", "poker") == "4x2"
    layout.check("4x2", "letter", "poker")  # the engine's own layout goes with any paper and card
    layout.check("3x3", "a4", "standard")
    with pytest.raises(layout.LayoutError, match="unknown layout '5x5'"):
        layout.check("5x5", "a4", "standard")
    with pytest.raises(layout.LayoutError, match="A4 paper with standard cards only, not letter"):
        layout.check("3x3", "letter", "standard")
    with pytest.raises(layout.LayoutError, match="not a4 with poker"):
        layout.check("3x3", "a4", "poker")
    assert layout.engine_flags("4x2") == [] and layout.engine_flags("3x3") == ["--borderless", "--label", ""]


@needs_engine
def test_cards_per_page_follows_the_layout():
    from mtgproxy import manifest

    assert manifest.cards_per_page("a4", "standard", "4x2") == 8
    assert manifest.cards_per_page("a4", "standard", "3x3") == 9


@needs_engine
def test_the_blank_paper_a_cut_reads_beside_each_mark_is_white_and_clear_of_the_cards(tmp_path):
    """cameo.sensing.quiet and reference: no ink within a millimetre (the sensor reads a 1 mm leg about
    2 mm wide), on the engine's own 3x3 marks; and at least 2 mm from every card on both layouts."""
    import numpy as np

    from mtgproxy import engine
    from mtgproxy.cameo import geometry, sensing, session

    engine.ensure_engine()
    from enums import Registration
    from page_manager import generate_reg_mark

    with generate_reg_mark(
        "210mm", "297mm", "3.5mm", "1mm", "6mm", 300, Registration.THREE, inset_y="9mm"
    ) as im:
        dark = np.asarray(im.convert("L")) < 128
    px = 300 / 25.4
    for name in ("3x3", "4x2"):
        svg = tmp_path / f"{name}.svg"
        g = build_cut_svg("a4", "standard", svg, name)
        f = session.Frame(
            g.page_w_mm,
            g.page_h_mm,
            g.reg_inset_mm,
            g.reg_x_mm,
            g.reg_y_mm,
            g.reg_length_mm,
            1.0,
            g.reg_inset_y_mm,
        )
        cards = [line.bbox() for line in geometry.load_svg(svg)]
        for c in [*sensing.quiet(f), sensing.reference(f)]:
            assert 1.0 <= c.x <= f.page_w - 1.0 and 1.0 <= c.y <= f.page_h - 1.0
            assert min(_gap((c.x, c.y, c.x, c.y), b) for b in cards) >= 2.0, (name, c.what)
            if name == "3x3":
                spot = dark[
                    int((c.y - 1) * px) : int((c.y + 1) * px), int((c.x - 1) * px) : int((c.x + 1) * px)
                ]
                assert spot.size and not spot.any(), c.what


def _marks(f) -> list[tuple[float, float, float, float]]:
    """The frame's mark ink, page mm: the square, then each L's two legs (square-capped, 1 mm)."""
    from mtgproxy.cameo import proof

    t, n = f.thickness / 2, f.length
    tr, bl = (f.inset + f.width, f.top), (f.inset, f.top + f.height)
    return [
        (f.inset, f.top, f.inset + proof.SQUARE, f.top + proof.SQUARE),
        (tr[0] - n, tr[1] - t, tr[0] + t, tr[1] + t),
        (tr[0] - t, tr[1] - t, tr[0] + t, tr[1] + n),
        (bl[0] - t, bl[1] - t, bl[0] + n, bl[1] + t),
        (bl[0] - t, bl[1] - n, bl[0] + t, bl[1] + t),
    ]


@needs_engine
def test_each_layout_names_four_3_mm_proof_targets_in_the_strip_above_its_cards():
    from mtgproxy.layout import proof_targets_mm

    assert proof_targets_mm("a4", "standard", "3x3") == (
        ((120, 11.75), (142, 11.75), (164, 11.75), (186, 11.75)),
        3,
    )
    assert proof_targets_mm("a4", "standard", "4x2") == (
        ((165, 12.9), (190, 12.9), (215, 12.9), (240, 12.9)),
        3,
    )
    g = build_cut_svg("a4", "standard", None, "3x3")
    assert (g.proof_targets[0], g.proof_target_mm) == ((120, 11.75), 3)
    assert proof_targets_mm("letter", "standard", "4x2") == ((), 0)  # a layout without them


@needs_engine
@pytest.mark.parametrize(("name", "cards"), [("3x3", 9), ("4x2", 8)])
def test_the_engine_prints_each_proof_target_where_the_proof_cut_traces_it(name, cards, tmp_path):
    """The engine's own sheet, rendered: each target's ink is the square cut-proxies traces, within
    a 300 ppi pixel or two of JPEG."""
    import numpy as np

    from mtgproxy import cutting, simulate
    from test_build_cli import _duplex_pages

    _duplex_pages(tmp_path, name, cards, ppi=300)
    g = build_cut_svg("a4", "standard", None, name)
    targets = cutting.sheet_targets(cutting.CutOptions(layout=name, proof=True), g)
    f = cutting.frame_of(g, targets=targets)
    grey = simulate.render(tmp_path / f"{name}.pdf", 1, f)
    px = simulate.PPI / 25.4
    assert [t.number for t in f.targets] == [1, 2, 3, 4]
    for t in f.targets:
        x0, y0 = t.x - 2.5, t.y - 2.5
        ys, xs = np.nonzero(
            grey[int(y0 * px) : int((t.y + 2.5) * px), int(x0 * px) : int((t.x + 2.5) * px)] < 64
        )
        ink = (x0 + xs.min() / px, y0 + ys.min() / px, x0 + (xs.max() + 1) / px, y0 + (ys.max() + 1) / px)
        assert ink == pytest.approx(t.outline().bbox(), abs=0.15), t.number
    report = simulate.simulate(grey, f)  # and the scan still reads its marks first, clear of them
    assert report.ok, report.lines()


@needs_engine
@pytest.mark.parametrize("name", ["3x3", "4x2"])
def test_the_proof_targets_keep_clear_of_the_marks_the_cards_and_the_scan(name, tmp_path):
    """Inside the mark frame; 6 mm of blank paper round every mark (MARK_QUIET in the deck box's
    job.ts, Graphtec's quiet zone); clear of the cards' cuts and of their print, which bleeds about
    0.7 mm past them; clear of the area search's rows, of where the firmware's retry reads its light
    and searches again (a third of the frame along, a leg's length higher, cameo-re NOTES.md), and
    of every reading the sensor check takes."""
    from mtgproxy import cutting
    from mtgproxy.cameo import driver, geometry, proof, sensing

    svg = tmp_path / "cut.svg"
    g = build_cut_svg("a4", "standard", svg, name)
    f = cutting.frame_of(g, targets=proof.targets(g.proof_targets, g.proof_target_mm))
    cards = geometry.load_svg(svg)
    top, left = driver.default_start(f)
    area = (left, top, left + 30, top + 30)
    retry = (left + f.width / 3, top - f.length, left + f.width / 3 + 30, top - f.length + 30)
    readings = [*sensing.quiet(f), sensing.reference(f), *sensing.checks(f), sensing.placement(f)]
    for t in f.targets:
        box = t.outline().bbox()
        assert (
            f.inset < box[0] and box[2] < f.inset + f.width and f.top < box[1] and box[3] < f.top + f.height
        )
        assert min(_gap(box, m) for m in _marks(f)) >= 6.0, t.number
        assert min(_gap(box, c.bbox()) for c in cards) >= 1.9 and not proof.on_job(t.outline(), cards)
        assert _gap(box, area) > 2 and _gap(box, retry) > 2, t.number
        assert min(_gap(box, (c.x, c.y, c.x, c.y)) for c in readings) >= 3, t.number
    xs = sorted(t.x for t in f.targets)
    assert min(b - a for a, b in itertools.pairwise(xs)) >= 20  # a round's cut lands on one alone
