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


def test_a_sheet_placed_right_with_dim_ink_is_cut_without_a_search():
    sheet = Sheet()
    record = run(sheet)
    events = [e for e, _ in record]
    assert "search" not in events and "placement paper" in events
    assert sheet.sent.count(b"TB123") == 1 and b"D" in sheet.sent.replace(b"TB", b"")
    assert any(e == "say" and "every mark reads where the scan put it" in f["message"] for e, f in record)


@pytest.mark.parametrize("off", [(2.0, 1.5), (-1.5, 0.0), (0.5, -2.0)])
def test_a_sheet_within_the_square_of_where_it_should_be_needs_no_search(off):
    sheet = Sheet(at=(3.0 + off[0], 3.0 + off[1]))
    record = run(sheet)
    assert not any(e == "search" for e, _ in record) and cut_once(sheet)


@pytest.mark.parametrize("off", [(3.0, 3.0), (-2.0, 4.0), (4.0, -1.0), (6.0, 2.0), (2.0, 5.0), (5.0, 5.0)])
def test_a_sheet_lying_further_off_is_found_and_read_where_it_lies(off):
    at = (3.0 + off[0], 3.0 + off[1])
    sheet = Sheet(at=at)
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx(at, abs=0.75)  # a reading every millimetre
    assert all(f["level"] == PAPER for e, f in record if e == "quiet")  # blank paper, where it lies
    assert any(e == "say" and m["message"].startswith("square found") for e, m in record)
    assert cut_once(sheet)


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
    with pytest.raises(
        session.CutError, match=r"no square within 9 mm of 10\.5 mm down, 10\.5 mm in on the mat"
    ):
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


def test_the_square_is_the_whole_run_of_its_width_nearest_where_it_should_be():
    # The diagonal search row on the deck box's sheet, --paper-at 0 with the paper 3.25 mm in each
    # way (2026-10-26): the pouch's edge read like ink at the row's start, the square 4 mm along it.
    row = sensing.search_row(replace(FRAME, paper_at=(0.0, 0.0)), "d")
    levels = [255, 255, 160, 255, 181, 161, 166, 159, 245, 255, 255, 255, 254, 137, 124, 130, 119]
    assert [p.offset for p in row.points] == [float(d) for d in range(-7, 10)]  # off-mat points left out
    assert sensing.square_in(row, levels) == 3.0
    x_row = [255, 146, 122, 255, 180, 172, 163, 159, 175, 172, 164, 167, 164, 154, 153, 148, 148]
    assert sensing.square_in(sensing.search_row(replace(FRAME, paper_at=(0.0, 0.0)), "x"), x_row) is None


def test_a_middle_that_reads_paper_is_looked_for_not_passed_on_a_try_at_its_edge():
    # 2026-10-26: re-tacked 2.6 mm further left, the square's middle read 134 and a quarter mm left
    # of it 255, its edge; the sheet passed and every scan started below the square.
    sheet = Sheet(at=(0.4, 3.0))
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx((0.4, 3.0), abs=0.75) and cut_once(sheet)
