"""Is the sheet lifting off the mat? The rules held against every session log in output/cut that
has the readings to judge by (read out of the logs on 2026-10-28), and the stops a lifting sheet
gets: before the scan, blank paper beside a mark (sensing.Flatness) or beside the square's right
and bottom edges (driver.locate); after it, the check's paper beside the square
(sensing.lifts_by_square).

The lifted sheets: Meren's box on 2026-10-26 (225945, its corner pressed down, it cut at 230904)
and a Betor box on a worn mat on 2026-10-28 (010531 rescanned three times; 093846 is its
--diagnose: paper round the square 201-255, elsewhere 96-130; pressed down, it cut at 094916)."""

import pytest

from mtgproxy.cameo import driver, sensing, session

LIFTED = {"betor-box.20261028-010531", "meren-box.20261026-225945"}

# Every check after a scan: the log, which scan, blank paper in the middle of the top margin (None:
# read before the reading existed), the check's paper beside the square (left, right, above, below,
# 1.25 mm out), whether that scan's check passed, and whether the run went on to cut.
ROUNDS = [
    ("a4-standard-3x3.20261028-002504", 1, 119, (109, 121, 110, 118), True, True),
    ("a4-standard-3x3.20261028-003306", 1, 132, (126, 130, 129, 126), True, True),
    ("a4-standard-3x3.20261028-003823", 1, 122, (148, 140, 135, 153), True, True),
    ("a4-standard-3x3.20261028-004343", 1, 111, (101, 118, 107, 110), True, True),
    ("a4-standard-3x3.20261028-004914", 1, 118, (166, 159, 145, 170), True, True),
    ("a4-standard-3x3.20261028-005806", 1, 122, (112, 115, 107, 118), True, True),
    ("aminatou-changes.20261026-000317", 1, None, (123, 155, 137, 134), True, True),
    ("betor-box.20261028-010531", 1, 101, (255, 185, 213, 202), False, False),
    ("betor-box.20261028-010531", 2, 101, (255, 195, 216, 210), False, False),
    ("betor-box.20261028-010531", 3, 101, (255, 205, 218, 223), False, False),
    ("betor-box.20261028-094916", 1, 96, (102, 98, 94, 99), True, True),
    ("betor-side.20261026-010645", 1, None, (170, 173, 207, 166), True, True),
    ("betor-side.20261026-012616", 1, None, (222, 225, 241, 255), False, False),
    ("betor-side.20261026-012616", 2, None, (226, 235, 253, 255), False, False),
    ("betor-side.20261026-012616", 3, None, (230, 236, 255, 255), False, False),
    ("betor-side.20261026-081506", 1, None, (255, 255, 255, 255), False, False),
    ("betor-side.20261026-090205", 1, 161, (152, 160, 154, 152), True, True),
    ("box-outline.20261026-132820", 1, 103, (149, 159, 181, 136), True, True),
    ("box.20261026-121748", 1, 163, (157, 164, 160, 135), True, True),
    ("box.20261026-122950", 1, 150, (157, 160, 161, 149), True, True),
    ("box.20261026-123310", 1, 150, (153, 255, 178, 148), False, True),  # a misread: the next scans
    ("box.20261026-123310", 2, 150, (156, 216, 222, 255), False, True),  # put the frame right
    ("box.20261026-123310", 3, 150, (138, 163, 199, 149), True, True),
    ("check-only.20261025-233053", 1, None, (179, 233, 212, 169), True, False),
    ("lands-sheet-1.20261025-234727", 1, None, (178, 234, 211, 170), True, True),
    ("lorehold-box.20261026-222011", 2, 123, (174, 158, 168, 150), True, True),
    ("mdfc-sheet.20261026-001305", 1, None, (214, 206, 226, 201), False, False),
    ("mdfc-sheet.20261026-001305", 2, None, (222, 231, 223, 202), False, False),
    ("mdfc-sheet.20261026-002038", 1, None, (186, 176, 183, 149), False, False),
    ("mdfc-sheet.20261026-002038", 2, None, (174, 177, 183, 150), False, False),
    ("mech-test.20261026-160143", 2, 128, (113, 122, 123, 117), True, True),
    ("meren-box.20261026-225945", 1, 110, (219, 223, 213, 218), False, False),
    ("meren-box.20261026-225945", 2, 110, (244, 249, 227, 226), False, False),
    ("meren-box.20261026-225945", 3, 110, (241, 250, 230, 227), False, False),
    ("meren-box.20261026-230904", 1, 117, (153, 138, 144, 133), True, True),
    ("orah-box.20261026-164400", 1, 107, (181, 152, 167, 153), True, True),
    ("rocco-box.20261026-184933", 1, 105, (121, 129, 127, 117), True, True),
    ("wide-scan-proof.20261026-003407", 1, None, (149, 181, 173, 155), False, False),
    ("wide-scan-proofcut.20261026-003552", 1, None, (149, 182, 173, 156), False, True),
    ("xenagos-box.20261027-231952", 2, 129, (99, 116, 107, 102), True, True),
]


def lifts(ref, beside) -> bool:
    return sensing.lifts_by_square(dict(zip(sensing.BESIDE_SQUARE, beside, strict=True)), ref)


def test_every_scan_of_a_lifted_sheet_says_it_lifts():
    lifted = [r for r in ROUNDS if r[0] in LIFTED]
    assert len(lifted) == 6 and all(lifts(ref, beside) for _, _, ref, beside, _, _ in lifted)


def test_no_scan_of_a_sheet_that_registered_and_cut_says_it_lifts():
    cut = [r for r in ROUNDS if r[5]]
    assert len(cut) == 24
    assert not any(lifts(ref, beside) for _, _, ref, beside, _, _ in cut)


def test_the_misreads_a_rescan_put_right_are_rescanned_not_stopped():
    # box.123310 read the square's ink beside it on one side, then two: a frame off, not a lift.
    misread = [r for r in ROUNDS if r[0] == "box.20261026-123310" and not r[4]]
    assert [sum(v >= ref + sensing.LIFT for v in beside) for _, _, ref, beside, _, _ in misread] == [1, 2]
    assert not any(lifts(ref, beside) for _, _, ref, beside, _, _ in misread)


def test_the_margin_either_side_of_the_rule():
    # The third-darkest side against the top margin's paper: what LIFTED_SIDES = 3 judges by.
    def third(ref, beside):
        return sorted((v - ref for v in beside), reverse=True)[2]

    judged = [r for r in ROUNDS if r[2] is not None]
    assert min(third(r[2], r[3]) for r in judged if r[0] in LIFTED) == 101
    assert max(third(r[2], r[3]) for r in judged if r[5] and r[4]) == 46
    assert 46 < sensing.LIFT < 101


def test_without_paper_mid_top_to_judge_by_the_rule_says_nothing():
    # Before 2026-10-26 no top-margin reading was taken: those scans (betor-side's not-flat sheet
    # among them) can't be judged, and a reference that reads like ink judges nothing either.
    assert not any(lifts(ref, beside) for _, _, ref, beside, _, _ in ROUNDS if ref is None)
    assert not lifts(250, (255, 255, 255, 255))


@pytest.mark.parametrize("dx", [x / 2 for x in range(-12, 13)])
@pytest.mark.parametrize("dy", [y / 2 for y in range(-12, 13)])
def test_a_frame_off_any_way_puts_at_most_two_of_the_points_on_the_square(dx, dy):
    # The four points straddle the 5 mm square by 7.5 mm across each axis: a misread can put one of
    # each pair on ink (half a millimetre of blur included), never three.
    frame = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
    x0, y0 = frame.inset + dx, frame.top + dy  # where the square really is, the frame off by (dx, dy)
    on = [
        x0 - 0.5 <= c.x <= x0 + 5.5 and y0 - 0.5 <= c.y <= y0 + 5.5
        for c in sensing.checks(frame)
        if c.what in sensing.BESIDE_SQUARE
    ]
    assert sum(on) <= 2


# --- before the scan: blank paper beside each mark ----------------------------------------------
# The log, blank paper 3 mm above the square, mid-top, inside the top-right L and inside the
# bottom-left L, and whether the run cut.
QUIET = [
    ("a4-standard-3x3.20261028-002504", 100, 119, 130, 110, True),
    ("a4-standard-3x3.20261028-003306", 123, 132, 136, 104, True),
    ("a4-standard-3x3.20261028-003823", 131, 122, 111, 174, True),
    ("a4-standard-3x3.20261028-004343", 98, 111, 118, 114, True),
    ("a4-standard-3x3.20261028-004914", 147, 118, 112, 118, True),
    ("a4-standard-3x3.20261028-005806", 109, 122, 129, 106, True),
    ("betor-box.20261028-094916", 97, 96, 103, 103, True),
    ("betor-side.20261026-090205", 147, 161, 189, 148, True),
    ("box-outline.20261026-132820", 174, 103, 108, 100, True),
    ("box.20261026-121748", 168, 163, 151, 107, True),
    ("box.20261026-122950", 167, 150, 157, 100, True),
    ("box.20261026-123310", 223, 150, 160, 95, True),
    ("lorehold-box.20261026-222011", 169, 123, 115, 194, True),
    ("mech-test.20261026-160143", 126, 128, 126, 161, True),
    ("meren-box.20261026-225945", 109, 110, 100, 105, False),  # lifted only as the scan fed the mat
    ("meren-box.20261026-230904", 146, 117, 108, 108, True),
    ("orah-box.20261026-164400", 175, 107, 100, 215, True),
    ("rocco-box.20261026-184933", 109, 105, 111, 107, True),
    ("xenagos-box.20261027-231952", 100, 129, 117, 165, True),
]


def flatness(tl, mid, tr, bl) -> sensing.Flatness:
    frame = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
    return sensing.Flatness(tuple(zip(sensing.quiet(frame), (tl, tr, bl), strict=True)), mid)


def test_blank_paper_beside_the_marks_of_every_sheet_that_cut_reads_as_paper():
    assert all(flatness(*r[1:5]).stop() is None for r in QUIET if r[5])


def test_the_lifted_betor_corner_stops_before_the_scan_naming_its_corners():
    # 093734's readings, taken where the frame expected the sheet, 3 mm left of where it lay: 255 above
    # the square (its diagnose read 201-255 all round it) and on the bottom-left L's own leg. Read
    # where the placement finds the sheet, the square's point is 3 mm above its middle.
    stop = flatness(255, 97, 99, 255).stop()
    assert stop is not None and stop.startswith(
        "the sheet lifts off the mat at its top-left and bottom-left corners (blank paper 3 mm above the "
        "square reads 255 and inside the bottom-left L 255, like ink; mid-top: 97). Press them down"
    )
    assert "retack the mat or use its other side" in stop


# --- before the scan: the square's right and bottom edges (driver.locate) ---------------------------
FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200, paper_at=(3.0, 3.0))
EDGE = 0.33  # mm either side of an edge that a reading is part ink: 2026-10-28's measured edge, its
# readings 0.23 mm out, 0.07 out, 0.08 in and 0.4 in at 23, 39, 61 and 100 % of the way to ink


class Sheet:
    """What the sensor reads before the scan on a sheet whose square lies ``off`` (right, down) mm from
    where FRAME expects it: ``ink`` inside, ``beside`` on the paper past its right and bottom edges
    (and round it), ``above`` more than 2 mm above it, blended linearly across each edge."""

    def __init__(self, off, ink=255, beside=110, above=110, near=None, reach=0.0):
        self.off, self.ink, self.beside, self.above = off, ink, beside, above
        self.near, self.reach = near, reach  # ``near`` within ``reach`` mm of the square: a lifting corner

    def sense(self, y, x, slot):
        px = x - FRAME.paper_at[0] - self.off[0] - FRAME.inset  # from the square's top-left corner
        py = y - FRAME.paper_at[1] - self.off[1] - FRAME.top
        inside = min(px, 5 - px, py, 5 - py)  # mm inside its nearest edge, < 0 outside
        paper = self.above if py < -2 else self.beside
        if self.near is not None and inside >= -self.reach:
            paper = self.near
        f = min(1.0, max(0.0, (inside + EDGE) / (2 * EDGE)))
        level = round(paper + f * (self.ink - paper))
        return level, f"{level:5d}"


def locate(sheet):
    return driver.locate(sheet, FRAME, session.SessionLog(None))


# The sheets that registered and cut, with the paper above the square, and as the paper past its
# right and bottom edges the darker of what the check read there, everywhere past the edge.
CUT = [
    (q[1], max(r[3][1], r[3][3])) for q in QUIET for r in ROUNDS if q[5] and r[0] == q[0] and r[4] and r[5]
]


@pytest.mark.parametrize("above, beside", CUT)
def test_no_sheet_that_cut_would_stop_at_the_square_s_edges(above, beside):
    assert len(CUT) == 18
    for dx in (-2.4, -1.5, -0.5, 0.0, 0.7, 1.6, 2.4):
        for dy in (-2.4, -1.0, 0.0, 1.2, 2.4):
            found = locate(Sheet((dx, dy), beside=beside, above=above))
            if found is not None:
                assert found == pytest.approx((dx, dy), abs=0.3)


def test_paper_beside_the_square_that_reads_like_its_ink_stops_before_the_scan():
    # 6 mm right of the square's middle, where nothing is printed on any sheet: like ink.
    with pytest.raises(session.CutError) as e:
        locate(Sheet((0.4, -0.3), beside=250))
    assert str(e.value).startswith(
        "the sheet lifts off the mat at its top-left corner (blank paper 6 mm right of the square's middle "
        "reads 250, like its ink (255)). Press it down onto the mat"
    )


def test_dark_paper_round_the_square_is_measured_through_or_stops_before_the_scan():
    # The paper within 4 mm of the square reads 240, like the Betor sheet's 201-255 round its square,
    # and 200 further out. Where that ring reaches the point 6 mm right of the expected middle, it is
    # the paper the edges are judged against and the square measures where it lies (the check after
    # the scan then names the lift, sensing.lifts_by_square); where it doesn't, the ring reads as ink
    # and an edge measures past 5.3 mm from a middle that reads ink, which no square can.
    ring = {"beside": 200, "near": 240, "reach": 4.0}
    assert locate(Sheet((0.4, -0.3), **ring)) == pytest.approx((0.4, -0.3), abs=0.3)
    with pytest.raises(
        session.CutError, match=r"^the sheet lifts off the mat at its top-left corner \(blank paper "
    ):
        locate(Sheet((-1.2, 1.0), **ring))
