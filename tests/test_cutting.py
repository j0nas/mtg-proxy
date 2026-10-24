"""cut-proxies' options: the mark frame, the stored offset, the blade presets, and the menu."""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from conftest import needs_engine
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


def frame(marks=4, inset=10.0):
    from mtgproxy.cameo import session

    return session.Frame(
        page_w=297, page_h=210, inset=inset, width=297 - 2 * inset, height=210 - 2 * inset, marks=marks
    )


def test_the_next_sheet_searches_around_where_the_last_one_registered(tmp_path):
    o = cutting.CutOptions(out_dir=tmp_path)
    starts, origin = cutting.scan_starts(o, frame())
    assert (starts[0], len(starts), origin) == ((7.5, 18), 9, "the default")
    cutting.remember_start(tmp_path, frame(), (9.5, 18))
    starts, origin = cutting.scan_starts(o, frame())
    assert starts[:3] == [(9.5, 18), (9.5, 16), (9.5, 20)] and origin == "where the last sheet registered"
    assert len(starts) == 9  # the default (7.5, 18) is already one of the neighbours
    cutting.remember_start(tmp_path, frame(), (11.5, 24))
    assert cutting.scan_starts(o, frame())[0][-1] == (7.5, 18)  # far off: the default comes last
    # A 3-mark sheet's square is found by an area search: one start, nothing remembered.
    assert cutting.scan_starts(o, frame(marks=3)) == ([(8, 4)], "the area search's corner")


def test_given_starts_are_tried_as_given(tmp_path):
    cutting.remember_start(tmp_path, frame(), (9.5, 18))
    o = cutting.CutOptions(out_dir=tmp_path, scan_starts=[(6.0, 17.0)])
    assert cutting.scan_starts(o, frame()) == ([(6.0, 17.0)], "--scan-start")


def test_a_damaged_memory_falls_back_to_the_default(tmp_path):
    (tmp_path / cutting.SCAN_START_FILE).write_text("{not json")
    assert cutting.remembered_start(tmp_path, frame()) is None
    cutting.remember_start(tmp_path, frame(), (7.5, 20))  # and is replaced
    assert cutting.remembered_start(tmp_path, frame()) == (7.5, 20)


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
    assert (o.reg_length, o.reg_thickness) == (None, 1.0)  # legs: the layout's, 9.4 on a 4x2 A4
    assert (o.layout, o.registration) == ("4x2", "4")


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


# --- the menu a bare cut-proxies shows at a terminal ---------------------------
def write_run(where, name, paper="letter", registration="3", layout="4x2", cards=8):
    from mtgproxy.sidecar import RunInfo

    where.mkdir(parents=True, exist_ok=True)
    info = RunInfo(
        name,
        paper,
        "standard",
        registration,
        cards=cards,
        fronts_only=True,
        generated="2026-10-01",
        layout=layout,
    )
    return info.write(where)


def test_a_3x3_run_is_cut_as_one(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "test-sheet", paper="a4", layout="3x3", cards=9)
    o = cut_options(monkeypatch, "--dry-run")
    assert (o.layout, o.registration, o.paper, o.reg_length, o.reg_inset) == ("3x3", "3", "a4", None, None)
    result = CliRunner().invoke(cli.app, ["cut", "--dry-run", "-r", "4"])
    assert result.exit_code == 1 and "--layout 3x3 prints 3-mark registration only" in result.output


def test_a_plain_3x3_sheet_takes_three_marks(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no run.json
    o = cut_options(monkeypatch, "--layout", "3x3", "--dry-run")
    assert (o.layout, o.registration, o.label) == ("3x3", "3", None)
    result = CliRunner().invoke(cli.app, ["cut", "--layout", "3x3", "-p", "letter", "--dry-run"])
    assert result.exit_code == 1 and "A4 paper with standard cards only" in result.output


def test_the_menu_names_a_3x3_run(tmp_path):
    from mtgproxy import cutmenu

    assert cutmenu.describe(write_run(tmp_path, "t", "a4", layout="3x3", cards=9)) == (
        "t: 9 cards, a4/standard 3x3, 3-mark"
    )


@needs_engine
def test_a_3x3_dry_run_scans_its_own_frame(tmp_path, capsys):
    """The whole job against the stand-in: the frame, leg length and SVG are the 3x3 layout's."""
    o = cutting.CutOptions(layout="3x3", registration="3", dry_run=True, out_dir=tmp_path, label="t")
    assert cutting.run_cut(o) == 0
    out = capsys.readouterr().out
    assert (
        "t — 9 cards (3x3), 3-mark registration (marks inset 3.5 mm, 9 mm top and bottom, 203 x 279 mm apart, 6 x 1 mm)"
        in out
    )
    assert "scan starts at (11, 1) mm, the area search's corner" in out
    sent = (tmp_path / "t.cmds").read_bytes().decode().split("\x03")
    # Legs 10.5 mm (TB51 in 1/20 mm), then TB123 with the frame's height and width and the start.
    assert "TB51,120" in sent and "TB123,5580,4060,220,20" in sent
    assert (tmp_path / "t.svg").read_text().count("<path ") == 9
    four = cutting.CutOptions(dry_run=True, out_dir=tmp_path, label="f")
    assert cutting.run_cut(four) == 0
    sent = (tmp_path / "f.cmds").read_bytes().decode().split("\x03")
    assert "TB51,188" in sent and "TB124,3800,5540,150,360" in sent  # the 4x2's, as before


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
    assert asked[0][1][0] == "Cut mydeck: 8 cards, letter/standard, 3-mark · laminate · Bluetooth"
    assert (o.label, o.paper, o.registration, o.connection) == ("mydeck", "letter", "3", "ble")
    assert (o.force, o.passes, o.dry_run, o.proof, o.probe) == (20, 3, False, False, False)


def test_with_no_run_here_the_default_is_a_plain_a4_sheet(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path / "otherdeck", "otherdeck")
    asked = menu(monkeypatch, "default", "done")
    o = cut_options(monkeypatch)
    assert asked[0][1][0].startswith("Cut plain A4 sheet")
    assert (o.label, o.paper, o.card_size, o.registration) == (None, "a4", "standard", "4")


def test_choose_picks_a_run_below_a_preset_and_a_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sub = write_run(tmp_path / "otherdeck", "otherdeck", paper="a4", registration="4")
    asked = menu(monkeypatch, "choose", str(sub), "paper", "proof", "done")
    o = cut_options(monkeypatch)
    assert asked[1][1][0].startswith("plain A4 sheet")  # nothing here, so plain stays the default
    assert (o.label, o.force, o.passes, o.proof, o.dry_run) == ("otherdeck", 10, 1, True, False)


def test_choose_plain_ignores_this_folders_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_run(tmp_path, "mydeck")
    asked = menu(monkeypatch, "choose", "", "laminate", "dry-run")
    o = cut_options(monkeypatch)
    assert asked[1][1][0].startswith("mydeck:")  # this folder's run stays the default
    assert (o.label, o.paper, o.registration, o.dry_run) == (None, "a4", "4", True)


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
