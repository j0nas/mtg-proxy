"""Where the sensor reads the marks back: rows across the square and across each L leg."""

import pytest

from mtgproxy.cameo import protocol, sensing, session

FRAME = session.Frame(page_w=210, page_h=297, inset=3.5, width=203, height=279, length=6, inset_y=9)


def test_every_mark_gets_rows_across_its_edges():
    points = sensing.profiles(FRAME)
    rows = {p.profile for p in points}
    assert rows == {
        "square across x",
        "square across y",
        "top-right vertical leg across x",
        "top-right horizontal leg across y",
        "bottom-left vertical leg across x",
        "bottom-left horizontal leg across y",
    }
    assert len(points) == 2 * 21 + 4 * 17


def test_the_square_row_runs_from_paper_through_the_square_to_paper():
    row = [p for p in sensing.profiles(FRAME) if p.profile == "square across x"]
    assert (row[0].x, row[-1].x) == (1.0, 11.0)  # 2.5 mm either side of the 3.5-8.5 mm square
    assert {p.y for p in row} == {11.5}  # through its middle


def test_each_leg_is_crossed_halfway_along_it_on_its_centre_line():
    by = {p.profile: p for p in sensing.profiles(FRAME) if p.offset == 0}
    assert (by["top-right vertical leg across x"].x, by["top-right vertical leg across x"].y) == (206.5, 12)
    assert (by["top-right horizontal leg across y"].x, by["top-right horizontal leg across y"].y) == (
        203.5,
        9,
    )
    assert (by["bottom-left vertical leg across x"].x, by["bottom-left vertical leg across x"].y) == (
        3.5,
        285,
    )
    assert (by["bottom-left horizontal leg across y"].x, by["bottom-left horizontal leg across y"].y) == (
        6.5,
        288,
    )


def test_a_reading_is_registered_from_the_top_left_mark_without_the_blade_bias():
    assert sensing.registered(FRAME, sensing.Point("x", 6.0, 11.5, 0)) == (2.5, 2.5)


def test_tb63_stores_into_slots_one_to_four_only_and_replies_parse_as_numbers():
    assert protocol.sense(4) == "TB63,4"
    with pytest.raises(ValueError):
        protocol.sense(0)
    assert protocol.number(b"  180\x03") == 180
    assert protocol.number(b"   -1\x03") == -1
    assert protocol.number(b"1,2\x03") is None
    assert protocol.number(None) is None


def test_a_cut_reads_the_square_and_both_legs_of_each_l():
    c = sensing.checks(FRAME)
    assert len(c) == 17 and sum(x.ink for x in c) == 5
    square = {x.what: (x.x, x.y) for x in c if x.mark == "top-left"}
    assert square["the square"] == (6.0, 11.5) and square["left of the square"] == (2.25, 11.5)
    assert square["below the square"] == (6.0, 15.25)  # 1.25 mm below its bottom edge
    legs = {(x.mark, x.what): (x.x, x.y) for x in c}
    assert legs[("top-right", "the vertical leg")] == (206.5, 12)
    assert legs[("bottom-left", "above the horizontal leg")] == (6.5, 287)


def test_readings_between_ink_and_paper_count_as_neither():
    c = sensing.checks(FRAME)[0]
    assert (c.reads(255), c.reads(250), c.reads(247), c.reads(245), c.reads(None)) == (
        True,
        True,
        None,
        False,
        None,
    )


def test_the_placement_reading_is_the_square_middle_in_page_mm():
    p = sensing.placement(FRAME)
    assert (p.x, p.y, p.ink) == (6.0, 11.5, True)
