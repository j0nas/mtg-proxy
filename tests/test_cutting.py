"""cut-proxies' options: the mark frame, the stored offset, the blade presets, and the menu."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import needs_engine
from mtgproxy import cli, cutting, layout


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


def frame(inset=10.0):
    from mtgproxy.cameo import session

    return session.Frame(page_w=297, page_h=210, inset=inset, width=297 - 2 * inset, height=210 - 2 * inset)


def test_the_scan_starts_once_2_mm_into_the_square(tmp_path):
    """TB123's area search finds the square from one start: nothing is remembered between sheets."""
    o = cutting.CutOptions(out_dir=tmp_path)
    assert cutting.scan_starts(o, frame()) == ([(12, 4)], "2 mm into the square")
    assert cutting.scan_starts(o, frame(inset=5)) == ([(8.5, 1)], "3.5 mm into the square")


def test_given_starts_are_tried_as_given(tmp_path):
    o = cutting.CutOptions(out_dir=tmp_path, scan_starts=[(6.0, 17.0), (8.0, 17.0)])
    assert cutting.scan_starts(o, frame()) == ([(6.0, 17.0), (8.0, 17.0)], "--scan-start")


def test_the_default_start_follows_the_paper_and_a_given_one_stays_as_given(tmp_path):
    laid = replace(frame(inset=5), paper_at=(3.0, 3.0))  # the paper deck box, its pouch on the tack
    o = cutting.CutOptions(out_dir=tmp_path)
    assert cutting.scan_starts(o, laid) == ([(11.5, 4)], "3.5 mm into the square")
    o = cutting.CutOptions(out_dir=tmp_path, scan_starts=[(5.5, 14.5)])
    assert cutting.scan_starts(o, laid) == ([(5.5, 14.5)], "--scan-start")


def test_the_diagnose_report_says_where_the_paper_lies_when_its_off_the_grids_corner():
    head = "cut-proxies: before any scan, in the loaded mat's frame (page mm"
    assert cutting.diagnose_report([], frame()).splitlines()[0] == head + "):"
    laid = replace(frame(), paper_at=(3.0, 2.0))
    assert cutting.diagnose_report([], laid).splitlines()[0] == (
        head + ", the paper's corner 3 mm right and 2 mm down of the mat grid's):"
    )


def cut_options(monkeypatch, *args: str) -> cutting.CutOptions:
    seen: list[cutting.CutOptions] = []
    monkeypatch.setattr(cutting, "run_cut", lambda o: seen.append(o) or 0)
    result = CliRunner().invoke(cli.app, ["cut", *args])
    assert result.exit_code == 0, result.output
    return seen[0]


def test_laminate_is_the_default_preset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no run.json here
    o = cut_options(monkeypatch)
    assert (o.force, o.speed, o.depth, o.passes) == (20, 25, 5, 3)
    assert (o.reg_length, o.reg_thickness) == (None, 1.0)  # legs: the layout's, 6 on a 3x3 A4
    assert o.layout == "3x3"  # a plain sheet is the default layout, on A4
    assert cut_options(monkeypatch, "-p", "letter").layout == "4x2"  # 3x3 is A4 only


def test_the_paper_preset_and_explicit_flags_override_it(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    o = cut_options(monkeypatch, "--preset", "paper")
    assert (o.force, o.speed, o.depth, o.passes) == (10, 5, 1, 1)
    o = cut_options(monkeypatch, "--preset", "paper", "--passes", "2")
    assert (o.force, o.speed, o.depth, o.passes) == (10, 5, 1, 2)


def test_paper_at_is_right_then_down_or_one_number_for_both(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert cut_options(monkeypatch).paper_at == (0.0, 0.0)
    assert cut_options(monkeypatch, "--paper-at", "3,2").paper_at == (3.0, 2.0)
    assert cut_options(monkeypatch, "--paper-at", "3").paper_at == (3.0, 3.0)
    assert cut_options(monkeypatch, "--paper-at", "2.5, 0").paper_at == (2.5, 0.0)


@pytest.mark.parametrize("value", ["3,2,1", "3,", "", "x", "3,y", "-1", "3,-2", "nan", "inf,3"])
def test_paper_at_refuses_anything_but_one_or_two_lengths_of_0_or_more(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cutting, "run_cut", lambda o: pytest.fail(f"cut with --paper-at {value!r}"))
    result = CliRunner().invoke(cli.app, ["cut", f"--paper-at={value}"])
    want = f"--paper-at wants RIGHT,DOWN in mm, 0 or more (one number for both), got {value!r}"
    assert result.exit_code == 1 and want in result.output


def test_an_unknown_preset_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["cut", "--preset", "cardstock"])
    assert result.exit_code == 1 and "--preset must be one of laminate, paper" in result.output


# --- proof targets ------------------------------------------------------------------
def test_the_proof_flags_name_the_targets_where_to_start_and_the_blade(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    o = cut_options(
        monkeypatch,
        *("--proof", "--proof-target", "281.5,179.5", "--proof-target", "288.5, 179.5"),
        *("--proof-target-size", "3", "--proof-from", "1", "--proof-blade", "8,4,1"),
    )
    assert o.proof_targets == [(281.5, 179.5), (288.5, 179.5)] and o.proof_target_size == 3
    assert (o.proof_from, o.proof_blade) == (1, (8, 4, 1))
    o = cut_options(monkeypatch, "--proof")
    assert (o.proof_targets, o.proof_target_size, o.proof_from, o.proof_blade) == (None, None, 0, None)


@pytest.mark.parametrize(
    ("flag", "value", "want"),
    [
        ("--proof-target", "3", "--proof-target wants X,Y in mm"),
        ("--proof-target", "0,5", "--proof-target wants X,Y in mm"),
        ("--proof-target", "5,x", "--proof-target wants X,Y in mm"),
        ("--proof-blade", "10,5", "--proof-blade wants FORCE,SPEED,DEPTH"),
        ("--proof-blade", "50,5,2", "--proof-blade wants FORCE,SPEED,DEPTH"),
    ],
)
def test_proof_flags_that_cant_be_cut_are_refused(monkeypatch, tmp_path, flag, value, want):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cutting, "run_cut", lambda o: pytest.fail(f"cut with {flag} {value!r}"))
    result = CliRunner().invoke(cli.app, ["cut", "--proof", f"{flag}={value}"])
    assert result.exit_code == 1 and want in result.output


@needs_engine
def test_a_proxy_sheets_targets_are_its_layouts_and_an_svg_sheet_has_only_its_flags():
    g = layout.build_cut_svg("a4", "standard", None, "3x3")
    sheet = cutting.sheet_targets(cutting.CutOptions(proof=True), g)
    assert [(t.x, t.y, t.size, t.number) for t in sheet] == [
        (120, 11.75, 3, 1),
        (142, 11.75, 3, 2),
        (164, 11.75, 3, 3),
        (186, 11.75, 3, 4),
    ]
    assert [t.number for t in cutting.sheet_targets(cutting.CutOptions(proof=True, proof_from=3), g)] == [4]
    g4 = layout.build_cut_svg("a4", "standard", None, "4x2")
    box = cutting.CutOptions(svg=Path("box.svg"), layout="4x2", proof=True)
    assert cutting.sheet_targets(box, g4) == ()  # the engine didn't print it: its layout's aren't there
    assert "box.svg has no proof targets: give each one" in cutting.no_targets(box, g4)
    flagged = replace(
        box, proof_targets=[(281.5, 179.5), (288.5, 179.5)], proof_target_size=3.5, proof_from=1
    )
    assert [(t.x, t.y, t.size, t.number) for t in cutting.sheet_targets(flagged, g4)] == [
        (288.5, 179.5, 3.5, 2)
    ]
    used = cutting.no_targets(replace(flagged, proof_from=2), g4)
    assert "all 2 proof targets on this sheet are cut already (--proof-from 2)" in used


@needs_engine
def test_a_stop_at_the_proof_cut_says_how_far_the_targets_are_cut_and_a_retry_takes_the_next(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cutting, "dry_confirm", lambda round_, rescan: "q")
    o = cutting.CutOptions(proof=True, dry_run=True, out_dir=tmp_path)
    with pytest.raises(
        cutting.CutError, match="proof target 1 on this sheet is cut: a rerun on it takes --proof-from 1"
    ):
        cutting.run_cut(o)
    assert o.proof_from == 1
    monkeypatch.setattr(cutting, "dry_confirm", lambda round_, rescan: "y")
    assert cutting.run_cut(o) == 0 and o.proof_from == 2  # the retry traced target 2
    transcript = (tmp_path / "a4-standard-3x3.cmds").read_text("latin1")
    assert "FX10,1\x03TJ0\x03!5,1" in transcript  # with proof.BLADE


@needs_engine
def test_proof_with_every_target_cut_already_stops_before_connecting(monkeypatch, tmp_path):
    o = cutting.CutOptions(proof=True, dry_run=True, out_dir=tmp_path, proof_from=4)
    monkeypatch.setattr(cutting, "frame_of", lambda *a, **k: pytest.fail("planned a cut"))
    with pytest.raises(cutting.CutError, match="all 4 proof targets on this sheet are cut already"):
        cutting.run_cut(o)


def test_a_sheet_printed_before_proof_targets_is_refused_for_proof(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "olddeck", paper="a4", layout="3x3", cards=9, proof_targets=False)
    monkeypatch.setattr(cutting, "run_cut", lambda o: pytest.fail("cut with --proof"))
    result = CliRunner().invoke(cli.app, ["cut", "--proof"])
    assert (
        result.exit_code == 1
        and "olddeck was printed before its sheets carried proof targets" in result.output
    )
    o = cut_options(monkeypatch, "--dry-run")  # without --proof it cuts
    assert (o.label, o.proof) == ("olddeck", False)


def test_a_retry_on_the_same_sheet_takes_the_next_target_and_the_next_sheet_its_first(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    menu(monkeypatch, "retry", "next-proof", "done")
    seen: list[int] = []

    def run_cut(o):
        seen.append(o.proof_from)
        o.proof_from += 1  # each run traces one target
        if len(seen) == 1:
            raise cutting.CutError("stopped at the proof cut: nothing else was cut")
        return 0

    monkeypatch.setattr(cutting, "run_cut", run_cut)
    result = CliRunner().invoke(cli.app, ["cut", "--proof", "--proof-from", "1"])
    assert result.exit_code == 0, result.output
    assert seen == [1, 2, 0]


# --- the menu a bare cut-proxies shows at a terminal ---------------------------
def write_run(where, name, paper="letter", layout="4x2", cards=8, registration="3", proof_targets=True):
    from mtgproxy.sidecar import RunInfo

    where.mkdir(parents=True, exist_ok=True)
    info = RunInfo(
        name,
        paper,
        "standard",
        cards=cards,
        fronts_only=True,
        generated="2026-10-01",
        registration=registration,
        layout=layout,
        proof_targets=proof_targets,
    )
    return info.write(where)


def test_a_3x3_run_is_cut_as_one(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "test-sheet", paper="a4", layout="3x3", cards=9)
    o = cut_options(monkeypatch, "--dry-run")
    assert (o.layout, o.paper, o.reg_length, o.reg_inset) == ("3x3", "a4", None, None)


def test_a_4_mark_run_is_refused(monkeypatch, tmp_path):
    """A sheet printed before 3-mark only: its marks have no square for TB123 to find."""
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "old", paper="a4", registration="4")
    monkeypatch.setattr(cutting, "run_cut", lambda o: pytest.fail("a 4-mark sheet was cut"))
    result = CliRunner().invoke(cli.app, ["cut", "--dry-run"])
    assert result.exit_code == 1
    assert "old was printed with 4-mark registration" in result.output
    assert "4-mark sheets are no longer supported: rebuild and reprint" in result.output
    result = CliRunner().invoke(cli.app, ["cut", "-r", "3", "--dry-run"])
    assert result.exit_code == 2 and "No such option" in result.output  # no -r: every sheet is 3-mark


def test_a_plain_3x3_sheet(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no run.json
    o = cut_options(monkeypatch, "--layout", "3x3", "--dry-run")
    assert (o.layout, o.label) == ("3x3", None)
    result = CliRunner().invoke(cli.app, ["cut", "--layout", "3x3", "-p", "letter", "--dry-run"])
    assert result.exit_code == 1 and "A4 paper with standard cards only" in result.output


def test_the_menu_names_a_3x3_run(tmp_path):
    from mtgproxy import cutmenu

    assert (
        cutmenu.describe(write_run(tmp_path, "t", "a4", layout="3x3", cards=9))
        == "t: 9 cards, a4/standard 3x3"
    )


@needs_engine
def test_a_3x3_dry_run_scans_its_own_frame(tmp_path, capsys):
    """The whole job against the stand-in: the frame, leg length and SVG are the 3x3 layout's."""
    o = cutting.CutOptions(layout="3x3", dry_run=True, out_dir=tmp_path, label="t")
    assert cutting.run_cut(o) == 0
    out = capsys.readouterr().out
    assert (
        "t — 9 cards (3x3), 3-mark registration (marks inset 3.5 mm, 9 mm top and bottom, 203 x 279 mm apart, 6 x 1 mm)"
        in out
    )
    assert "scan starts at (11, 1) mm, 2 mm into the square" in out
    sent = (tmp_path / "t.cmds").read_bytes().decode().split("\x03")
    # Legs 6 mm (TB51 in 1/20 mm), then TB123 with the frame's height and width and the start.
    assert "TB51,120" in sent and "TB123,5580,4060,220,20" in sent
    assert (tmp_path / "t.svg").read_text().count("<path ") == 9
    four = cutting.CutOptions(layout="4x2", dry_run=True, out_dir=tmp_path, label="f")
    assert cutting.run_cut(four) == 0
    sent = (tmp_path / "f.cmds").read_bytes().decode().split("\x03")
    assert "TB51,188" in sent and "TB123,3800,5540,240,80" in sent  # the 4x2's frame, from (12, 4)
    assert not (tmp_path / "scan-start.json").exists()


@needs_engine
def test_a_dry_run_says_where_the_paper_lies_only_when_its_off_the_grids_corner(tmp_path, capsys):
    o = cutting.CutOptions(
        layout="4x2", reg_inset=5, dry_run=True, out_dir=tmp_path, label="box", paper_at=(3.0, 3.0)
    )
    assert cutting.run_cut(o) == 0
    out = capsys.readouterr().out
    assert "cut-proxies: the paper's top-left corner 3 mm right and 3 mm down of the mat grid's\n" in out
    assert "scan starts at (11.5, 4) mm, 3.5 mm into the square" in out
    assert "TB123,4000,5740,230,80" in (tmp_path / "box.cmds").read_bytes().decode().split("\x03")
    assert cutting.run_cut(replace(o, paper_at=(0.0, 0.0))) == 0
    out = capsys.readouterr().out
    assert "scan starts at (8.5, 1) mm" in out and "paper's top-left corner" not in out


def menu(monkeypatch, *answers):
    """Answer the menu's questions in order; returns what it asked as (message, titles)."""
    from mtgproxy import cutmenu

    asked, script = [], iter(answers)

    class Question:
        def unsafe_ask(self):
            return next(script)

    def select(message, choices):
        asked.append((message, [c.title for c in choices]))
        return Question()

    monkeypatch.setattr(cli, "_at_terminal", lambda: True)
    monkeypatch.setattr(cutmenu.questionary, "select", select)
    monkeypatch.setattr(cutting, "wait_for_next_sheet", lambda o, interrupted: False)  # Enter: menu
    return asked


def test_enter_cuts_this_folders_run_with_laminate_over_bluetooth(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "mydeck")
    asked = menu(monkeypatch, "default", "done")
    o = cut_options(monkeypatch)
    assert asked[0][1][0] == "Cut mydeck: 8 cards, letter/standard 4x2 · laminate · Bluetooth"
    assert (o.label, o.paper, o.connection) == ("mydeck", "letter", "ble")
    assert (o.force, o.passes, o.dry_run, o.proof, o.probe) == (20, 3, False, False, False)


def test_with_no_run_here_enter_cuts_the_newest_run_below(monkeypatch, tmp_path):
    import os

    monkeypatch.chdir(tmp_path)
    old = write_run(tmp_path / "olddeck", "olddeck")
    os.utime(old, (1, 1))
    write_run(tmp_path / "otherdeck", "otherdeck")
    asked = menu(monkeypatch, "default", "done")
    o = cut_options(monkeypatch)
    assert asked[0][1][0] == "Cut otherdeck: 8 cards, letter/standard 4x2 · laminate · Bluetooth"
    assert (o.label, o.paper) == ("otherdeck", "letter")


def test_outside_a_deck_folder_the_menu_lists_the_proxies_folder(monkeypatch, tmp_path, proxies_root):
    monkeypatch.chdir(tmp_path)  # nothing here or below
    write_run(proxies_root / "built", "built")
    asked = menu(monkeypatch, "default", "done")
    o = cut_options(monkeypatch)
    assert asked[0][1][0].startswith("Cut built: 8 cards")
    assert o.label == "built"


def test_with_no_run_anywhere_the_default_is_a_plain_a4_sheet(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "default", "done")
    o = cut_options(monkeypatch)
    assert asked[0][1][0].startswith("Cut plain A4 sheet")
    assert (o.label, o.paper, o.card_size, o.layout) == (None, "a4", "standard", "3x3")


def test_choose_picks_a_run_below_a_preset_and_a_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sub = write_run(tmp_path / "otherdeck", "otherdeck", paper="a4")
    asked = menu(monkeypatch, "choose", str(sub), "paper", "proof", "done")
    o = cut_options(monkeypatch)
    assert asked[1][1][0].startswith("otherdeck: ")  # nothing here, so the newest run below is the default
    assert (o.label, o.force, o.passes, o.proof, o.dry_run) == ("otherdeck", 10, 1, True, False)


def test_choose_plain_ignores_this_folders_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "mydeck")
    asked = menu(monkeypatch, "choose", "", "laminate", "dry-run")
    o = cut_options(monkeypatch)
    assert asked[1][1][0].startswith("mydeck:")  # this folder's run stays the default
    assert (o.label, o.paper, o.dry_run) == (None, "a4", True)


def test_usb_still_asks_and_says_so(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "default", "done")
    o = cut_options(monkeypatch, "--usb")
    assert asked[0][1][0].endswith("· USB") and o.connection == "usb"


def test_any_other_flag_cuts_without_asking(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch)
    o = cut_options(monkeypatch, "--dry-run")
    assert asked == [] and o.dry_run


def test_quit_cuts_nothing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    menu(monkeypatch, "quit")
    monkeypatch.setattr(cutting, "run_cut", lambda o: pytest.fail("cut after quit"))
    result = CliRunner().invoke(cli.app, ["cut"])
    assert result.exit_code == 0, result.output


# --- what's next after a cut ------------------------------------------------------
def cut_jobs(monkeypatch, *args: str) -> tuple[list[cutting.CutOptions], str]:
    seen: list[cutting.CutOptions] = []
    monkeypatch.setattr(cutting, "run_cut", lambda o: seen.append(o) or 0)
    result = CliRunner().invoke(cli.app, ["cut", *args])
    assert result.exit_code == 0, result.output
    return seen, result.output


def test_next_sheet_repeats_the_job_and_one_more_pass_cuts_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "next", "pass", "next", "done")
    jobs, _ = cut_jobs(monkeypatch, "--preset", "laminate")  # any flag skips the first menu
    assert [m for m, _ in asked] == [f"Sheet {n} done. Lift a corner:" for n in (1, 2, 2, 3)]
    assert jobs[0] == jobs[1] == jobs[3] and (jobs[0].passes, jobs[0].proof) == (3, False)
    assert (jobs[2].passes, jobs[2].proof, jobs[2].force) == (1, False, 20)


def test_a_proofed_batch_proofs_its_first_sheet_only_unless_asked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "next", "next-proof", "next", "done")
    jobs, _ = cut_jobs(monkeypatch, "--proof")
    assert [j.proof for j in jobs] == [True, False, True, False]
    assert all(j.passes == 3 for j in jobs)
    assert asked[0][1][:2] == [
        "next sheet: it's loaded, cut it now, same job, no proof cuts",
        "next sheet, proof cuts first",
    ]


def test_the_menu_flows_into_whats_next(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "default", "done")
    jobs, _ = cut_jobs(monkeypatch)
    assert len(jobs) == 1 and asked[1][0] == "Sheet 1 done. Lift a corner:"


def test_dry_runs_and_probes_dont_ask_whats_next(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch)
    for flag in ("--dry-run", "--probe"):
        jobs, _ = cut_jobs(monkeypatch, flag)
        assert len(jobs) == 1
    assert asked == []


def watch(monkeypatch, *reloads, enter=False):
    """Script the mat watcher: each call returns the next of ``reloads`` (True: reloaded, an
    exception: raised); Enter is pressed during the countdown when ``enter``."""
    script = iter(reloads)

    def wait(o, interrupted):
        r = next(script)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(cutting, "wait_for_next_sheet", wait)
    monkeypatch.setattr(cli, "_enter_within", lambda s: enter)


def test_reloading_the_mat_starts_the_next_sheet_without_a_proof(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "done")
    watch(monkeypatch, True, False)  # sheet 1: reloaded; sheet 2: Enter
    jobs, output = cut_jobs(monkeypatch, "--proof")
    assert [j.proof for j in jobs] == [True, False] and jobs[1].passes == 3
    assert [m for m, _ in asked] == ["Sheet 2 done. Lift a corner:"]  # no menu after sheet 1
    assert "sheet 2 starts in 1 s" in output


def test_enter_during_the_countdown_holds_the_next_sheet(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "done")
    watch(monkeypatch, True, enter=True)
    jobs, _ = cut_jobs(monkeypatch, "--proof")
    assert len(jobs) == 1 and [m for m, _ in asked] == ["Sheet 1 done. Lift a corner:"]


def test_a_mat_that_cant_be_watched_falls_back_to_the_menu(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "done")
    watch(monkeypatch, cutting.CutError("can't watch the mat (Cameo disconnected)"))
    jobs, output = cut_jobs(monkeypatch, "--proof")
    assert len(jobs) == 1 and len(asked) == 1 and "choose from the menu" in output


def test_from_a_script_one_cut_and_the_passes_hint(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    jobs, output = cut_jobs(monkeypatch)
    assert len(jobs) == 1 and "Rerun with --passes 1" in output


def failing_then_cutting(monkeypatch, fails: int) -> list[cutting.CutOptions]:
    seen: list[cutting.CutOptions] = []

    def run_cut(o):
        seen.append(o)
        if len(seen) <= fails:
            raise cutting.CutError("registration marks not found")
        return 0

    monkeypatch.setattr(cutting, "run_cut", run_cut)
    return seen


def test_a_failed_job_can_be_retried(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    asked = menu(monkeypatch, "retry", "next", "done")
    jobs = failing_then_cutting(monkeypatch, fails=1)
    result = CliRunner().invoke(cli.app, ["cut", "--proof"])
    assert result.exit_code == 0, result.output
    assert "registration marks not found" in result.output
    assert [m.split(".")[0] for m, _ in asked] == ["Sheet 1 failed", "Sheet 1 done", "Sheet 2 done"]
    assert jobs[0] == jobs[1] and jobs[1].proof  # the retry proofs again: no sheet has passed yet
    assert replace(jobs[2], proof=True) == jobs[0]


def test_done_after_a_failure_exits_with_an_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    menu(monkeypatch, "done")
    failing_then_cutting(monkeypatch, fails=1)
    assert CliRunner().invoke(cli.app, ["cut", "--proof"]).exit_code == 1


def test_from_a_script_a_failure_just_fails(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    jobs = failing_then_cutting(monkeypatch, fails=1)
    result = CliRunner().invoke(cli.app, ["cut"])
    assert result.exit_code == 1 and len(jobs) == 1
    assert "error: registration marks not found" in result.output
