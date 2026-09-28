"""cut-proxies' mark-geometry override: the launcher's command rewrite and the argv it builds."""

from pathlib import Path

from mtgproxy import cutting, regmark_launch


def test_replacements_convert_mm_to_machine_units():
    assert regmark_launch.replacements(9.4, 1.0) == {"TB51,400": "TB51,188", "TB53,10": "TB53,20"}
    assert regmark_launch.replacements(None, 1.0) == {"TB53,10": "TB53,20"}


def test_rewrite_touches_only_the_two_mark_commands_in_strings_and_lists():
    repl = regmark_launch.replacements(9.4, 1.0)
    seen: set[str] = set()
    assert regmark_launch.rewrite("TB51,400", repl, seen) == "TB51,188"
    assert regmark_launch.rewrite(["FN0", "TB53,10"], repl, seen) == ["FN0", "TB53,20"]
    assert regmark_launch.rewrite("TB124,3800,5540,0,0", repl, seen) == "TB124,3800,5540,0,0"
    assert seen == {"TB51,400", "TB53,10"}


def test_command_is_the_plain_driver_without_overrides():
    o = cutting.CutOptions()
    argv = cutting.command(o, ["--x"], Path("cut.svg"))
    assert argv[1].endswith("sendto_silhouette.py")
    assert argv[-2:] == ["--x", "cut.svg"]


def test_command_goes_through_the_launcher_with_overrides():
    o = cutting.CutOptions(reg_length=9.4, cut_beyond=12)
    argv = cutting.command(o, ["--x"], Path("cut.svg"))
    assert argv[1] == str(cutting.LAUNCHER)
    assert argv[3:8] == ["--length", "9.4", "--beyond", "12", "--"]  # unset thickness: driver's
    assert argv[-2:] == ["--x", "cut.svg"]


def test_dry_runs_always_use_the_launcher_to_simulate_the_scan():
    argv = cutting.command(cutting.CutOptions(dry_run=True), ["--x"], Path("cut.svg"))
    assert argv[1] == str(cutting.LAUNCHER)
    assert argv[3] == "--"


def test_widen_grows_the_clip_box_right_and_down_once():
    clip = {"urx": 277.0, "ury": 0.0, "llx": 0.0, "lly": 190.0}
    regmark_launch.widen(clip, 12)
    regmark_launch.widen(clip, 12)  # the driver calls clip_point per point; widen only once
    assert (clip["urx"], clip["lly"], clip["llx"], clip["ury"]) == (289.0, 202.0, 0.0, 0.0)


def test_launcher_parses_named_options_before_the_separator():
    drv, opts, rest = regmark_launch.parse(
        ["x", "/drv", "--length", "9.4", "--beyond", "5", "--", "--a", "b"]
    )
    assert (drv, opts, rest) == ("/drv", {"length": 9.4, "beyond": 5.0}, ["--a", "b"])


def test_clip_report_reads_the_final_bounding_box_line():
    log = (
        "Final bounding box and point counts: {'clip': {'urx': 277.0, 'ury': 0.0, 'llx': 0.0, 'lly': 190.0, "
        "'count': 42}, 'only': False, 'count': 1542, 'llx': 0.0, 'urx': 278.6, 'lly': 200.35, 'ury': 1.0}"
    )
    assert cutting.clip_report(log) == (42, "x 0..278.6, y 1..200.35 mm")


def test_mark_commands_in_reads_the_announced_geometry():
    assert cutting.mark_commands_in("TB99TB52,2TB51,188TB53,20TB55,1") == ("188", "20")
    assert cutting.mark_commands_in("nothing here") == (None, None)


def test_with_inset_moves_the_marks_and_keeps_the_page():
    from mtgproxy import layout

    geom = layout.Geometry("a4", "standard", 8, 297.0, 210.0, 10.0, 277.0, 190.0, (0, 0, 1, 1))
    moved = cutting.with_inset(geom, 5)
    assert (moved.reg_inset_mm, moved.reg_x_mm, moved.reg_y_mm) == (5, 287.0, 200.0)
    assert (moved.page_w_mm, moved.page_h_mm) == (297.0, 210.0)


def geometry():
    from mtgproxy import layout

    return layout.Geometry("a4", "standard", 8, 297.0, 210.0, 10.0, 277.0, 190.0, (0, 0, 1, 1))


def test_bluetooth_is_the_default_but_dry_runs_stay_off_the_air():
    argv = cutting.driver_argv(cutting.CutOptions(), geometry(), 0, 1, "x")
    i = argv.index("--connection_type")
    assert argv[i : i + 4] == ["--connection_type", "ble", "--bluetooth_name", "CAMEO 5 ALPHA"]
    assert "--connection_type" not in cutting.driver_argv(
        cutting.CutOptions(connection="usb"), geometry(), 0, 1, "x"
    )
    assert "--connection_type" not in cutting.driver_argv(
        cutting.CutOptions(dry_run=True), geometry(), 0, 1, "x"
    )


# The driver's log when the Cameo wasn't on the chosen connection (2026-09-28): it exited 0.
NO_DEVICE_LOG = """Preventing macOS sleep while cutting.
device fallback under macosx not implemented. Help adding code!
No Graphtec Silhouette devices found.
Check USB and Power.
Devices:
 done. 0 min 0 sec
"""


def test_a_run_that_never_reached_the_cut_is_caught_with_the_drivers_reason():
    assert not cutting.reached_cut(NO_DEVICE_LOG)
    assert cutting.driver_error(NO_DEVICE_LOG) == "No Graphtec Silhouette devices found."
    unloaded = "status=unloaded\nNo media is loaded. Load media into the cutter and try again.\n done."
    assert not cutting.reached_cut(unloaded)
    assert cutting.driver_error(unloaded).startswith("No media is loaded")
    assert cutting.reached_cut("status=ready\nFinal bounding box and point counts: {}\n\nstatus=moving")


def test_run_cut_fails_instead_of_reporting_done_when_nothing_was_sent(tmp_path, monkeypatch):
    import subprocess

    import pytest

    svg = tmp_path / "cut.svg"
    svg.write_text("<svg/>")
    monkeypatch.setattr(cutting, "ensure_driver", lambda: None)
    monkeypatch.setattr(cutting.layout, "build_cut_svg", lambda *a: geometry())
    monkeypatch.setattr(cutting.studio3, "read_cut_offset", lambda: (0.0, 1.0))

    def fake_driver(argv, **kwargs):
        (tmp_path / "cut.log").write_text(NO_DEVICE_LOG)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cutting.subprocess, "run", fake_driver)
    o = cutting.CutOptions(svg=svg, out_dir=tmp_path, legacy=True)
    with pytest.raises(cutting.CutError, match="NOTHING WAS CUT over ble: No Graphtec"):
        cutting.run_cut(o)
