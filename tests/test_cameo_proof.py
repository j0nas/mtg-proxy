"""Proof cuts: where they land over the marks, what they leave uncut, and the y / r / q flow
against a scripted machine."""

import pytest

from mtgproxy.cameo import driver, geometry, proof, protocol, session
from mtgproxy.cameo.transport import RecordingTransport

FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
START = (2.5, 11.5)
FOUND = b"    0\x03"
ENDING = "TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03"
SQUARE = geometry.Polyline(((20, 20), (60, 20), (60, 60), (20, 60), (20, 20)))
# The top-left corner's L, page (8, 5) → (5, 5) → (5, 7.5), commanded from the top-left mark with
# the 1 mm y bias.
TL_L = "M20,60\x03D20,0,70,0\x03"


def machine():
    answers = {b"\x1b\x05": b"0\x03", b"FG": driver.FIRMWARE, b"TB124": FOUND}
    return RecordingTransport(lambda *a, **k: None, answers)


def job(lines=None):
    lines = lines or [SQUARE]
    return session.Job(lines, FRAME, protocol.Blade(), (0.0, 1.0), shapes=lines)


def log():
    return session.SessionLog(None)


def answers(*replies):
    asked = []

    def confirm(attempt, rescan):
        asked.append((attempt, rescan))
        return replies[len(asked) - 1]

    return confirm, asked


def pts(line):
    return [tuple(round(v, 6) for v in p) for p in line.points]


def test_each_leg_is_cut_along_its_centre_line_except_where_the_sensor_crosses_it():
    assert proof.pieces(9.4, [4.5]) == [(0, 3), (6, 9.4)]
    assert proof.pieces(9.4, [4.5, 6.5]) == [(0, 3), (8, 9.4)]  # the top-left's first search too
    assert proof.pieces(9.4, [4.5, 1.5]) == [(6, 9.4)]  # a 10 mm inset's start: no corner piece


def test_the_cuts_trace_every_mark_a_corner_l_and_the_legs_ends():
    got = {}
    for corner, cut in proof.cuts(FRAME, START):
        got.setdefault(corner, []).append(pts(cut))
    assert got["top-left"] == [
        [(8, 5), (5, 5), (5, 7.5)],  # the corner, 3 mm along the horizontal leg, 2.5 down
        [(13, 5), (14.4, 5)],  # from past the first search's line to the leg's inked end
        [(5, 11), (5, 14.4)],
    ]
    assert got["top-right"] == [
        [(289, 5), (292, 5), (292, 8)],
        [(286, 5), (282.6, 5)],
        [(292, 11), (292, 14.4)],
    ]
    assert got["bottom-right"] == [
        [(289, 205), (292, 205), (292, 202)],
        [(286, 205), (282.6, 205)],
        [(292, 199), (292, 195.6)],
    ]
    assert got["bottom-left"] == [
        [(8, 205), (5, 205), (5, 202)],
        [(11, 205), (14.4, 205)],
        [(5, 199), (5, 195.6)],
    ]


def test_a_cut_on_the_job_is_refused_but_one_in_a_hole_or_in_the_waste_is_fine():
    hole = geometry.Polyline(((30, 30), (50, 30), (50, 50), (30, 50), (30, 30)))
    shapes = [SQUARE, hole]
    cut = lambda *p: geometry.Polyline(p)  # noqa: E731
    assert proof.on_job(cut((22, 25), (25, 25)), shapes)  # on the part
    assert not proof.on_job(cut((35, 40), (45, 40)), shapes)  # inside its hole: cut away
    assert not proof.on_job(cut((5, 5), (10, 5)), shapes)  # waste
    assert proof.on_job(cut((25, 19.7), (30, 19.7)), shapes)  # 0.3 mm from the outline
    assert proof.on_job(cut((10, 40), (25, 40)), shapes)  # crosses it
    assert proof.on_job(cut((1, 1), (9, 9)), [geometry.Polyline(((1, 9), (9, 1)))])  # crosses an open cut
    assert proof.on_job(cut((10, 25), (19.7, 25), (19.7, 22)), shapes)  # an L whose corner nears the part


def test_y_cuts_the_proof_then_the_job_after_one_scan():
    t = machine()
    confirm, asked = answers("y")
    driver.cut(job(), t, log(), driver.default_starts(FRAME), confirm)
    out = t.sent.decode("latin1")
    assert asked == [(0, True)]
    assert out.count("TB124,") == 1
    # Scan, blade, proof cuts, park the head at the top edge's middle, then the job.
    assert out.index("TB124,") < out.index("FX20,1") < out.index(TL_L) < out.index("M0,2870\x03")
    assert out.index("M0,2870\x03") < out.index("M320,300") < out.rindex(ENDING)


def test_r_goes_home_and_scans_again_then_proofs_the_same_marks():
    t = machine()
    confirm, asked = answers("r", "y")
    driver.cut(job(), t, log(), driver.default_starts(FRAME), confirm)
    out = t.sent.decode("latin1")
    assert asked == [(0, True), (1, True)]
    assert out.count("TB124,") == 2 and out.count(TL_L) == 2
    first_scan, second_scan = out.index("TB124,"), out.rindex("TB124,")
    assert out.index(ENDING) < second_scan  # home between the rounds
    assert first_scan < out.index(TL_L) < second_scan < out.rindex(TL_L) < out.index("M320,300")


def test_q_cuts_nothing_more_and_goes_home():
    t = machine()
    confirm, _ = answers("q")
    with pytest.raises(session.CutError, match="stopped at the proof cuts"):
        driver.cut(job(), t, log(), driver.default_starts(FRAME), confirm)
    out = t.sent.decode("latin1")
    assert TL_L in out and "M320,300" not in out
    assert out.rstrip("\x1b\x05").endswith(ENDING)


def test_the_last_round_offers_no_rescan_and_r_there_stops():
    t = machine()
    confirm, asked = answers("r", "r", "r")
    with pytest.raises(session.CutError, match="stopped at the proof cuts"):
        driver.cut(job(), t, log(), driver.default_starts(FRAME), confirm)
    assert asked == [(0, True), (1, True), (2, False)]
    assert t.sent.decode("latin1").count("TB124,") == 3


def test_a_proof_cut_that_would_touch_the_job_is_left_out():
    near = geometry.Polyline(((12, 3), (20, 3), (20, 8), (12, 8), (12, 3)))  # over the top-left's leg end
    t = machine()
    confirm, _ = answers("y")
    driver.cut(job([near]), t, log(), driver.default_starts(FRAME), confirm)
    out = t.sent.decode("latin1")
    assert "M20,160\x03D20,188\x03" not in out  # the horizontal leg's end piece, x 13..14.4
    assert TL_L in out  # the corner's L still goes down


def test_no_answer_on_the_terminal_means_stop():
    def closed(attempt, rescan):
        raise EOFError

    t = machine()
    with pytest.raises(session.CutError, match="stopped at the proof cuts"):
        driver.cut(job(), t, log(), driver.default_starts(FRAME), closed)
