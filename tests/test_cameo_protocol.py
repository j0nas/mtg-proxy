"""Our driver's GP-GL strings, pinned to Silhouette Studio's own conversation with the Cameo 5 Alpha
(PacketLogger over Bluetooth, 2026-09-28: 10 mm inset A4 landscape, force 15, speed 10, depth 3)."""

import pytest

from mtgproxy.cameo import driver, session
from mtgproxy.cameo import protocol as p


def test_the_job_speaks_studios_sequence_byte_for_byte():
    assert p.PREPARE == ("TG1", "FN0", "TB50,0", "FM0", "TR0,1")
    assert p.mark_setup(9.4, 1) == ("TB99", "TB52,2", "TB51,188", "TB53,20", "TB55,1", "APS30")
    assert p.scan(124, 190, 277, 2.5, 11.5) == ("TB124,3800,5540,50,230", "TB99")
    # Studio's area is the mark frame (Z3800,5540); everything after it is verbatim.
    assert p.blade_setup(p.Blade(force=15, speed=10, depth=3), 190, 277) == (
        "\\0,0", "Z3800,5540", "J1", "FX15,1", "TJ0", "!10,1", "APS0", "FC0,1,1", "FE0,1",
        "FF1,0,1", "FF1,1,1", "FX15,1", "TJ3", "!10,1", "APS30", "FC18,1,1", "TF3,1",
    )  # fmt: skip
    assert p.RETURN_TO_ORIGIN == ("TB0", "L0", "\\0,0", "M0,0", "TR0,0", "J0", "FN0", "TB50,0")
    assert "SO0" not in p.RETURN_TO_ORIGIN  # never move the origin


def test_the_scan_starts_where_studio_starts_it():
    studio = session.Frame(page_w=297, page_h=210, inset=10, width=277, height=190)
    assert driver.default_starts(studio)[0] == (2.5, 11.5)
    deckbox = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
    assert driver.default_starts(deckbox)[0] == (2.5, 11.5)  # top on the paper, left as Studio's
    wide = session.Frame(page_w=297, page_h=210, inset=20, width=257, height=170)
    assert driver.default_starts(wide)[0] == (12.5, 21.5)


def test_only_a_padded_reply_is_a_scan_result():
    assert p.scan_result(b"    0\x03") == "found"
    assert p.scan_result(b"    1\x03") == "not found"
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
