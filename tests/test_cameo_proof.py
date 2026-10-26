"""Proof targets: the outline a proof cut traces, which target each round takes, the proof blade,
and the y / r / q flow against a scripted machine."""

import pytest

from mtgproxy.cameo import driver, geometry, proof, protocol, session
from mtgproxy.cameo.transport import RecordingTransport

# The deck box's sheet: A4 landscape, marks 5 mm in, four 3 mm targets in a row.
TARGETS = proof.targets([(100, 80), (120, 80), (140, 80), (160, 80)])
FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200, targets=TARGETS)
FOUND = b"    0\x03"
ENDING = "TB0\x03L0\x03\\0,0\x03M0,0\x03TR0,0\x03J0\x03FN0\x03TB50,0\x03"
SQUARE = geometry.Polyline(((20, 20), (60, 20), (60, 60), (20, 60), (20, 20)))
# Target 1's edge, page (98.5, 78.5) → (101.5, 78.5) → (101.5, 81.5) → (98.5, 81.5), commanded from the
# top-left mark with the 1 mm y bias: (y, x) = (74.5, 93.5) mm, 20 steps a mm.
T1 = "M1490,1870\x03D1490,1930,1550,1930,1550,1870,1490,1870\x03"
JOB = "M320,300"  # the job's square starts at page (20, 20)
PROOF_BLADE = "FX10,1\x03TJ0\x03!5,1"  # proof.BLADE: force 10, speed 5 ...
JOB_BLADE = "FX20,1\x03TJ0\x03!25,1"  # ... and the job's own, force 20, speed 25
PARK = "M0,2870\x03"  # the head at the top edge's middle


def machine():
    answers = {b"\x1b\x05": b"0\x03", b"FG": driver.FIRMWARE, b"TB123": FOUND}
    return RecordingTransport(lambda *a, **k: None, answers)


def job(lines=None, frame=FRAME):
    lines = lines or [SQUARE]
    return session.Job(lines, frame, protocol.Blade(), (0.0, 1.0), shapes=lines)


def log():
    return session.SessionLog(None)


def starts():
    return [driver.default_start(FRAME)]


def answers(*replies):
    asked = []

    def confirm(round_, rescan):
        asked.append((round_, rescan))
        return replies[len(asked) - 1]

    return confirm, asked


def pts(line):
    return [tuple(round(v, 6) for v in p) for p in line.points]


def traced(target):
    """The commands that trace ``target``'s edge, as the job's own cut would send them."""
    return "".join(c + "\x03" for c in session.cut_commands(job([target.outline()])))


def test_a_target_is_cut_along_its_edge_and_numbered_in_the_order_given():
    assert pts(TARGETS[0].outline()) == [
        (98.5, 78.5),
        (101.5, 78.5),
        (101.5, 81.5),
        (98.5, 81.5),
        (98.5, 78.5),
    ]
    assert TARGETS[0].outline().closed
    assert [t.number for t in TARGETS] == [1, 2, 3, 4] and {t.size for t in TARGETS} == {proof.SIZE}
    assert TARGETS[1].name(4) == "target 2 of 4 (120, 80 mm)"
    assert traced(TARGETS[0]) == T1
    assert pts(proof.Target(10, 20, 4).outline())[2] == (12, 22)


def test_the_proof_blade_scores_laminate_and_stays_well_under_a_cut():
    assert (proof.BLADE.force, proof.BLADE.speed, proof.BLADE.depth) == (10, 5, 2)
    assert proof.BLADE.force < 20 and proof.BLADE.depth < 5  # the laminate preset's, the lightest cut


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


def test_y_cuts_one_target_with_the_proof_blade_then_the_job_with_its_own():
    t = machine()
    confirm, asked = answers("y")
    driver.cut(job(), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert asked == [(0, True)]
    assert out.count("TB123,") == 1
    assert out.count(T1) == 1 and traced(TARGETS[1]) not in out  # one pass, on the first target only
    # Scan, the job's blade, then the proof blade for the target, the job's blade again, park, the job.
    assert out.index("TB123,") < out.index(JOB_BLADE) < out.index(PROOF_BLADE) < out.index("TF2,1")
    assert out.index("TF2,1") < out.index(T1) < out.index(JOB_BLADE, out.index(T1)) < out.index(PARK)
    assert out.index("TF5,1", out.index(T1)) < out.index(PARK) < out.index(JOB) < out.rindex(ENDING)
    assert out.count("TF2,1") == 1  # the job itself is never cut with the proof blade


def test_r_goes_home_scans_again_and_traces_the_next_target():
    t = machine()
    confirm, asked = answers("r", "y")
    driver.cut(job(), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert asked == [(0, True), (1, True)]
    t2 = traced(TARGETS[1])
    assert out.count("TB123,") == 2 and out.count(T1) == 1 and out.count(t2) == 1
    first_scan, second_scan = out.index("TB123,"), out.rindex("TB123,")
    assert out.index(ENDING) < second_scan  # home between the rounds
    assert first_scan < out.index(T1) < second_scan < out.index(t2) < out.index(JOB)


def test_q_cuts_nothing_more_and_goes_home():
    t = machine()
    confirm, _ = answers("q")
    with pytest.raises(session.CutError, match="stopped at the proof cut"):
        driver.cut(job(), t, log(), starts(), confirm)
    out = t.sent.decode("latin1")
    assert T1 in out and JOB not in out
    assert out.rstrip("\x1b\x05").endswith(ENDING)


def test_the_last_round_offers_no_rescan_and_r_there_stops():
    t = machine()
    confirm, asked = answers("r", "r", "r")
    with pytest.raises(session.CutError, match="stopped at the proof cut"):
        driver.cut(job(), t, log(), starts(), confirm)
    assert asked == [(0, True), (1, True), (2, False)]
    out = t.sent.decode("latin1")
    assert out.count("TB123,") == 3
    assert [out.count(traced(tg)) for tg in TARGETS] == [1, 1, 1, 0]  # three rounds, three targets


def test_as_many_rounds_as_targets_are_left():
    t = machine()
    confirm, asked = answers("r")
    frame = session.Frame(297, 210, 5, 287, 200, targets=TARGETS[3:])  # the first three cut already
    with pytest.raises(session.CutError, match="stopped at the proof cut"):
        driver.cut(job(frame=frame), t, log(), starts(), confirm)
    assert asked == [(0, False)]  # one target left: no rescan
    assert traced(TARGETS[3]) in t.sent.decode("latin1")


def test_each_target_is_reported_as_it_is_cut():
    t = machine()
    confirm, _ = answers("r", "y")
    seen = []
    said = []
    driver.cut(job(), t, session.SessionLog(None, echo=said.append), starts(), confirm, on_target=seen.append)
    assert [tg.number for tg in seen] == [1, 2]
    assert "proof: target 1 of 4 (100, 80 mm) cut, one pass at force 10, speed 5, depth 2" in said


def test_a_proof_blade_of_its_own_replaces_the_default():
    t = machine()
    confirm, _ = answers("y")
    driver.cut(job(), t, log(), starts(), confirm, proof_blade=protocol.Blade(8, 3, 1))
    out = t.sent.decode("latin1")
    assert "FX8,1\x03TJ0\x03!3,1" in out and "TF1,1" in out and PROOF_BLADE not in out


def test_a_target_that_would_touch_the_job_is_left_out_and_the_next_one_taken():
    near = geometry.Polyline(((95, 75), (105, 75), (105, 85), (95, 85), (95, 75)))  # around target 1
    t = machine()
    confirm, _ = answers("y")
    said = []
    driver.cut(job([near]), t, session.SessionLog(None, echo=said.append), starts(), confirm)
    out = t.sent.decode("latin1")
    assert T1 not in out and traced(TARGETS[1]) in out
    assert "proof: target 1 of 4 (100, 80 mm) would touch the job, so it is left out" in said


def test_a_sheet_without_targets_stops_before_the_machine_moves():
    t = machine()
    confirm, asked = answers("y")
    bare = session.Frame(297, 210, 5, 287, 200)
    with pytest.raises(session.CutError, match="no proof target to cut \\(the sheet has none\\)"):
        driver.cut(job(frame=bare), t, log(), starts(), confirm)
    assert not asked and "TB123" not in t.sent.decode("latin1")


CARDS = session.Frame(
    page_w=297, page_h=210, inset=10, width=277, height=190, targets=proof.targets([(165, 12.9)])
)


def test_a_job_scans_tb123_from_inside_the_square_and_traces_a_target_inside_the_frame():
    t = machine()
    lines = [geometry.Polyline(((30, 30), (60, 30), (60, 60), (30, 60), (30, 30)))]
    job3 = session.Job(lines, CARDS, protocol.Blade(), (0.0, 0.5), shapes=lines)
    confirm, _ = answers("y")
    driver.cut(job3, t, log(), [driver.default_start(CARDS)], confirm)
    out = t.sent.decode("latin1")
    assert "TB123,3800,5540,240,80\x03" in out  # from (12, 4): 2 mm into the square, 6 mm left of it
    assert "\\0,0\x03" in out  # the target lies inside the frame: no wider area
    assert "M38,3070\x03" in out  # its edge starts at page (163.5, 11.4), 0.5 mm down with the bias


def test_no_answer_on_the_terminal_means_stop():
    def closed(round_, rescan):
        raise EOFError

    t = machine()
    with pytest.raises(session.CutError, match="stopped at the proof cut"):
        driver.cut(job(), t, log(), starts(), closed)
