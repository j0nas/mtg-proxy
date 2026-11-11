"""The debug sensor (a2) before the scan, and the rule that keeps the debug set safe: nothing else
this driver sends may hold a lowercase letter (protocol.UNLOCK opens a set where `q` and `m` never
return)."""

import pytest

from mtgproxy.cameo import driver, protocol, reading, session
from test_cameo_placement import FRAME, Sheet, inked, job, said

PAPER, INK = 580, 100  # a2 on laminated glossy paper: 545-615, and 65-160 on the square and legs


class A2Sheet(Sheet):
    """``Sheet`` whose pre-scan readings are a2: the head's position (``g9``, short by the firmware's
    calibration) plus the sensor's 19.2 mm. TB63 after the scan is Sheet's own. ``dark`` page points
    read like ink on blank paper: a sheet lifting there."""

    def __init__(self, *a, dark=lambda x, y: False, **kw):
        super().__init__(*a, **kw)
        self.dark, self.light, self.unlocked = dark, False, False
        self.g9 = (0.0, 0.0)

    def write(self, data: bytes) -> None:
        super().write(data)
        for cmd in data.split(b"\x03"):
            text = cmd.decode("latin1")
            if text == protocol.UNLOCK:
                self.unlocked = True
            elif text == "a0":
                self.light = True
            elif text == "a4":
                self.light = False
            elif text.startswith("g9,"):
                y, x = (int(v) / 20 for v in text[3:].split(","))
                self.g9 = (y / reading.G9_ALONG, x / reading.G9_ACROSS)
            elif text == "a2" and self.unlocked:
                y, x = self.g9
                px, py = x + reading.SENSOR_RIGHT - self.at[0], y - self.at[1]
                level = INK if inked(px, py) else self.ink_level(px, py)
                self.pending.append(f"{level if self.light else 2:5d}\x03".encode())

    def ink_level(self, px, py) -> int:
        return 140 if self.dark(px, py) else PAPER


def run(sheet, at=(3.0, 3.0), firmware=None):
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


# --- the invariant ---------------------------------------------------------------------------


def test_the_driver_sends_no_lowercase_but_debug_commands():
    for bad in ("a2", "q", "m0,0", "TB63,4x"):
        with pytest.raises(ValueError, match="lowercase"):
            protocol.cmd("M0,0", bad)
    for bad in ("q", "m1,1", "a3", "M0,0", "a1,1000", "f1", "t3,0", "FG"):
        with pytest.raises(ValueError, match="debug commands"):
            protocol.debug(bad)
    assert protocol.debug("a0", protocol.sensor_level(120), "a2", "a4", protocol.head_to(-5.25, 3)) == (
        b"a0\x03a1,120\x03a2\x03a4\x03g9,-105,60\x03"
    )
    with pytest.raises(ValueError):
        protocol.sensor_level(256)


def test_every_command_the_protocol_builds_is_uppercase():
    frame = FRAME
    built: list[str] = [
        protocol.VERSION,
        protocol.UNLOCK,
        protocol.LIGHT_SLOT,
        *protocol.PREPARE,
        *protocol.RETURN_TO_ORIGIN,
        *protocol.mark_setup(frame.length, frame.thickness),
        *protocol.scan(*frame.scan_size, 8.5, 1.0),
        *protocol.scan(*frame.scan_size, 8.5, 1.0, marks=2),
        *protocol.blade_setup(protocol.Blade(), 200.0, 290.0, -1.0, -2.0),
        *protocol.cutting_area(200.0, 290.0, -1.0, -2.0),
        protocol.move(10, 20),
        protocol.draw(10, 20),
        protocol.draw_path([(1, 2), (3, 4)]),
        protocol.select_light(0),
        protocol.light_level(1),
        protocol.sense(4),
    ]
    for c in built:
        assert not protocol.LOWERCASE.search(c), c
    for c in (protocol.INIT, protocol.STATUS):  # bare escape pairs
        assert not protocol.LOWERCASE.search(c)
    assert protocol.cmd(*built)  # and cmd takes them all


def lower(sent: bytes) -> list[str]:
    text = sent.decode("latin1").replace(protocol.STATUS, "")  # the bare status query has no ETX
    return [c for c in text.split("\x03") if protocol.LOWERCASE.search(c)]


def test_a_whole_a2_cut_sends_only_debug_commands_in_lowercase():
    sheet = A2Sheet()
    run(sheet)
    assert sheet.unlocked and cut_once(sheet)
    sent = lower(bytes(sheet.sent))
    assert sent and all(protocol.DEBUG_COMMAND.match(c) for c in sent)
    assert not [c for c in sent if c.startswith(("q", "m"))]
    after = bytes(sheet.sent)[bytes(sheet.sent).index(b"FPGRFCC1") :]
    assert protocol.UNLOCK.encode() not in after[1:]  # opened once for the connection


# --- the readings ----------------------------------------------------------------------------


def test_the_placement_reads_a2_with_the_light_on_and_never_asks_tb63_before_the_scan():
    sheet = A2Sheet()
    record = run(sheet)
    assert cut_once(sheet)
    out = bytes(sheet.sent)
    before = out[: out.index(b"TB123")]
    assert b"TB63" not in before and b"FPGRFCC1" in before and b"a1,120" in before
    assert (
        out.index(b"a0") < out.index(b"a2") < out.rindex(b"a4") < out.index(b"TB61,0\x03", out.rindex(b"a4"))
    )
    assert out.rindex(b"a4") < out.index(b"TB123")  # the light off and the scan's level back before the scan
    levels = [f["level"] for e, f in record if e in ("placement", "placement paper", "quiet")]
    assert levels and set(levels) <= {PAPER, INK}
    assert next(f for e, f in record if e == "prescan")["sensor"] is True


def test_a_reading_sends_the_head_left_of_the_point_by_the_sensors_offset_and_g9s_shortfall():
    sheet = A2Sheet()
    s = session.Session(sheet, session.SessionLog(None))
    sensor = reading.DebugSensor(s)
    sensor.sense(10.0, 8.0)
    y, x = 10.0 * reading.G9_ALONG, (8.0 - reading.SENSOR_RIGHT) * reading.G9_ACROSS
    assert bytes(sheet.sent).startswith(f"g9,{protocol.su(y)},{protocol.su(x)}\x03".encode())
    with pytest.raises(session.CutError, match=r"past the 17\.5 mm it may"):
        sensor.sense(10.0, 1.0)  # the head would go 18.2 mm left of the mat's zero


@pytest.mark.parametrize("off", [(0.0, 0.0), (2.0, 1.5), (-1.5, 0.0), (0.5, -2.0)])
def test_a_sheet_near_where_it_should_be_is_read_where_it_lies_without_a_search(off):
    at = (3.0 + off[0], 3.0 + off[1])
    sheet = A2Sheet(at=at)
    record = run(sheet)
    (located,) = [f for e, f in record if e == "located"]
    assert located["paper_at"] == pytest.approx(at, abs=0.45)
    assert not any(e == "hunt" for e, _ in record)
    assert all(f["level"] == PAPER for e, f in record if e == "quiet") and cut_once(sheet)


@pytest.mark.parametrize("off", [(3.0, 3.0), (-2.0, 4.0), (4.0, -1.0), (6.0, 2.0), (2.0, 5.0)])
def test_a_sheet_lying_further_off_is_hunted_and_measured(off):
    at = (3.0 + off[0], 3.0 + off[1])
    sheet = A2Sheet(at=at)
    record = run(sheet, at=(3.0, 3.0))
    (located,) = [f for e, f in record if e == "located"]
    assert any(e == "hunt" for e, _ in record)
    assert located["paper_at"] == pytest.approx(at, abs=0.2)
    assert all(f["level"] == PAPER for e, f in record if e == "quiet") and cut_once(sheet)


def test_no_square_anywhere_near_stops_before_the_scan():
    sheet = A2Sheet(at=(15.0, 15.0))
    with pytest.raises(session.CutError, match=r"no square at 10\.5 mm down"):
        run(sheet)
    assert b"TB123" not in sheet.sent and sheet.light is False


def test_blank_paper_that_reads_like_ink_beside_a_mark_is_a_sheet_lifting():
    # NOT measured on a lifted sheet (no a2 reading of one exists): a quiet point under half the
    # reference's level stops the cut, as TB63's ink-like blank paper did.
    f = FRAME
    sheet = A2Sheet(dark=lambda x, y: abs(x - (f.inset + f.width - 3)) < 1.5 and abs(y - (f.top + 3)) < 1.5)
    with pytest.raises(session.CutError, match=r"lifts off the mat at its top-right corner.*reads 140"):
        run(sheet)
    assert b"TB123" not in sheet.sent and sheet.light is False


def test_a_dim_square_is_ink_against_the_paper_beside_it_and_the_checks_after_the_scan_still_pass():
    sheet = A2Sheet(ink=238)  # TB63's dim ink (deck box): the scan's checks take the paper they read first
    record = run(sheet)
    checks = [f for e, f in record if e == "check"]
    assert checks and all(f["ok"] for f in checks) and cut_once(sheet)


# --- the guard -------------------------------------------------------------------------------


def test_another_firmware_keeps_tb63_and_says_so_without_opening_the_debug_set():
    sheet = Sheet()
    sheet.answers[b"FG"] = [b"CAMEO 5 ALPHA V1.05    \x03"]
    record = run(sheet)
    assert b"FPGRFCC1" not in sheet.sent and b"a2" not in sheet.sent and cut_once(sheet)
    assert any(
        m.startswith("WARNING: the sensor's fast reading is only verified on firmware V1.04")
        for m in said(record)
    )
    assert not lower(bytes(sheet.sent))


def test_no_number_from_a2_keeps_tb63_and_says_so():
    sheet = Sheet()  # answers the unlock and a2 with nothing
    record = run(sheet)
    out = bytes(sheet.sent)
    assert b"FPGRFCC1" in out and b"TB63" in out[: out.index(b"TB123")] and cut_once(sheet)
    assert any("didn't answer with a number" in m for m in said(record))
    assert out.index(b"a4") < out.index(b"TB63")  # the light off again, and the readings go on in TB63
    assert next(f for e, f in record if e == "prescan")["sensor"] is False


def test_the_dry_run_answers_a2():
    from mtgproxy.cameo.transport import RecordingTransport

    t = RecordingTransport(lambda *a, **k: None, driver.DRY_ANSWERS)
    s = session.Session(t, session.SessionLog(None))
    assert reading.DebugSensor.open(s, "CAMEO 5 ALPHA V1.04") is not None
