"""Placement before the scan on a sheet the sensor reads like the paper deck box's (2026-10-26):
dark paper, dim ink, and a sheet that may lie a few millimetres off where --paper-at says."""

from dataclasses import replace

import pytest

from mtgproxy.cameo import driver, geometry, protocol, sensing, session
from mtgproxy.cameo.transport import RecordingTransport

# The deck box's frame: A4 landscape, 5 mm square top-left at 5-10 mm, 9.4 mm legs.
FRAME = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200, length=9.4)
INK, PAPER = 242, 130  # what the deck box's square and blank paper read


def inked(x: float, y: float, f: session.Frame = FRAME) -> bool:
    """Page point on the square or an L leg (1 mm wide)."""
    if f.inset <= x <= f.inset + 5 and f.top <= y <= f.top + 5:
        return True
    tr_x, tr_y = f.inset + f.width, f.top
    bl_x, bl_y = f.inset, f.top + f.height
    legs = [
        (tr_x - f.length, tr_y - 0.5, tr_x + 0.5, tr_y + 0.5),
        (tr_x - 0.5, tr_y - 0.5, tr_x + 0.5, tr_y + f.length),
        (bl_x - 0.5, bl_y - 0.5, bl_x + f.length, bl_y + 0.5),
        (bl_x - 0.5, bl_y - f.length, bl_x + 0.5, bl_y + 0.5),
    ]
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in legs)


class Sheet(RecordingTransport):
    """A machine with a sheet on its mat whose paper corner lies ``at`` (right, down) mm from the
    grid's: TB63 reads what lies under the last move. Before the scan a move is in the mat's frame;
    after it, from the top-left mark, which the scan found where it is."""

    def __init__(self, at=(3.0, 3.0), ink=INK, paper=PAPER, edge=False):
        super().__init__(lambda *a, **k: None, {b"\x1b\x05": b"0\x03", b"FG": driver.FIRMWARE,
                                                b"TB123": b"    0\x03", b"TB60": b"    0\x03"})  # fmt: skip
        self.at, self.ink, self.paper, self.edge = (
            at,
            ink,
            paper,
            edge,
        )  # edge: the pouch past the paper reads like ink
        self.head, self.scanned = (0.0, 0.0), False

    def write(self, data: bytes) -> None:
        for cmd in data.split(b"\x03"):
            text = cmd.decode("latin1")
            if text.startswith("M"):
                y, x = (int(v) / 20 for v in text[1:].split(","))
                self.head = (y, x)
            if text.startswith("TB123"):
                self.scanned = True
        super().write(data)
        for cmd in data.split(b"\x03"):
            if cmd.startswith(b"TB63"):
                y, x = self.head
                if self.scanned:
                    px, py = x + FRAME.inset, y + FRAME.top
                else:
                    px, py = x - self.at[0], y - self.at[1]
                dark = inked(px, py) or (self.edge and (px < 0 or py < 0))
                self.pending.append(
                    f"{255 if dark and not inked(px, py) else self.ink if dark else self.paper:5d}\x03".encode()
                )


def job(at=(3.0, 3.0)):
    lines = [geometry.Polyline(((20, 20), (60, 20), (60, 60), (20, 60), (20, 20)))]
    return session.Job(lines, replace(FRAME, paper_at=at), protocol.Blade(), (0.0, 0.5))


def run(sheet, at=(3.0, 3.0)):
    """Cut on ``sheet`` with the paper said to be ``at``; every log event and message, in order."""
    record = []

    class Log(session.SessionLog):
        def __call__(self, event, **fields):
            record.append((event, fields))

        def say(self, message):
            record.append(("say", {"message": message}))

    j = job(at)
    driver.cut(j, sheet, Log(None), [driver.default_start(j.frame)], check=True)
    return record


def cut_once(sheet):
    return sheet.sent.count(b"TB123") == 1 and b"D" in sheet.sent.replace(b"TB", b"")


def test_dim_ink_on_dark_paper_is_ink_against_that_paper():
    assert sensing.thresholds(None) == (250, 245)
    assert sensing.thresholds(130) == (230, 229)  # the deck box's paper: 242 is ink, 180 paper
    assert sensing.thresholds(180) == (250, 245)  # proxy paper: as before
    assert sensing.thresholds(255) == (250, 245)  # paper that doesn't read as paper proves nothing
    c = sensing.placement(FRAME)
    assert c.reads(242) is False and c.reads(242, 130) is True and c.reads(150, 130) is False


def said(record) -> list[str]:
    return [f["message"] for e, f in record if e == "say"]


# How close the placement puts the square before the scan, across and down: three and four halvings
# of 6 mm (0.38 and 0.19 mm) and the moves' 0.05 mm steps.
ACROSS, DOWN = 0.4, 0.25


def near(got, want) -> bool:
    """(x, y) within ACROSS and DOWN of ``want``."""
    return abs(got[0] - want[0]) <= ACROSS and abs(got[1] - want[1]) <= DOWN


def test_a_sheet_placed_right_with_dim_ink_is_cut_without_a_search():
    sheet = Sheet()
    record = run(sheet)
    events = [e for e, _ in record]
    assert "hunt" not in events and "placement paper" in events
    assert sheet.sent.count(b"TB123") == 1 and b"D" in sheet.sent.replace(b"TB", b"")
    assert any("every mark reads where the scan put it" in m for m in said(record))


@pytest.mark.parametrize("off", [(2.0, 1.5), (-1.5, 0.0), (0.5, -2.0), (-2.2, 2.2), (2.3, -0.7)])
def test_a_sheet_within_the_square_of_where_it_should_be_is_read_where_it_lies_without_a_search(off):
    # 2026-10-28: a middle that read ink passed the frame unmoved, so the readings after it landed
    # up to 2.5 mm off; two Betor runs read the bottom-left L's own leg as its blank paper and stopped.
    at = (3.0 + off[0], 3.0 + off[1])
    sheet = Sheet(at=at)
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert near(located["paper_at"], at)
    assert not any(e == "hunt" for e, _ in record)
    assert all(f["level"] == PAPER for e, f in record if e == "quiet")  # blank paper, where it lies
    assert cut_once(sheet)


@pytest.mark.parametrize("off", [(3.0, 3.0), (-2.0, 4.0), (4.0, -1.0), (6.0, 2.0), (2.0, 5.0), (5.0, 5.0)])
def test_a_sheet_lying_further_off_is_found_and_read_where_it_lies(off):
    at = (3.0 + off[0], 3.0 + off[1])
    sheet = Sheet(at=at)
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx(at, abs=0.2)  # the hunt's measure: five halvings
    assert all(f["level"] == PAPER for e, f in record if e == "quiet")  # blank paper, where it lies
    assert cut_once(sheet)


@pytest.mark.parametrize("at", [(3.0, 3.0), (5.0, 2.0), (7.0, 6.0)])
def test_where_the_sheet_was_found_goes_to_the_log_not_the_terminal(at):
    # Jonas, 2026-10-28: a sheet lies up to about 3 mm from where it was laid against the mat's
    # edge, laminated and fed in; that is no news.
    record = run(Sheet(at=at))
    (note,) = [f["msg"] for e, f in record if e == "note" and f["msg"].startswith("square found")]
    assert note.endswith("of the mat grid's")
    assert not any("square found" in m or "paper's corner" in m for m in said(record))


def test_the_scan_starts_where_the_square_was_found():
    sheet = Sheet(at=(7.0, 5.0))  # 4 mm right of and 2 mm below where --paper-at 3 says
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    right, down = located["paper_at"]
    top, left = driver.default_start(FRAME)
    scans = [m["message"] for e, m in record if e == "say" and m["message"].startswith("marks found")]
    assert scans == [f"marks found in 0 s (scan start {top + down:g}, {left + right:g} mm)"]


def test_no_square_anywhere_near_stops_before_the_scan():
    sheet = Sheet(at=(15.0, 15.0))
    with pytest.raises(session.CutError, match=r"no square at 10\.5 mm down, 10\.5 mm in on the mat"):
        run(sheet)
    assert b"TB123" not in sheet.sent


def test_marks_that_read_dim_ink_pass_against_their_own_paper():
    sheet = Sheet(ink=238)  # every mark this dim: below INK, far above the paper
    record = run(sheet)
    checks = [f for e, f in record if e == "check"]
    assert checks and all(f["ok"] for f in checks) and cut_once(sheet)


def test_proxy_paper_keeps_the_fixed_thresholds():
    sheet = Sheet(ink=242, paper=180)  # dim ink on proxy paper is no ink: the sheet isn't trusted
    with pytest.raises(session.CutError):
        run(sheet)
    assert b"D" not in sheet.sent.replace(b"TB", b"")


def test_a_pouch_edge_that_reads_like_ink_doesnt_hide_the_square():
    sheet = Sheet(at=(6.25, 6.25), edge=True)  # the paper 3.25 mm off both ways, as on 2026-10-26
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx((6.25, 6.25), abs=0.75) and cut_once(sheet)


def test_a_middle_that_reads_paper_is_looked_for_not_passed_on_a_try_at_its_edge():
    # 2026-10-26: re-tacked 2.6 mm further left, the square's middle read 134 and a quarter mm left
    # of it 255, its edge; the sheet passed and every scan started below the square.
    sheet = Sheet(at=(0.4, 3.0))
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx((0.4, 3.0), abs=0.75) and cut_once(sheet)


class FussySheet(Sheet):
    """A sheet whose scan finds the marks only as the firmware does: its first search row has to run
    through the square at least 2 mm below its top edge and 1 mm above its bottom one, the pass that
    measures the square, starting BACKUP above that row, must start 1 mm inside the paper (the deck
    box's 5 mm square, 5 mm from the paper's edge, leaves a window a millimetre high: docs/someday.md),
    and the search starts on paper left of the square. ``finds`` False: no scan ever finds them.
    ``slips`` (right, down) mm moves the sheet as the first scan feeds the mat in and out."""

    def __init__(self, at=(3.0, 3.0), finds=True, slips=(0.0, 0.0), **kw):
        super().__init__(at=at, **kw)
        self.finds, self.slips = finds, slips
        self.answers[b"TB122"] = [b"    0\x03"]

    def write(self, data: bytes) -> None:
        ok = None
        for cmd in data.split(b"\x03"):
            if cmd.startswith((b"TB123", b"TB122")):
                if self.slips != (0.0, 0.0):
                    self.at, self.slips = (self.at[0] + self.slips[0], self.at[1] + self.slips[1]), (0.0, 0.0)
                y, x = (int(v) / 20 for v in cmd.decode().split(",")[3:5])
                top, left = self.at[1] + FRAME.top, self.at[0] + FRAME.inset
                row = max(top + 2, self.at[1] + driver.BACKUP + 1) <= y <= top + 4
                ok = self.finds and row and self.at[0] < x <= left - 1
                self.answers[cmd[:5]] = [b"    0\x03" if ok else b"    1\x03"]
        super().write(data)
        if ok is not None:
            self.scanned = ok


def test_measure_pins_the_square_to_a_fraction_of_a_millimetre():
    for off in [(0.0, 0.0), (2.0, 2.0), (-1.3, 0.7), (2.2, -2.2)]:
        sheet = Sheet(at=(3.0 + off[0], 3.0 + off[1]))
        s = session.Session(sheet, session.SessionLog(None))
        got = driver.measure(s, replace(FRAME, paper_at=(3.0, 3.0)), session.SessionLog(None), PAPER)
        assert got == pytest.approx(off, abs=0.2)


def scan_starts(record) -> list[list[float]]:
    """Each scan's start (top, left) mm, from its message."""
    out = []
    for m in said(record):
        if m.startswith("marks found") or m.startswith("scan "):
            at = m.split("scan start ")[1] if "scan start" in m else m.split(" from (")[1]
            out.append([float(v) for v in at.split(")")[0].removesuffix(" mm").split(", ")])
    return out


@pytest.mark.parametrize("at", [(3.0, 3.0), (4.5, 3.0), (4.6, 3.23), (5.27, 1.2), (1.0, 4.7), (6.2, 2.9)])
def test_the_first_scan_starts_where_the_square_lies(at):
    # 2026-10-26 and 27: three deck boxes 1.5-2.3 mm right of --paper-at 3 passed the placement
    # reading unmoved and every scan missed until the square was measured: mech-test (4.48, 3.23),
    # lorehold (5.27, 1.2), xenagos (4.48, 2.14). The scan window here is the firmware's.
    sheet = FussySheet(at=at)
    record = run(sheet)
    assert sheet.sent.count(b"TB123") == 1 and cut_once(sheet)
    top, left = driver.default_start(replace(FRAME, paper_at=at))
    ((got_top, got_left),) = scan_starts(record)
    assert near((got_left, got_top), (left, top))


def test_a_scan_that_finds_nothing_measures_the_square_and_scans_from_where_it_is():
    # The sheet moves a millimetre as the first scan feeds the mat in and out: that scan starts
    # where it lay, misses, and the square measured again puts the next one where it lies now.
    sheet = FussySheet(at=(5.0, 3.0), slips=(0.0, 1.0))
    record = run(sheet)
    (measured,) = [f for e, f in record if e == "measured"]
    assert measured["paper_at"] == pytest.approx((5.0, 4.0), abs=0.15)  # five halvings
    first, second = scan_starts(record)
    top, left = driver.default_start(replace(FRAME, paper_at=(5.0, 3.0)))
    assert near((first[1], first[0]), (left, top))
    assert second == pytest.approx(list(driver.default_start(replace(FRAME, paper_at=(5.0, 4.0)))), abs=0.15)
    assert sheet.sent.count(b"TB123") == 2
    assert b"D" in sheet.sent.replace(b"TB", b"")
    assert not any("measured" in m for m in said(record))  # the log has it, not the terminal


def test_a_square_that_measures_where_it_should_be_is_scanned_again_as_before():
    sheet = FussySheet(finds=False)
    with pytest.raises(session.CutError):
        run(sheet)
    assert sheet.sent.count(b"TB123") == 2 and sheet.sent.count(b"TB122") == 1
    assert b"D" not in sheet.sent.replace(b"TB", b"")


def test_a_sheet_where_the_frame_says_scans_once():
    sheet = FussySheet()
    record = run(sheet)
    assert not any(e in ("hunt", "measure", "measured") for e, _ in record) and cut_once(sheet)


def test_a_sheet_three_millimetres_right_is_found_in_a_few_readings():
    # 2026-10-26, Meren: the paper 6 mm in from the grid where --paper-at said 3. Rows of readings a
    # millimetre apart took 64 readings (about 4 minutes); the hunt's grid lands on the square in
    # one or two and measuring it takes nine.
    sheet = Sheet(at=(6.0, 3.0))
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx((6.0, 3.0), abs=0.2)
    readings = [e for e, _ in record if e in ("hunt", "measure")]
    assert len(readings) <= 15 and cut_once(sheet)


class LiftingSheet(Sheet):
    """After the scan, the paper within 2 mm of the square reads nearly black: the sheet lifts off
    the mat there and the sensor's reading blurs (Meren's sheet, 2026-10-26)."""

    def write(self, data: bytes) -> None:
        super().write(data)
        if self.scanned and any(cmd.startswith(b"TB63") for cmd in data.split(b"\x03")):
            y, x = self.head
            px, py = x + FRAME.inset, y + FRAME.top
            square = (FRAME.inset, FRAME.top, FRAME.inset + 5, FRAME.top + 5)
            near = square[0] - 2 <= px <= square[2] + 2 and square[1] - 2 <= py <= square[3] + 2
            if near and not inked(px, py):
                self.pending[-1] = b"  235\x03"


def test_a_sheet_lifting_by_the_square_stops_after_one_scan_and_says_so():
    sheet = LiftingSheet()
    with pytest.raises(session.CutError) as e:
        run(sheet)
    assert str(e.value) == (
        "the sheet lifts off the mat at its top-left corner (blank paper beside the square reads 235 to its "
        "left, 235 to its right, 235 above it and 235 below it, against 130 in the middle of the top "
        "margin). Press it down onto the mat, the mat on a table; if it won't stay down, retack the mat or "
        "use its other side. Reload and rerun: nothing was cut"
    )
    assert sheet.sent.count(b"TB123") == 1 and b"D" not in sheet.sent.replace(b"TB", b"")
