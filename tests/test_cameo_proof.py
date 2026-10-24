"""Proof cuts: where they land over the marks, what they leave uncut, and the y / r / q flow
against a scripted machine."""

import pytest

from mtgproxy.cameo import driver, geometry, proof, protocol, session
from mtgproxy.cameo.transport import RecordingTransport

FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
FOUND = b"    0\x03"
ENDING = "TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03"
SQUARE = geometry.Polyline(((20, 20), (60, 20), (60, 60), (20, 60), (20, 20)))
# The 5 mm square's outline, page (5, 5) → (10, 5) → (10, 10) → (5, 10), commanded from the top-left
# mark with the 1 mm y bias.
SQ = "M20,0\x03D20,100,120,100,120,0,20,0\x03"
# The top-right corner's L, page (289, 5) → (292, 5) → (292, 8).
TR_L = "M20,5680\x03D20,5740,80,5740\x03"


def machine():
    answers = {b"\x1b\x05": b"0\x03", b"FG": driver.FIRMWARE, b"TB123": FOUND}
    return RecordingTransport(lambda *a, **k: None, answers)


def job(lines=None):
    lines = lines or [SQUARE]
    return session.Job(lines, FRAME, protocol.Blade(), (0.0, 1.0), shapes=lines)


def log():
    return session.SessionLog(None)


def starts():
    return [driver.default_start(FRAME)]


def answers(*replies):
    asked = []

    def confirm(attempt, rescan):
        asked.append((attempt, rescan))
        return replies[len(asked) - 1]

    return confirm, asked


def pts(line):
    return [tuple(round(v, 6) for v in p) for p in line.points]


def test_each_leg_is_cut_along_its_centre_line_except_where_the_sensor_crosses_it():
    assert proof.pieces(9.4, 4.5) == [(0, 3), (6, 9.4)]
    assert proof.pieces(6, 3) == [(0, 1.5), (4.5, 6)]  # a 3x3 sheet's 6 mm legs
    assert proof.pieces(9.4, 1.5) == [(3, 9.4)]  # a crossing 1.5 mm along: no corner piece
    assert proof.crossing(FRAME) == 4.5  # half the 9 mm the machine keeps of a 9.4 mm leg


def test_the_cuts_trace_the_square_and_each_l_a_corner_and_the_legs_ends():
    got = {}
    for corner, cut in proof.cuts(FRAME):
        got.setdefault(corner, []).append(pts(cut))
    assert got["top-left"] == [[(5, 5), (10, 5), (10, 10), (5, 10), (5, 5)]]  # the 5 mm square's edge
    assert got["top-right"] == [
        [(289, 5), (292, 5), (292, 8)],  # the corner, 3 mm along each leg
        [(286, 5), (282.6, 5)],  # from past the sensor's crossing to the leg's inked end
        [(292, 11), (292, 14.4)],
    ]
    assert got["bottom-left"] == [
        [(8, 205), (5, 205), (5, 202)],
        [(11, 205), (14.4, 205)],
        [(5, 199), (5, 195.6)],
    ]
    assert set(got) == {"top-left", "top-right", "bottom-left"}  # no bottom-right mark


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
    driver.cut(job(), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert asked == [(0, True)]
    assert out.count("TB123,") == 1
    # Scan, blade, proof cuts, park the head at the top edge's middle, then the job.
    assert (
        out.index("TB123,") < out.index("FX20,1") < out.index(SQ) < out.index(TR_L) < out.index("M0,2870\x03")
    )
    assert out.index("M0,2870\x03") < out.index("M320,300") < out.rindex(ENDING)


def test_r_goes_home_and_scans_again_then_proofs_the_same_marks():
    t = machine()
    confirm, asked = answers("r", "y")
    driver.cut(job(), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert asked == [(0, True), (1, True)]
    assert out.count("TB123,") == 2 and out.count(SQ) == 2
    first_scan, second_scan = out.index("TB123,"), out.rindex("TB123,")
    assert out.index(ENDING) < second_scan  # home between the rounds
    assert first_scan < out.index(SQ) < second_scan < out.rindex(SQ) < out.index("M320,300")


def test_q_cuts_nothing_more_and_goes_home():
    t = machine()
    confirm, _ = answers("q")
    with pytest.raises(session.CutError, match="stopped at the proof cuts"):
        driver.cut(job(), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert SQ in out and "M320,300" not in out
    assert out.rstrip("\x1b\x05").endswith(ENDING)


def test_the_last_round_offers_no_rescan_and_r_there_stops():
    t = machine()
    confirm, asked = answers("r", "r", "r")
    with pytest.raises(session.CutError, match="stopped at the proof cuts"):
        driver.cut(job(), t, log(), starts(), confirm)
    assert asked == [(0, True), (1, True), (2, False)]
    assert t.sent.decode("latin1").count("TB123,") == 3


def test_a_proof_cut_that_would_touch_the_job_is_left_out():
    near = geometry.Polyline(
        ((277, 3), (284, 3), (284, 8), (277, 8), (277, 3))
    )  # over the top-right's leg end
    t = machine()
    confirm, _ = answers("y")
    driver.cut(job([near]), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert "M20,5620\x03D20,5552\x03" not in out  # the horizontal leg's end piece, x 286..282.6
    assert TR_L in out and SQ in out  # the corner's L and the square still go down


CARDS = session.Frame(page_w=297, page_h=210, inset=10, width=277, height=190)


def test_a_4x2_sheet_scores_the_squares_outline_and_its_two_ls():
    got = {}
    for corner, cut in proof.cuts(CARDS):
        got.setdefault(corner, []).append(pts(cut))
    assert set(got) == {"top-left", "top-right", "bottom-left"}  # no bottom-right mark
    assert got["top-left"] == [[(10, 10), (15, 10), (15, 15), (10, 15), (10, 10)]]  # the 5 mm square's edge
    assert got["bottom-left"][0] == [(13, 200), (10, 200), (10, 197)]  # an L, crossed half a leg in


def test_a_job_scans_tb123_from_inside_the_square_and_traces_its_outline_on_the_corner():
    t = machine()
    lines = [geometry.Polyline(((30, 30), (60, 30), (60, 60), (30, 60), (30, 30)))]
    job3 = session.Job(lines, CARDS, protocol.Blade(), (0.0, 0.5), shapes=lines)
    confirm, _ = answers("y")
    driver.cut(job3, t, log(), [driver.default_start(CARDS)], confirm)
    out = t.sent.decode("latin1")
    assert "TB123,3800,5540,240,80\x03" in out  # from (12, 4): 2 mm into the square, 6 mm left of it
    assert "\\0,0\x03" in out  # the 5 mm square's outline starts on the corner line: no wider area
    assert "M10,0\x03" in out  # the outline starts at page (10, 10), 0.5 mm down with the bias


def test_no_answer_on_the_terminal_means_stop():
    def closed(attempt, rescan):
        raise EOFError

    t = machine()
    with pytest.raises(session.CutError, match="stopped at the proof cuts"):
        driver.cut(job(), t, log(), starts(), closed)
