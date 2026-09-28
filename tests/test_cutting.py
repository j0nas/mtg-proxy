"""cut-proxies' options: the mark frame, the stored offset, and the blade presets."""

import json

from typer.testing import CliRunner

from mtgproxy import cli, cutting


def geometry():
    from mtgproxy import layout

    return layout.Geometry("a4", "standard", 8, 297.0, 210.0, 10.0, 277.0, 190.0, (0, 0, 1, 1))


def test_with_inset_moves_the_marks_and_keeps_the_page():
    moved = cutting.with_inset(geometry(), 5)
    assert (moved.reg_inset_mm, moved.reg_x_mm, moved.reg_y_mm) == (5, 287.0, 200.0)
    assert (moved.page_w_mm, moved.page_h_mm) == (297.0, 210.0)


def test_read_cut_offset(tmp_path):
    assert cutting.read_cut_offset(tmp_path / "missing.json") == (0.0, 0.0)
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"y_mm": 0.5}))
    assert cutting.read_cut_offset(p) == (0.0, 0.5)
    p.write_text("garbage")
    assert cutting.read_cut_offset(p) == (0.0, 0.0)


def test_the_stored_offset_is_the_calibrated_one():
    assert cutting.read_cut_offset() == (0.0, 0.5)


def cut_options(monkeypatch, *args: str) -> cutting.CutOptions:
    seen: list[cutting.CutOptions] = []
    monkeypatch.setattr(cutting, "run_cut", lambda o: seen.append(o) or 0)
    result = CliRunner().invoke(cli.app, ["cut", *args])
    assert result.exit_code == 0, result.output
    return seen[0]


def test_laminate_is_the_default_preset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no run.json here
    o = cut_options(monkeypatch)
    assert (o.force, o.speed, o.depth, o.passes) == (20, 25, 4, 3)
    assert (o.reg_length, o.reg_thickness) == (9.4, 1.0)


def test_the_paper_preset_and_explicit_flags_override_it(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    o = cut_options(monkeypatch, "--preset", "paper")
    assert (o.force, o.speed, o.depth, o.passes) == (10, 5, 1, 1)
    o = cut_options(monkeypatch, "--preset", "paper", "--passes", "2")
    assert (o.force, o.speed, o.depth, o.passes) == (10, 5, 1, 2)


def test_an_unknown_preset_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["cut", "--preset", "cardstock"])
    assert result.exit_code == 1 and "--preset must be one of laminate, paper" in result.output
