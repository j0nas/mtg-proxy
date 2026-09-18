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
