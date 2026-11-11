"""The sensor's own registration (``survey``) and the scan's run-ups past the print, on a machine whose
3x3 sheet lies turned, stretched and off on the mat: TB63 reads the ink under a small spot, TB123
finds, misses or misreads the marks as told, and every cut is checked where it lands on the page."""

import math
from dataclasses import replace

import pytest

from mtgproxy.cameo import driver, geometry, proof, protocol, reading, session, survey
from mtgproxy.cameo.transport import RecordingTransport

# a4-standard-3x3: portrait A4, the square 3.5 mm in and 9 mm down, 6 mm legs, nine cards.
FRAME = session.Frame(page_w=210, page_h=297, inset=3.5, width=203, height=279, length=6.0, inset_y=9.0)
CARDS = [(9.2287 + c * 64.262, 15.24 + r * 89.2387) for c in range(3) for r in range(3)]
BLEED = 0.7  # mm the engine prints each card past its outline
INK, PAPER = 255, 150
SPOT = 0.6  # mm: the side of the patch the sensor reads


def outline(x0: float, y0: float) -> geometry.Polyline:
    return geometry.Polyline(((x0, y0), (x0 + 63, y0), (x0 + 63, y0 + 88), (x0, y0 + 88), (x0, y0)))


SHAPES = [outline(x, y) for x, y in CARDS]


def ink_boxes(f: session.Frame) -> list[tuple[float, float, float, float]]:
    """Every printed rectangle, page mm: the square, each L's two legs, and the cards with their bleed."""
    x0, y0, t = f.inset, f.top, f.thickness / 2
    out = [(x0, y0, x0 + proof.SQUARE, y0 + proof.SQUARE)]
    for _, (i, j), (sx, sy) in proof.L_MARKS:
        cx, cy = x0 + i * f.width, y0 + j * f.height
        ex, ey = cx + sx * f.length, cy + sy * f.length
        out.append((min(cx - t, ex), cy - t, max(cx + t, ex), cy + t))
        out.append((cx - t, min(cy - t, ey), cx + t, max(cy + t, ey)))
    out += [(x - BLEED, y - BLEED, x + 63 + BLEED, y + 88 + BLEED) for x, y in CARDS]
    return out


class Mat(RecordingTransport):
    """A Cameo with a sheet whose paper corner lies ``at`` (x, y) mm on the mat, turned ``turn``
    degrees clockwise about it and printed ``stretch`` (x, y) times its size. Moves before a scan are
    in the mat's frame. TB12x answers ``scan``: "found" installs the frame the marks really make,
    "misread" the same with the bottom-left L ``misread`` (x, y) mm off, "missed" finds nothing and
    leaves the mat's frame. Every draw is kept as the page points the blade went down on."""

    def __init__(self, at=(1.0, -1.0), turn=0.0, stretch=(1.0, 1.0), scan="found", misread=(0.0, -5.6)):
        super().__init__(
            lambda *a, **k: None, {b"\x1b\x05": b"0\x03", b"FG": driver.FIRMWARE, b"TB60": b"    0\x03"}
        )
        self.at, self.turn, self.stretch, self.scan, self.misread = at, turn, stretch, scan, misread
        self.head = (0.0, 0.0)  # (y, x) as commanded
        self.frame_map = None  # commanded (x, y) → mat (x, y) after a scan that found the marks
        self.boxes = ink_boxes(FRAME)
        self.told: tuple[float, float] | None = None
        self.drawn: list[tuple[float, float]] = []  # page points, in order

    def mat(self, x: float, y: float) -> tuple[float, float]:
        a = math.radians(self.turn)
        u, v = x * self.stretch[0], y * self.stretch[1]
        return self.at[0] + u * math.cos(a) - v * math.sin(a), self.at[1] + u * math.sin(a) + v * math.cos(a)

    def page(self, mx: float, my: float) -> tuple[float, float]:
        a = math.radians(self.turn)
        dx, dy = mx - self.at[0], my - self.at[1]
        u, v = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
        return u / self.stretch[0], v / self.stretch[1]

    def on_mat(self, y: float, x: float) -> tuple[float, float]:
        if self.frame_map is None:
            return x, y
        return self.frame_map(x, y)

    def level(self, mx: float, my: float) -> int:
        n, hits = 5, 0
        for i in range(n):
            for j in range(n):
                px, py = self.page(mx + (i / (n - 1) - 0.5) * SPOT, my + (j / (n - 1) - 0.5) * SPOT)
                hits += any(x0 <= px <= x1 and y0 <= py <= y1 for x0, y0, x1, y1 in self.boxes)
        return round(PAPER + (INK - PAPER) * hits / n**2)

    def install(self, h: float, w: float) -> None:
        f = FRAME
        o = self.mat(f.inset, f.top)
        tr = self.mat(f.inset + f.width, f.top)
        bl = self.mat(f.inset, f.top + f.height)
        if self.scan == "misread":
            bl = (bl[0] + self.misread[0], bl[1] + self.misread[1])
        ax, ay = (tr[0] - o[0]) / w, (tr[1] - o[1]) / w
        dx, dy = (bl[0] - o[0]) / h, (bl[1] - o[1]) / h
        self.frame_map = lambda u, v: (o[0] + u * ax + v * dx, o[1] + u * ay + v * dy)

    def write(self, data: bytes) -> None:
        replies = []
        for cmd in data.split(b"\x03"):
            text = cmd.decode("latin1")
            if text.startswith("TB12"):
                h, w = (int(v) / 20 for v in text.split(",")[1:3])
                self.told = (h, w)
                self.frame_map = None
                if self.scan != "missed":
                    self.install(h, w)
                replies.append(b"    1\x03" if self.scan == "missed" else b"    0\x03")
            elif text.startswith("M") and "," in text:
                self.head = tuple(int(v) / 20 for v in text[1:].split(","))
            elif text[:1] == "D" and text[1:2] in "-0123456789" and "," in text:
                vals = [int(v) / 20 for v in text[1:].split(",")]
                for y, x in zip(vals[::2], vals[1::2], strict=True):
                    self.head = (y, x)
                    self.drawn.append(self.page(*self.on_mat(y, x)))
            elif text.startswith("TB63"):
                replies.append(f"{self.level(*self.on_mat(*self.head)):5d}\x03".encode())
        super().write(data)
        self.pending.extend(replies)


def record():
    out = []

    class Log(session.SessionLog):
        def __call__(self, event, **fields):
            out.append((event, fields))

        def say(self, message):
            out.append(("say", {"message": message}))

    return out, Log(None)


def card_job(at=(1.0, -1.0)) -> session.Job:
    """The bottom-left card's outline: the cut nearest the marks the scan misses on a turned sheet."""
    return session.Job(
        [outline(*CARDS[2])], replace(FRAME, paper_at=at), protocol.Blade(), (0.0, 0.0), shapes=SHAPES
    )


def cut(mat: Mat, at=(1.0, -1.0)):
    events, log = record()
    j = card_job(at)
    driver.cut(j, mat, log, [driver.default_start(j.frame)], check=True)
    return events


def landed(mat: Mat, tol: float) -> None:
    """Every point the blade went down on lies on the card's outline, within ``tol`` mm."""
    x0, y0 = CARDS[2]
    assert mat.drawn
    for px, py in mat.drawn:
        off = min(abs(px - x0), abs(px - x0 - 63), abs(py - y0), abs(py - y0 - 88))
        assert off <= tol, (px, py)


def said(events):
    return [f["message"] for e, f in events if e == "say"]


def test_the_scan_is_told_a_frame_whose_run_ups_start_past_the_cards_print():
    assert driver.run_ups(FRAME, SHAPES) == (5.5, 6.05)  # 283.5 mm down, 202.55 across: past 281.7 and 200.8
    assert driver.run_ups(FRAME, []) == (0.0, 0.0)
    f = replace(FRAME, run_up=(5.5, 6.0))
    assert f.scan_size == (284.5, 209.0)
    y, x = f.machine(FRAME.inset + 100, FRAME.top + 200)
    assert (y, x) == pytest.approx((200 * 284.5 / 279, 100 * 209 / 203))  # undone by the scan's own scaling


def test_a_straight_sheet_registers_by_the_scan_and_cuts_on_the_line():
    mat = Mat()
    events = cut(mat)
    assert mat.told == (284.5, 209.05)  # TB123,5690,4181
    assert not any(e == "survey" for e, _ in events)
    landed(mat, 0.05)


@pytest.mark.parametrize("turn", [0.6, -0.9, 1.1])
def test_a_turned_sheet_the_scan_misses_is_found_by_the_sensor_and_cut_on_the_line(turn):
    mat = Mat(turn=turn, scan="missed", stretch=(1.002, 0.998))
    events = cut(mat)
    (fit,) = [f for e, f in events if e == "surveyed"]
    assert fit["turn"] == pytest.approx(turn, abs=0.03)
    assert any(m.startswith("the sensor found every mark") for m in said(events))
    landed(mat, 0.15)


def test_a_frame_the_check_disagrees_with_is_surveyed_through_and_cut_on_the_line():
    # The scan takes the bottom-left card's corner for the bottom-left L, 5.6 mm above it.
    mat = Mat(turn=0.4, scan="misread")
    events = cut(mat)
    assert any(m.startswith("check: bottom-left") for m in said(events))
    assert any(e == "surveyed" for e, _ in events)
    landed(mat, 0.15)


def test_a_mark_the_sensor_cant_find_stops_before_anything_is_cut():
    mat = Mat(scan="missed")
    mat.boxes = [b for b in mat.boxes if not (b[0] < 10 and b[1] > 270)]  # no bottom-left L at all
    with pytest.raises(session.CutError, match="found no bottom-left L within 7 mm"):
        cut(mat)
    assert not mat.drawn


def test_a_fit_whose_edges_arent_square_is_refused():
    fit = session.Fit((3.5, 9.0), (0.0, 0.0), (1.0, 0.0), (math.sin(math.radians(1)), 1.0))
    assert "off square" in survey.judge(fit)
    assert survey.judge(session.Fit((3.5, 9.0), (0.0, 0.0), (1.0, 0.0), (0.0, 1.0))) is None
    assert "further apart down" in survey.judge(session.Fit((3.5, 9.0), (0.0, 0.0), (1.0, 0.0), (0.0, 1.03)))


def test_an_edge_is_pinned_between_two_readings_by_their_levels():
    ramp = lambda t: round(PAPER + (INK - PAPER) * min(1, max(0, (t - 1.7) / 0.6)))  # noqa: E731
    got = survey.edge(ramp, 3.0, INK, 1.25, PAPER, (INK + PAPER) / 2, 2)  # a leg's reach, 1.75 mm
    assert got == pytest.approx(2.0, abs=0.08)


def profile(ink_spans, blur=0.3):
    """Levels along one axis: ink over ``ink_spans`` (from, to) mm, ramping over ``blur`` either side."""

    def cover(t):
        return max(
            min(1.0, max(0.0, (min(b, t + blur) - max(a, t - blur)) / (2 * blur))) for a, b in ink_spans
        )

    return lambda t: round(PAPER + (INK - PAPER) * cover(t))


def test_the_squares_middle_comes_from_both_edges_where_the_paper_beside_it_reads_clean():
    # the first 3x3 layout: the paper's edge 3.5 mm left of the square, a pouch's band past it
    read = profile([(-20, 0.0), (3.5, 8.5)])
    assert survey.square_side(read, 6.0, INK, PAPER) == pytest.approx(6.0, abs=0.1)


def test_a_band_against_the_square_leaves_its_near_edge_to_its_size():
    read = profile([(-20, 3.0), (3.5, 8.5)])  # the band reaches to half a mm from the square
    assert survey.square_side(read, 6.0, INK, PAPER) == pytest.approx(6.0, abs=0.15)


def test_ink_that_runs_on_past_the_far_edge_is_no_square():
    read = profile([(-20, 20)])  # a pouch's band, or a card
    assert survey.square_side(read, 6.0, INK, PAPER) is None


def test_a_search_with_no_paper_round_it_gives_up_after_a_few_readings():
    # 2026-11-01: a corner that didn't lie flat read 221-255 all round its L for 4.5 minutes.
    reads = []

    def read(t):
        reads.append(t)
        return 240

    assert survey.search(read, 0.0, survey.LOST_REACH, 150, survey.LEG_WIDTH, survey.LEG_EDGE, 2) is None
    assert len(reads) <= survey.BLIND + 2 * survey.LEG_HALVINGS


def test_a_square_whose_paper_no_longer_reads_as_paper_stops_in_a_few_readings():
    # 2026-11-01, Gisa in a 125 µm pouch: paper beside the square read 135-141 before the scan and
    # 197-224 after it, the sheet lifted; the survey spent 90 s on the square before giving up.
    n = []

    def read(y, x):
        n.append((y, x))
        return 255 if abs(x - 6) < 2.5 and abs(y - 11.5) < 2.5 else 210

    with pytest.raises(
        session.CutError, match=r"lifts off the mat at its top-left corner .*where it read 135"
    ):
        survey.survey(FRAME, read, lambda x, y: (y, x), session.SessionLog(None), lambda mark: 135)
    assert len(n) <= survey.BLIND + 2 * survey.SQUARE_HALVINGS + 2


# --- registering with the debug sensor (a2) instead of the scan: ``cut --register sensor`` ----------


class A2Mat(Mat):
    """``Mat`` whose pre-scan readings are a2 (paper 580, ink 100, from ``level``'s 150-255): the head's
    place (``g9``, short by the firmware's calibration) plus the sensor's 19.2 mm. Moves, draws and the
    TB63 after a scan are Mat's. Nothing here installs a frame: a cut in sensor mode is in the mat's own."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.g9 = (0.0, 0.0)
        self.unlocked = self.light = False

    def write(self, data: bytes) -> None:
        super().write(data)
        for cmd in data.split(b"\x03"):
            text = cmd.decode("latin1")
            if text == protocol.UNLOCK:
                self.unlocked = True
            elif text in ("a0", "a4"):
                self.light = text == "a0"
            elif text.startswith("g9,"):
                y, x = (int(v) / 20 for v in text[3:].split(","))
                self.g9 = (y / reading.G9_ALONG, x / reading.G9_ACROSS)
            elif text == "a2" and self.unlocked:
                y, x = self.g9
                lvl = self.level(x + reading.SENSOR_RIGHT, y)
                a2 = round(580 - (lvl - PAPER) / (INK - PAPER) * 480)
                self.pending.append(f"{a2 if self.light else 2:5d}\x03".encode())


LAID = (3.6, 0.0)  # where the test sheet lies on the mat, about: the sensor can't reach left of 1.7 mm


def cut_by_sensor(mat: A2Mat, at=LAID):
    events, log = record()
    j = card_job(at)
    driver.cut(j, mat, log, [driver.default_start(j.frame)], check=True, register="sensor")
    return events


def test_the_sensor_registers_a_straight_sheet_without_a_scan_and_cuts_on_the_line():
    mat = A2Mat(at=LAID)
    events = cut_by_sensor(mat)
    assert b"TB123" not in mat.sent and b"TB122" not in mat.sent
    (fit,) = [f for e, f in events if e == "sensor_fit"]
    assert (fit["width"], fit["height"]) == (pytest.approx(203, abs=0.1), pytest.approx(279, abs=0.1))
    assert fit["turn"] == pytest.approx(0.0, abs=0.03) and fit["readings"] < 140
    assert "check: every mark reads where the sensor put it (17 readings)" in said(events)
    landed(mat, 0.15)
    out = bytes(mat.sent)
    assert out.rindex(b"a4") < out.index(b"D")  # the light off and the scan's level back before the cut


@pytest.mark.parametrize("kw", [{"turn": 0.5}, {"turn": -0.5, "stretch": (1.002, 0.998)}, {"at": (6.6, 3.0)}])
def test_the_sensor_registers_a_sheet_turned_or_lying_off_and_cuts_on_the_line(kw):
    mat = A2Mat(**{"at": LAID, **kw})
    events = cut_by_sensor(mat)  # told the paper is at LAID, as every cut is
    (fit,) = [f for e, f in events if e == "sensor_fit"]
    assert fit["turn"] == pytest.approx(kw.get("turn", 0.0), abs=0.05)
    assert b"TB123" not in mat.sent
    landed(mat, 0.15)


def test_a_leg_read_off_where_the_others_put_it_is_refused_with_nothing_cut():
    mat = A2Mat(at=LAID)
    # the bottom-left L's two legs printed 2.2 mm left of the square's column: the fit comes out 0.45°
    # off square, which the scan's frame would have taken as it came
    mat.boxes = [
        (x0 - 2.2, y0, x1 - 2.2, y1) if (x0 < 10 and y0 > 270) else (x0, y0, x1, y1)
        for x0, y0, x1, y1 in mat.boxes
    ]
    with pytest.raises(
        session.CutError, match=r"don't fit together: its left edge comes out .*° off square.*Nothing was cut"
    ):
        cut_by_sensor(mat)
    assert not mat.drawn and mat.light is False


def test_a_sheet_with_a_leg_missing_stops_in_plain_words():
    mat = A2Mat()
    mat.boxes = [
        b for b in mat.boxes if not (b[0] > 200 and b[1] < 20 and b[3] - b[1] > 3)
    ]  # top-right upright leg
    with pytest.raises(session.CutError, match=r"found no top-right L's upright leg within"):
        cut_by_sensor(mat)
    assert not mat.drawn


def test_the_sensor_mode_without_the_debug_sensor_says_so_before_reading_anything():
    mat = Mat()  # answers no a2
    _, log = record()
    j = card_job()
    with pytest.raises(session.CutError, match="--register sensor needs the Cameo's debug sensor"):
        driver.cut(j, mat, log, [driver.default_start(j.frame)], check=True, register="sensor")
    assert b"TB63" not in mat.sent and not mat.drawn
