"""Our driver's session against a scripted machine: registration retries, the guaranteed return to
the origin, preflight refusals, bounds and cut pacing."""

import pytest

from mtgproxy.cameo import driver, geometry, protocol, sensing, session
from mtgproxy.cameo.transport import RecordingTransport

FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
FOUND, NOT_FOUND = b"    0\x03", b"    1\x03"


def machine(overrides=None):
    answers = {
        b"\x1b\x05": b"0\x03",
        b"FG": driver.FIRMWARE,
        b"TB123": FOUND,
        b"TB122": FOUND,
        **(overrides or {}),
    }
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
    result = driver.cut(job(), t, log(), [driver.default_start(FRAME)])
    out = sent(t)
    ending = "TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03"
    # Studio's order: prepare, describe + scan, then the blade inside the frame, cut, go home.
    assert out.index("TR0,1") < out.index("TB123,4000,5740,140,20\x03TB99") < out.index("FX20,1")
    assert out.index("FX20,1") < out.index("D") < out.rindex(ending)
    assert "Z4140,5880\x03" in out  # the cutting area reaches the paper's far edges (+2 mm)
    assert "SO0" not in out
    assert result.start == (7, 1)  # 2 mm into the 5 mm deck box's square, 1 mm from the edge


def test_a_status_reply_during_the_scan_is_not_mistaken_for_its_result():
    t = machine({b"TB123": b"1\x03"})  # "moving" arrives first ...
    write = t.write

    def then_found(data):
        write(data)
        if b"TB123" in data:
            t.pending.append(FOUND)  # ... then the padded result

    t.write = then_found
    assert driver.cut(job(), t, log(), [(2.5, 6.5)]).start == (2.5, 6.5)


def test_a_missed_scan_is_retried_from_the_next_start():
    t = machine({b"TB123": [NOT_FOUND, FOUND]})
    result = driver.cut(job(), t, log(), [(2.5, 2.5), (0.0, 0.0)])
    out = sent(t)
    assert out.count("TB123,") == 2 and "TB123,4000,5740,0,0\x03" in out
    assert out.count("TB52,2\x03") == 2  # the mark description is re-sent before each scan
    assert result.start == (0.0, 0.0)


def test_when_no_start_finds_the_marks_nothing_is_cut_but_the_head_still_goes_home():
    t = machine({b"TB123": NOT_FOUND})
    with pytest.raises(session.CutError, match="couldn't find the marks"):
        driver.cut(job(), t, log(), [(2.5, 2.5), (0.0, 0.0)])
    out = sent(t)
    assert "D" not in out.split("TB123")[-1].split("L0")[0]  # no draw after the last scan
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
    driver.cut(job([geometry.Polyline(((10, 2.5), (40, 2.5)))]), t, log(), [driver.default_start(FRAME)])
    assert "\\-40,0\x03Z4140,5880\x03" in sent(t)  # page y 2.5 → -1.5 commanded (inset 5, +1 bias), 0.5 past
    t = machine()
    driver.cut(job(), t, log(), [driver.default_start(FRAME)])
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


# --- watching the mat between sheets ------------------------------------------------
def test_a_reload_is_the_mat_going_out_then_coming_back_ready():
    statuses = [b"0\x03", b"0\x03", b"2\x03", b"2\x03", b"0\x03"]  # in, out, back in
    t = machine({b"\x1b\x05": statuses})
    waits = []
    assert driver.wait_for_reload(t, log(), lambda s: waits.append(s) or False)
    assert len(waits) == 4 and sent(t) == "\x1b\x05" * 5  # only the status query, nothing moves


def test_a_mat_that_never_leaves_is_no_reload():
    t = machine()  # "ready" throughout: the sheet just cut is still in
    polls = iter([False, False, True])  # the operator gives up on the third wait
    assert not driver.wait_for_reload(t, log(), lambda s: next(polls))


def test_sensing_moves_through_the_registered_frame_and_reads_each_point_without_cutting():
    points = [
        sensing.Point("square", FRAME.inset + 2.5, FRAME.top + 2.5, 0.0),
        sensing.Point("paper", 1.0, 2.0, 0.0),
    ]
    t = machine({b"TB60": b"    0\x03", b"TB62": b"  120\x03", b"TB63": [b"  180\x03", b"   35\x03"]})
    report = driver.sense(FRAME, t, log(), [(7, 1)], points)
    out = sent(t)
    assert [level for _, level in report["readings"]] == [180, 35]
    readings = out.replace("\x1b\x05", "")  # without the status polls between a move and its reading
    assert readings.index("TB123") < readings.index("M50,50\x03TB63,4\x03")
    assert readings.index("M50,50\x03TB63,4\x03") < readings.index("M-60,-80\x03TB63,4\x03")
    assert "\\-70,-90\x03" in out  # the area reaches the paper point left of and above the square
    assert "D" not in out.replace("TB", "")  # no draw commands at all
    assert readings.endswith("TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03")


def test_sensing_refuses_when_the_scan_restores_its_light_from_the_slot_it_would_overwrite():
    t = machine({b"TB60": b"    4\x03", b"TB62": b"  120\x03"})
    with pytest.raises(session.CutError, match="light slot reads 4"):
        driver.sense(FRAME, t, log(), [(7, 1)], sensing.profiles(FRAME))
    assert "TB63" not in sent(t) and "TB123" not in sent(t)


INK, PAPER = b"  255\x03", b"  180\x03"
SQUARE_OK = [INK, PAPER, PAPER, PAPER, PAPER]  # its middle, then left, right, above, below it
MARKS_OK = SQUARE_OK + [INK] * 4  # then the top-right L's two legs' centre lines, then the bottom-left's
FLAT = [PAPER] * 4  # before the scan, after the placement: blank paper above the square, mid-top, in each L


def checked(readings):
    return machine({b"TB60": b"    0\x03", b"TB63": readings})


def test_a_frame_the_print_agrees_with_is_cut_after_nine_readings():
    t = checked([INK, *FLAT, *MARKS_OK])  # the placement reading first, then the blank paper
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t)
    assert out.count("TB63,4") == 1 + 4 + 9 and out.count("TB123") == 1
    assert out.index("TB60") < out.index("TB63,4") < out.index("TB123")  # slot, placement, scan
    assert "\\-35,-35\x03" in out  # the area reaches the paper points 1.25 mm left of and above the square
    assert out.rindex("TB63,4") < out.index("D")  # every reading before the first cut


def test_a_misread_rescans_and_cuts_once_the_marks_agree():
    misread = [PAPER, *SQUARE_OK[1:], INK, INK, INK, INK]  # the square's middle reads paper
    t = checked([INK, *FLAT, *misread, *MARKS_OK])
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t)
    assert out.count("TB123") == 2 and "D" in out.replace("TB", "")


def test_a_misread_on_every_scan_stops_before_anything_is_cut():
    t = checked([INK, PAPER])  # every reading after the placement is paper
    with pytest.raises(session.CutError, match="after 3 scans: nothing was cut"):
        driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t)
    assert out.count("TB123") == 2 and out.count("TB122") == 1  # three-mark twice, then two-mark
    assert "D" not in out.replace("TB", "")
    assert out.endswith("TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03\x1b\x05")


def test_a_sheet_without_ink_under_the_square_is_never_scanned():
    t = checked([PAPER])
    with pytest.raises(session.CutError, match="the sheet isn't where its marks should be"):
        driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    assert "TB123" not in sent(t)


def test_a_leg_centre_that_misses_by_a_hair_is_read_a_quarter_mm_either_side():
    nudged = [b"  240\x03", INK]  # the centre in between, the first nudge on ink
    t = checked([INK, *FLAT, *SQUARE_OK, *nudged, INK, INK, INK])
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t).replace("\x1b\x05", "")
    assert "M94,5735\x03TB63,4" in out  # the top-right vertical leg, 0.25 mm left of its centre line
    assert "D" in out.replace("TB", "")


def test_the_light_slot_the_scan_uses_is_never_overwritten():
    t = machine({b"TB60": b"    4\x03"})
    with pytest.raises(session.CutError, match="light slot reads 4"):
        driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    assert "TB63" not in sent(t) and "TB123" not in sent(t)


def test_the_scan_and_the_next_sheet_get_the_scans_own_light_back_after_every_reading():
    t = checked([INK, *FLAT, *MARKS_OK])
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t).replace("\x1b\x05", "")
    before = out.rindex("TB63,4", 0, out.index("TB123"))  # the last reading before the scan
    assert before < out.index("TB61,0", before) < out.index("TB123")  # TB63 leaves its light on
    last = out.rindex("TB63,4")
    assert last < out.index("TB61,0", last) < out.index("D")


def test_two_failed_three_mark_scans_fall_back_to_two_marks_and_cut_when_the_check_agrees():
    t = machine(
        {b"TB60": b"    0\x03", b"TB63": [INK, *FLAT, *MARKS_OK], b"TB123": NOT_FOUND, b"TB122": FOUND}
    )
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t)
    assert out.count("TB123") == 2 and out.count("TB122,4000,5740") == 1
    assert out.rindex("TB63,4") < out.index(
        "D"
    )  # the top-right L read before the cut, though TB122 skipped it


def test_without_the_check_a_missed_scan_never_falls_back_to_two_marks():
    t = machine({b"TB123": NOT_FOUND, b"TB122": FOUND})
    with pytest.raises(session.CutError, match="couldn't find the marks"):
        driver.cut(job(), t, log(), [driver.default_start(FRAME)])
    assert "TB122" not in sent(t)


FAINT = [b"  189\x03", b"  177\x03", b"  168\x03", b"  130\x03", b"  125\x03"]  # centre, nudges, sides


def test_a_faint_leg_passes_when_it_is_darker_than_the_paper_on_both_sides():
    t = checked([INK, *FLAT, *SQUARE_OK, INK, INK, *FAINT, INK])  # the bottom-left vertical leg on 2026-10-26
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    assert "D" in sent(t).replace("TB", "")


def test_a_shadow_edge_dark_on_one_side_only_is_no_leg():
    edge = [b"  215\x03", b"  220\x03", b"  205\x03", b"  230\x03", b"  180\x03"]
    t = checked([INK, *FLAT, *[*SQUARE_OK, *edge, INK, INK, INK] * 3])  # the same on all three scans
    with pytest.raises(session.CutError, match="after 3 scans"):
        driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    assert sent(t).count("TB63,4") == 1 + 4 + 3 * 13  # each scan: 5 for the square, 5 for the edge, 3 legs


def test_diagnose_reads_the_square_then_legs_where_the_square_says_and_never_scans():
    sq = sensing.square_rows(FRAME)

    def mark(row, shift, width):
        return [
            b"  255\x03" if abs(p.offset - row.middle - shift) <= width else b"  170\x03" for p in row.points
        ]

    legs = sensing.leg_rows(FRAME, dx=1.0, dy=0.0)
    readings = (
        mark(sq[0], 1.0, 2.75)
        + mark(sq[1], 0.0, 2.75)
        + [r for row in legs for r in mark(row, 1.0 if row.axis == "x" else 0.0, 0.3)]
    )
    t = machine({b"TB60": b"    0\x03", b"TB63": readings})
    seen, flat = driver.diagnose(FRAME, t, log())
    out = sent(t).replace("\x1b\x05", "")
    assert [s.shift for s in seen] == [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    first = legs[0].points[0]  # the top-right vertical leg's row starts 2 mm left of where the square says
    assert (
        f"M{protocol.su(first.y)},{protocol.su(first.x)}\x03TB63,4" in out
        and first.x == FRAME.inset + FRAME.width - 1.0
    )
    assert out.count("TB63,4") == 2 * 21 + 4 * 9 + 4 and "TB123" not in out and "TB122" not in out
    assert flat.stop() is None and flat.reference == 170  # the blank paper a cut reads, last
    assert out.rindex("TB63,4") < out.rindex("TB61,0")  # the scan's own light back for whatever comes next


def test_a_sheet_that_doesnt_lie_flat_by_a_mark_is_never_scanned():
    # 2026-10-26: blank paper inside the top-right L read 255, like ink; mid-top read 171.
    t = checked([INK, PAPER, b"  171\x03", INK, PAPER])  # placement, above the square, mid-top, the L's
    with pytest.raises(session.CutError) as e:
        driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    assert str(e.value) == (
        "sheet not detected as flat (blank paper inside the top-right L: 255, mid-top: 171): nothing scanned or cut"
    )
    out = sent(t).replace("\x1b\x05", "")
    assert "TB123" not in out and "TB122" not in out and out.count("TB63,4") == 5
    assert out.rindex("TB63,4") < out.rindex("TB61,0")  # the light back for the next job, checked or not
    assert out.endswith("TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03")


def test_blank_paper_where_the_scan_moves_matches_the_page():
    t = checked([INK, *FLAT, *MARKS_OK])
    driver.cut(job(), t, log(), [driver.default_start(FRAME)], check=True)
    out = sent(t).replace("\x1b\x05", "")
    pre = out[: out.index("TB123")]  # before the scan the head moves in page mm (inset 5, top 5)
    for c in [*sensing.quiet(FRAME), sensing.reference(FRAME)]:
        assert f"M{protocol.su(c.y)},{protocol.su(c.x)}\x03TB63,4" in pre


def test_paper_that_barely_stands_out_warns_and_still_cuts():
    said = []
    t = checked([INK, PAPER, PAPER, PAPER, b"  232\x03", *MARKS_OK])  # the bottom-left L's paper at 232
    driver.cut(
        job(), t, session.SessionLog(None, echo=said.append), [driver.default_start(FRAME)], check=True
    )
    assert any(
        line.startswith("check: low contrast inside the bottom-left L (blank paper: 232") for line in said
    )
    assert "D" in sent(t).replace("TB", "")


def test_ink_on_a_mark_whose_paper_read_like_ink_proves_nothing():
    flat = sensing.Flatness(tuple(zip(sensing.quiet(FRAME), (180, 255, 180), strict=True)), 171)
    t = checked(MARKS_OK)
    failed = driver.check_marks(session.Session(t, log()), FRAME, log(), flat)
    assert failed == [
        "top-right, the vertical leg: reads ink, but the paper beside it didn't read as paper",
        "top-right, the horizontal leg: reads ink, but the paper beside it didn't read as paper",
    ]
