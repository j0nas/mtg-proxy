from __future__ import annotations

import re

import pytest

from conftest import needs_engine
from mtgproxy.layout import build_cut_svg, fmt


@needs_engine
def test_unknown_sizes_are_clean_errors():
    from mtgproxy.layout import LayoutError, page_size_mm

    with pytest.raises(LayoutError, match="unknown paper size 'A4'"):
        page_size_mm("A4", "standard")
    with pytest.raises(LayoutError, match="no 'poker_x' layout for 'a4'"):
        page_size_mm("a4", "poker_x")


def test_fmt():
    assert fmt(10.0) == "10" and fmt(1.23456) == "1.2346" and fmt(0.5) == "0.5"


@needs_engine
def test_a4_standard_geometry_and_svg(tmp_path):
    svg = tmp_path / "cut.svg"
    g = build_cut_svg("a4", "standard", svg)
    assert g.cards == 8
    assert {g.page_w_mm, g.page_h_mm} == {210.0, 297.0}
    assert g.reg_inset_mm == 10.0
    assert (g.reg_x_mm, g.reg_y_mm) == (g.page_w_mm - 20, g.page_h_mm - 20)
    x0, y0, x1, y1 = g.cut_bbox_mm
    assert 10 <= x0 < x1 <= g.page_w_mm - 10 and 10 <= y0 < y1 <= g.page_h_mm - 10
    text = svg.read_text()
    assert text.count("<path ") == 8
    assert len(re.findall(r"\bA[0-9.]+,[0-9.]+ 0 [01] [01]", text)) == 8 * 4  # rounded corners as true arcs
    assert build_cut_svg("a4", "standard", None) == g  # geometry-only mode
    assert (g.reg_length_mm, g.layout) == (9.4, "4x2")
    assert build_cut_svg("a4", "standard", None, "4x2") == g


# The 3x3 sheet's printed marks (page mm, portrait), from page_manager.generate_reg_mark: the square
# is 5 mm from the inset with no stroke; an L's legs are 1 mm lines centred on the inset, running
# 10.5 mm from the corner's centre lines to their (square-capped) ends.
SQUARE_3X3 = (3.5, 3.5, 8.5, 8.5)  # 5 mm from the inset, no stroke (engine fork cd4166a)
TOP_RIGHT_L = [(196.0, 3.0, 207.0, 4.0), (206.0, 3.0, 207.0, 14.0)]
BOTTOM_LEFT_L = [(3.0, 293.0, 14.0, 294.0), (3.0, 283.0, 4.0, 294.0)]


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
    assert (g.reg_inset_mm, g.reg_x_mm, g.reg_y_mm, g.reg_length_mm) == (3.5, 203.0, 290.0, 10.5)
    text = svg.read_text()
    assert text.count("<path ") == 9 and "a4/standard 3x3: 9 cards" in text
    assert len(re.findall(r"\bA[0-9.]+,[0-9.]+ 0 [01] [01]", text)) == 9 * 4
    outlines = sorted((line.bbox() for line in geometry.load_svg(svg)), key=lambda b: (round(b[1]), b[0]))
    assert len(outlines) == 9
    for x0, y0, x1, y1 in outlines:
        assert (round(x1 - x0, 2), round(y1 - y0, 2)) == (63.0, 88.0)
        assert x0 > 3.5 and x1 < 206.5 and y0 > 3.5 and y1 < 293.5  # inside the mark frame
    marks = {"square": [SQUARE_3X3], "top-right": TOP_RIGHT_L, "bottom-left": BOTTOM_LEFT_L}
    clear = {k: min(_gap(o, m) for o in outlines for m in parts) for k, parts in marks.items()}
    # Every cut is more than 5 mm from every mark's ink: the square 6.3, the L's 5.4 (a 4x2 sheet's
    # L's: 6.1). Card print runs 0.7 mm past the cut, so it comes within about 4.7 mm of an L.
    assert clear == pytest.approx({"square": 6.78, "top-right": 5.42, "bottom-left": 5.42}, abs=0.05)
    # The corner a fourth mark would take is as clear as the others: -r 4 is refused for its scan
    # start (layout.registration), not for room.
    bottom_right = [(196.0, 293.0, 207.0, 294.0), (206.0, 283.0, 207.0, 294.0)]
    assert min(_gap(o, m) for o in outlines for m in bottom_right) == pytest.approx(5.42, abs=0.05)


@needs_engine
def test_the_marks_the_engine_prints_are_the_frame_cut_proxies_scans():
    """generate_reg_mark's 3x3 marks, measured: the square and both L's where the frame says."""
    import numpy as np

    from mtgproxy import engine

    engine.ensure_engine()
    from enums import Registration
    from page_manager import generate_reg_mark

    with generate_reg_mark("210mm", "297mm", "3.5mm", "1mm", "10.5mm", 300, Registration.THREE) as im:
        dark = np.asarray(im.convert("L")) < 128
    px = 300 / 25.4

    def ink(x0, y0, x1, y1):
        ys, xs = np.nonzero(dark[int(y0 * px) : int(y1 * px), int(x0 * px) : int(x1 * px)])
        found = (x0 + xs.min() / px, y0 + ys.min() / px, x0 + (xs.max() + 1) / px, y0 + (ys.max() + 1) / px)
        return pytest.approx(found, abs=0.1)  # within a 300 ppi pixel

    assert dark.shape == (3507, 2480)  # portrait A4 at 300 ppi
    assert ink(0, 0, 14, 14) == SQUARE_3X3
    assert ink(190, 0, 210, 14.5) == (196.0, 3.0, 207.0, 14.0)  # the top-right L's two legs
    assert ink(0, 282.5, 20, 297) == (3.0, 283.0, 14.0, 294.0)  # the bottom-left L's
    assert not dark[int(282 * px) :, int(190 * px) :].any()  # nothing bottom-right
    assert not dark[int(15 * px) : int(282 * px)].any()  # and nothing else on the page

    # The frame cut-proxies describes to the machine for this layout. TB123 takes the square's
    # centre less 2.5 mm as its edges, which must land on the L's centre lines, the frame's corners.
    from mtgproxy.cameo import proof, session

    g = build_cut_svg("a4", "standard", None, "3x3")
    f = session.Frame(
        g.page_w_mm, g.page_h_mm, g.reg_inset_mm, g.reg_x_mm, g.reg_y_mm, g.reg_length_mm, 1.0, 3
    )
    centre = (SQUARE_3X3[0] + SQUARE_3X3[2]) / 2
    assert centre - 2.5 == f.inset == (TOP_RIGHT_L[0][1] + TOP_RIGHT_L[0][3]) / 2
    assert f.inset + f.width == (TOP_RIGHT_L[1][0] + TOP_RIGHT_L[1][2]) / 2  # top-right corner, x
    assert f.inset + f.height == (BOTTOM_LEFT_L[0][1] + BOTTOM_LEFT_L[0][3]) / 2  # bottom-left, y
    assert f.inset + f.height - f.length == BOTTOM_LEFT_L[1][1]  # TB51's leg: corner line to inked end
    xs, ys = zip(*proof.square(f).points, strict=True)
    assert (min(xs), min(ys), max(xs), max(ys)) == SQUARE_3X3  # the proof cut traces the ink's edge


def test_layout_names_papers_and_marks():
    from mtgproxy import layout

    layout.check("4x2", "letter", "poker")  # the default layout goes with any paper and card
    layout.check("3x3", "a4", "standard")
    with pytest.raises(layout.LayoutError, match="unknown layout '5x5'"):
        layout.check("5x5", "a4", "standard")
    with pytest.raises(layout.LayoutError, match="A4 paper with standard cards only, not letter"):
        layout.check("3x3", "letter", "standard")
    with pytest.raises(layout.LayoutError, match="not a4 with poker"):
        layout.check("3x3", "a4", "poker")
    assert layout.registration("4x2", None) == "4" and layout.registration("4x2", "3") == "3"
    assert layout.registration("3x3", None) == "3" and layout.registration("3x3", "3") == "3"
    with pytest.raises(layout.LayoutError, match=r"3-mark registration only .* Drop -r 4"):
        layout.registration("3x3", "4")
    assert layout.registration("3x3", "5") == "5"  # left for the caller's "must be 3 or 4"
    assert layout.engine_flags("4x2") == [] and layout.engine_flags("3x3") == ["--borderless"]


@needs_engine
def test_cards_per_page_follows_the_layout():
    from mtgproxy import manifest

    assert manifest.cards_per_page("a4", "standard") == 8
    assert manifest.cards_per_page("a4", "standard", "3x3") == 9
