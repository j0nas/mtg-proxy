"""Our driver's session against a scripted machine: registration retries, the guaranteed return to
the origin, preflight refusals, bounds and cut pacing."""

import pytest

from mtgproxy.cameo import driver, geometry, protocol, session
from mtgproxy.cameo.transport import RecordingTransport

FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
FOUND, NOT_FOUND = b"    0\x03", b"    1\x03"


def machine(overrides=None):
    answers = {b"\x1b\x05": b"0\x03", b"FG": driver.FIRMWARE, b"TB124": FOUND, **(overrides or {})}
    return RecordingTransport(lambda *a, **k: None, answers)


def job(lines=None, bias=(0.0, 1.0)):
    lines = lines or [geometry.Polyline(((20, 20), (60, 20), (60, 60), (20, 60), (20, 20)))]
    return session.Job(lines, FRAME, protocol.Blade(), bias)


def sent(t):
    return t.sent.decode("latin1")


def log():
    return session.SessionLog(None)


def test_a_good_job_scans_cuts_and_returns_to_the_origin_without_moving_it():
    t = machine()
    result = driver.cut(job(), t, log(), driver.default_starts(FRAME))
    out = sent(t)
    ending = "TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03"
    # Studio's order: prepare, describe + scan, then the blade inside the frame, cut, go home.
    assert out.index("TR0,1") < out.index("TB124,4000,5740,50,230\x03TB99") < out.index("FX20,1")
    assert out.index("FX20,1") < out.index("D") < out.rindex(ending)
    assert "Z4140,5880\x03" in out  # the cutting area reaches the paper's far edges (+2 mm)
    assert "SO0" not in out
    assert result.start == (2.5, 11.5)


def test_a_status_reply_during_the_scan_is_not_mistaken_for_its_result():
    t = machine({b"TB124": b"1\x03"})  # "moving" arrives first ...
    write = t.write

    def then_found(data):
        write(data)
        if b"TB124" in data:
            t.pending.append(FOUND)  # ... then the padded result

    t.write = then_found
    assert driver.cut(job(), t, log(), [(2.5, 6.5)]).start == (2.5, 6.5)


def test_a_missed_scan_is_retried_from_the_next_start():
    t = machine({b"TB124": [NOT_FOUND, FOUND]})
    result = driver.cut(job(), t, log(), [(2.5, 2.5), (0.0, 0.0)])
    out = sent(t)
    assert out.count("TB124,") == 2 and "TB124,4000,5740,0,0\x03" in out
    assert out.count("TB52,2\x03") == 2  # the mark description is re-sent before each scan
    assert result.start == (0.0, 0.0)


def test_when_no_start_finds_the_marks_nothing_is_cut_but_the_head_still_goes_home():
    t = machine({b"TB124": NOT_FOUND})
    with pytest.raises(session.CutError, match="couldn't find the marks"):
        driver.cut(job(), t, log(), [(2.5, 2.5), (0.0, 0.0)])
    out = sent(t)
    assert "D" not in out.split("TB124")[-1].split("L0")[0]  # no draw after the last scan
    assert out.endswith("TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03\x1b\x05")


def test_no_mat_means_nothing_is_sent_beyond_the_queries():
    t = machine({b"\x1b\x05": b"2\x03"})
    with pytest.raises(session.CutError, match="no mat loaded"):
        driver.cut(job(), t, log(), [(2.5, 2.5)])
    assert sent(t) == "\x1b\x04FG\x03\x1b\x05"


def test_bounds_hold_the_design_to_the_paper_and_measure_commands_from_the_top_left_mark():
    assert session.check_bounds(job()) == (15.0, 16.0, 55.0, 56.0)  # 1 mm y bias applied
    # Right at the paper's bottom edge is fine even though the bias pushes the command past it.
    edge = [geometry.Polyline(((10, 209.5), (20, 209.5)))]
    assert session.check_bounds(job(edge))[3] == pytest.approx(205.5)
    # Above/left of the top-left mark is fine as long as it stays on the paper.
    assert session.check_bounds(job([geometry.Polyline(((2, 1), (20, 1)))]))[:2] == (-3.0, -3.0)
    with pytest.raises(session.CutError, match="off the 297 x 210 mm page"):
        session.check_bounds(job([geometry.Polyline(((10, 20), (300, 20)))]))
    with pytest.raises(session.CutError, match="off the 297 x 210 mm page"):
        session.check_bounds(job([geometry.Polyline(((10, -0.5), (20, 3)))]))


def test_the_cutting_area_widens_past_the_top_left_mark_only_when_a_cut_goes_there():
    assert session.low_corner((15.0, 16.0, 55.0, 56.0)) == (0.0, 0.0)  # Studio's \\0,0
    assert session.low_corner((-3.0, -2.0, 55.0, 56.0)) == (-2.5, -3.5)  # y, x: 0.5 mm past
    t = machine()
    driver.cut(job([geometry.Polyline(((10, 2.5), (40, 2.5)))]), t, log(), driver.default_starts(FRAME))
    assert "\\-40,0\x03Z4140,5880\x03" in sent(t)  # page y 2.5 → -1.5 commanded (inset 5, +1 bias), 0.5 past
    t = machine()
    driver.cut(job(), t, log(), driver.default_starts(FRAME))
    assert "\\0,0\x03Z4140,5880\x03" in sent(t)


def test_the_cut_goes_out_in_whole_commands_of_at_most_1_kb_with_a_status_check_between():
    many = [geometry.Polyline(((10 + i, 20), (10 + i, 30))) for i in range(200)]
    t = machine()
    s = session.Session(t, log())
    s.cut(job(many))
    chunks = [c for c in t.sent.split(b"\x1b\x05") if c]
    assert len(chunks) > 1
    assert all(len(c) <= session.CHUNK_BYTES and c.endswith(b"\x03") for c in chunks)


def test_the_probe_asks_every_query_before_and_after_the_scan_and_cuts_nothing():
    t = machine({b"FQ5": [b"    0\x03", b"  -64\x03"]})  # the first real probe's values
    report = driver.probe(FRAME, t, log(), [(2.5, 2.5)])
    assert set(report["replies"]) == set(driver.PROBE_QUERIES)
    assert report["replies"]["FQ5"] == ["    0", "  -64"]
    assert "D" not in sent(t).replace("FQ", "")  # no draw commands at all


def test_each_path_is_one_move_then_multi_point_draws_without_repeated_points():
    square = geometry.Polyline(((20, 20), (60, 20), (60, 20.01), (60, 60), (20, 60), (20, 20)))
    cmds = session.cut_commands(job([square]))
    # (60, 20.01) rounds onto (60, 20) at the machine's 0.05 mm step and is dropped.
    assert cmds == ["M320,300", "D320,1100,1120,1100,1120,300,320,300"]
    long = geometry.Polyline(tuple((10 + i * 0.5, 20) for i in range(100)))
    draws = session.cut_commands(job([long]))[1:]
    assert len(draws) == 4 and all(d.count(",") + 1 <= 2 * session.POINTS_PER_DRAW for d in draws)


def test_an_oversized_command_still_goes_out_whole():
    t = machine()
    s = session.Session(t, log())
    session.CHUNK_BYTES, old = 16, session.CHUNK_BYTES
    try:
        s.cut(job([geometry.Polyline(((20, 20), (60, 20), (60, 60)))]))
    finally:
        session.CHUNK_BYTES = old
    assert b"D320,1100,1120,1100\x03" in t.sent  # longer than a chunk, sent intact
