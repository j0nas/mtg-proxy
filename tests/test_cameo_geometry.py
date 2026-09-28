"""Our driver's cut geometry: SVG → page-mm polylines, inside-first order, passes."""

import math

import pytest

from mtgproxy.cameo import geometry as g


def svg(tmp_path, body, size='width="297mm" height="210mm" viewBox="0 0 297 210"'):
    f = tmp_path / "cut.svg"
    f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {size}>{body}</svg>')
    return f


def close(a, b, tol=1e-3):
    return all(abs(p - q) < tol for p, q in zip(a, b, strict=True))


def test_paths_come_out_in_page_millimetres_whatever_the_svg_units(tmp_path):
    lines = g.load_svg(svg(tmp_path, '<path d="M10,20 L30,20 L30,40 Z"/>'))
    assert len(lines) == 1 and lines[0].closed
    assert all(
        close(a, b) for a, b in zip(lines[0].points, [(10, 20), (30, 20), (30, 40), (10, 20)], strict=True)
    )
    # The same square in a px document (Inkscape's 96 dpi): 1 mm = 96 / 25.4 px.
    px = 96 / 25.4
    body = f'<path d="M{10 * px},{20 * px} L{30 * px},{20 * px}"/>'
    lines = g.load_svg(svg(tmp_path, body, size='width="1122.5px" height="793.7px"'))
    assert close(lines[0].points[0], (10, 20)) and close(lines[0].points[-1], (30, 20))


def test_transforms_apply_and_arcs_flatten_onto_the_circle(tmp_path):
    body = '<g transform="translate(100,50)"><path d="M3,0 A3,3 0 0 1 0,3"/></g>'
    (line,) = g.load_svg(svg(tmp_path, body))
    assert close(line.points[0], (103, 50)) and close(line.points[-1], (100, 53))
    assert all(abs(math.dist(p, (100, 50)) - 3) < 1e-3 for p in line.points)
    assert len(line.points) > 10  # a quarter circle of r=3 is ~4.7 mm: sampled every 0.25 mm


def test_multiple_subpaths_split_into_separate_polylines(tmp_path):
    lines = g.load_svg(svg(tmp_path, '<path d="M0,0 L5,0 M10,0 L15,0"/>'))
    assert [len(pl.points) for pl in lines] == [2, 2]


def test_everything_inside_an_outline_is_cut_before_it():
    outline = g.Polyline(((0, 0), (100, 0), (100, 100), (0, 100), (0, 0)))
    slit = g.Polyline(((10, 10), (10, 20)))
    hole = g.Polyline(((50, 50), (60, 50), (60, 60), (50, 60), (50, 50)))
    stray = g.Polyline(((150, 0), (160, 0)))  # outside everything: same depth as the outline
    assert g.depths([outline, slit, hole, stray]) == [0, 1, 1, 0]
    ordered = g.order([outline, stray, hole, slit])
    assert ordered[:2] == [slit, hole]  # inner first, nearest start first
    assert set(ordered[2:]) == {outline, stray}


def test_an_open_path_is_entered_from_its_nearer_end():
    dash = g.Polyline(((10, 0), (0, 0)))
    (first,) = g.order([dash], start=(0, 0))
    assert first.points[0] == (0, 0)


def test_passes_lap_closed_loops_continuously_and_repeat_open_paths():
    square = g.Polyline(((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)))
    dash = g.Polyline(((20, 0), (21, 0)))
    loop, *dashes = g.passes([square, dash], 3, overcut_mm=0.5)
    assert loop.points[:5] == square.points and len(loop.points) == 5 + 4 + 4 + 1
    assert loop.points[-1] == pytest.approx((0.5, 0))  # overcut past the start, no lift
    assert dashes == [dash, dash, dash]
