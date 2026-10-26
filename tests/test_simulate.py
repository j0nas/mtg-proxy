from __future__ import annotations

import numpy as np
import pytest
from typer.testing import CliRunner

from conftest import needs_engine
from mtgproxy import cutting, layout, simulate
from mtgproxy.cli import app

runner = CliRunner()


def frame_of(name: str):
    return cutting.frame_of(layout.build_cut_svg("a4", "standard", None, name))


@pytest.fixture(scope="module")
def sheet_3x3(tmp_path_factory):
    """The engine's 3x3 front page at 300 ppi, nine flat cards, as the sheet's PDF."""
    from test_build_cli import _duplex_pages

    out = tmp_path_factory.mktemp("sheet")
    _duplex_pages(out, "3x3", 9, ppi=300)
    return out / "3x3.pdf"


def paint(grey, x0, y0, x1, y1, value=0):
    """A patch of print in page mm."""
    s = simulate.PPI / 25.4
    out = grey.copy()
    out[int(y0 * s) : int(y1 * s), int(x0 * s) : int(x1 * s)] = value
    return out


def failing(report):
    return {p.name for p in report.passes if not p.ok}


@needs_engine
def test_the_default_3x3_sheet_is_clean(sheet_3x3):
    """Every pass finds its mark first and stays at least SPOT clear of the cards (simulated on the
    engine's own page, 2026-10-26); the page renders upright from its landscape PDF."""
    f = frame_of("3x3")
    report = simulate.run(sheet_3x3, f)
    assert report.ok, report.lines()
    assert len(report.passes) == 7
    assert all(p.caught == "mark" and p.clearance is not None for p in report.passes)
    area, down, _, bl_down, *_ = report.passes
    assert area.name.startswith("area search")
    assert area.runs[-1] == pytest.approx((2.5, 7.5), abs=0.2)  # the 5 mm square, from the start at x 1
    assert down.runs[0][1] - down.runs[0][0] == pytest.approx(5.0, abs=0.2)
    assert bl_down.runs[0][1] - bl_down.runs[0][0] == pytest.approx(1.0, abs=0.2)  # a 1 mm leg
    assert report.lines()[-1].startswith("clean")


@needs_engine
def test_print_next_to_a_pass_is_flagged(sheet_3x3):
    """A patch 0.5 mm from the bottom-left L's vertical-leg pass (y 285 on a 3x3 sheet), and another
    sitting on the top-right L's pass, are named; the rest of the first stay clean."""
    f = frame_of("3x3")
    grey = simulate.render(sheet_3x3, 1, f)
    beside = simulate.simulate(paint(grey, 4.3, 285.5, 5.3, 287.0), f)
    assert failing(beside) == {"bottom-left: left"}
    (p,) = (p for p in beside.passes if p.name == "bottom-left: left")
    assert p.caught == "mark" and p.clearance == pytest.approx(0.5, abs=0.1)
    assert beside.lines()[-1].startswith("FAIL") and "bottom-left: left" in beside.lines()[-1]

    on_it = simulate.simulate(paint(grey, 198.0, 11.0, 200.0, 13.0), f)  # between start and the leg
    assert "top-right: right" in failing(on_it)  # the next pass starts off the misread leg too
    (p,) = (p for p in on_it.passes if p.name == "top-right: right")
    assert p.caught == "other print" and "not a mark" in p.note


@needs_engine
def test_the_area_search_can_latch_onto_a_card(sheet_3x3):
    """A 5 mm dark block above the square, on a row before the one through it, is a run the firmware
    accepts: the search ends there, not at the square."""
    f = frame_of("3x3")
    grey = simulate.render(sheet_3x3, 1, f)
    report = simulate.simulate(paint(grey, 12.0, 10.0, 17.0, 12.5), f, start=(9.0, 10.0))
    assert not report.ok and report.passes[0].caught == "other print"


def test_a_vector_pdf_renders_the_same_way(tmp_path):
    """The frame's marks drawn as vectors (as the deck box's PDF is) walk clean, with no print near.
    A Figure of its own, without pyplot: switching pyplot's backend moves the engine's marks by a
    pixel or two in every later test that draws them."""
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    f = frame_of("3x3")
    fig = Figure(figsize=(f.page_w / 25.4, f.page_h / 25.4))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, f.page_w)
    ax.set_ylim(f.page_h, 0)
    ax.axis("off")
    t, n = f.thickness / 2, f.length
    cx, cy = f.inset + f.width, f.top
    bx, by = f.inset, f.top + f.height
    for x, y, w, h in (
        (f.inset, f.top, 5, 5),
        (cx - n, cy - t, n + t, 2 * t),
        (cx - t, cy - t, 2 * t, n + t),
        (bx - t, by - t, n + t, 2 * t),
        (bx - t, by - n, 2 * t, n + t),
    ):
        ax.add_patch(Rectangle((x, y), w, h, color="black", linewidth=0))
    pdf = tmp_path / "vector.pdf"
    fig.savefig(pdf)
    report = simulate.run(pdf, f)
    assert report.ok, report.lines()
    assert all(p.clearance is None for p in report.passes)


def test_a_page_of_the_wrong_size_is_refused(tmp_path):
    from matplotlib.figure import Figure

    pdf = tmp_path / "small.pdf"
    Figure(figsize=(5, 5)).savefig(pdf)
    with pytest.raises(simulate.SimulationError, match="the layout's 210 x 297"):
        simulate.run(pdf, frame_of("3x3"))
    with pytest.raises(simulate.SimulationError, match="page 2"):
        simulate.run(pdf, frame_of("3x3"), page=2)


def test_a_blank_page_finds_nothing():
    f = frame_of("3x3")
    blank = np.full((int(f.page_h * simulate.PPI / 25.4), int(f.page_w * simulate.PPI / 25.4)), 255, np.uint8)
    report = simulate.simulate(blank, f)
    assert not report.ok and len(report.passes) == 1 and report.passes[0].caught is None


@needs_engine
def test_cli_simulate_reports_and_exits_by_the_verdict(sheet_3x3):
    ok = runner.invoke(app, ["simulate", str(sheet_3x3)])
    assert ok.exit_code == 0, ok.output
    assert "scan starts at (11, 1) mm" in ok.output and "clean:" in ok.output
    assert "bottom-left: left" in ok.output
    wrong = runner.invoke(app, ["simulate", str(sheet_3x3), "--reg-inset", "10"])  # marks that aren't there
    assert wrong.exit_code == 1 and "FAIL" in wrong.output
    bad = runner.invoke(app, ["simulate", str(sheet_3x3), "--layout", "5x5"])
    assert bad.exit_code == 1 and "unknown layout" in bad.output
