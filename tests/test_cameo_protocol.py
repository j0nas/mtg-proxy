"""Our driver's GP-GL strings, pinned to Silhouette Studio's own conversation with the Cameo 5 Alpha
(PacketLogger over Bluetooth, 2026-09-28: 10 mm inset A4 landscape, force 15, speed 10, depth 3)."""

import pytest

from mtgproxy.cameo import driver, session
from mtgproxy.cameo import protocol as p


def test_the_job_speaks_studios_sequence_byte_for_byte():
    assert p.PREPARE == ("TG1", "FN0", "TB50,0", "FM0", "TR0,1")
    assert p.mark_setup(9.4, 1) == ("TB99", "TB52,2", "TB51,188", "TB53,20", "TB55,1", "APS30")
    # Studio's 4-mark scan was TB124 with these arguments; ours is the 3-mark TB123 with the same.
    assert p.scan(190, 277, 2.5, 11.5) == ("TB123,3800,5540,50,230", "TB99")
    # Studio's area is the mark frame (Z3800,5540); everything after it is verbatim.
    assert p.blade_setup(p.Blade(force=15, speed=10, depth=3), 190, 277) == (
        "\\0,0", "Z3800,5540", "J1", "FX15,1", "TJ0", "!10,1", "APS0", "FC0,1,1", "FE0,1",
        "FF1,0,1", "FF1,1,1", "FX15,1", "TJ3", "!10,1", "APS30", "FC18,1,1", "TF3,1",
    )  # fmt: skip
    assert p.RETURN_TO_ORIGIN == ("TB0", "L0", "\\0,0", "M0,0", "TR0,0", "J0", "FN0", "TB50,0")
    assert "SO0" not in p.RETURN_TO_ORIGIN  # never move the origin


SHEET_3X3 = session.Frame(page_w=210, page_h=297, inset=3.5, width=203, height=279, length=6, inset_y=9)


@pytest.mark.parametrize(
    "frame, start",
    [
        (SHEET_3X3, (11, 1)),  # registered on laminated 3x3 sheets, 2026-10-25; inset - 6 is off the paper
        (session.Frame(page_w=297, page_h=210, inset=10, width=277, height=190), (12, 4)),
        (session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200), (7, 1)),  # the deck box
    ],
    ids=["3x3", "4x2", "deck box"],
)
def test_every_scan_starts_2_mm_into_the_square_inside_the_area_searchs_window(frame, start):
    """TB123 catches the square with its top-left corner from 5 mm above the start to 30 mm below,
    and from about 1 mm to 24 mm right of it (cameo-re NOTES.md)."""
    top, left = driver.default_start(frame)
    assert (top, left) == start
    corner_x, corner_y = frame.inset, frame.top  # the square's ink: 5 mm from the insets, no stroke
    assert top - corner_y == 2  # the first row runs through the square
    assert -5 <= corner_y - top <= 30 and 1 <= corner_x - left <= 24
    assert left < corner_x  # the sweep crosses the square's left edge from white paper


def test_only_a_padded_reply_is_a_scan_result():
    assert p.scan_result(b"    0\x03") == "found"
    assert p.scan_result(b"    1\x03") == "not found"
    assert p.scan_result(b"   -1\x03") == "not found (reply -1)"  # TB123: no square in its area search
    assert p.scan_result(b"1\x03") is None  # a status reply: "moving"
    assert p.scan_result(None) is None


def test_blade_values_are_range_checked():
    with pytest.raises(ValueError):
        p.Blade(force=41)


def test_moves_are_y_then_x_in_twentieths_of_a_millimetre_rounded():
    assert p.move(17.0, 28.4) == "M340,568"
    assert p.draw(0.026, 1.024) == "D1,20"


def test_status_replies():
    assert p.status_of(b"0\x03") == "ready"
    assert p.status_of(b"2\x03") == "unloaded"
    assert p.status_of(None) == "no reply"
    assert p.status_of(b"    7\x03") == "'7'"
