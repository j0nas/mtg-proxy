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
    assert len(c) == 9 and sum(x.ink for x in c) == 5
    square = {x.what: (x.x, x.y) for x in c if x.mark == "top-left"}
    assert square["the square"] == (6.0, 11.5) and square["left of the square"] == (2.25, 11.5)
    assert square["below the square"] == (6.0, 15.25)  # 1.25 mm below its bottom edge
    legs = {(x.mark, x.what): (x.x, x.y) for x in c}
    assert legs[("top-right", "the vertical leg")] == (206.5, 12)
    assert legs[("bottom-left", "the horizontal leg")] == (6.5, 288)


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


def test_the_square_on_a_good_sheet_reads_a_quarter_mm_left_of_true():
    # The laminated lands sheet's square across x on 2026-10-25, -2.5..7.5 mm from its near edge.
    levels = [161, 168, 176, 196, *[255] * 11, 253, 238, 232, 228, 230, 236]
    seen = sensing.see(sensing.square_rows(FRAME)[0], levels)
    assert (seen.peak, seen.shift) == (255, -0.25)
    assert not any("square" in line for line in sensing.verdict([seen], FRAME))  # nothing to warn about


def test_a_faint_leg_on_bright_paper_is_found_and_flagged():
    # The double-sided MDFC sheet's bottom-left vertical leg on 2026-10-26, its row centred 1.5 mm
    # right, where the square said the sheet sat.
    row = next(r for r in sensing.leg_rows(FRAME, dx=1.5) if r.mark == "bottom-left L" and r.axis == "x")
    seen = sensing.see(row, [110, 107, 94, 163, 239, 149, 116, 118, 118])
    assert (seen.peak, seen.shift) == (239, 1.5) and seen.paper < 135
    lines = sensing.verdict([seen], FRAME)
    assert any("never reads as full ink (peak 239" in line for line in lines)
    assert any("unusually bright" in line for line in lines)


def test_a_mark_at_the_end_of_its_row_has_no_position():
    row = sensing.leg_rows(FRAME)[0]
    seen = sensing.see(row, [190, 188, 192, 193, 197, 201, 200, 251, 255])  # the leg runs off the row
    assert seen.shift is None
    assert any("no clear mark within the row" in line for line in sensing.verdict([seen], FRAME))


def test_the_verdict_names_placement_turn_and_size():
    def at(row, shift):  # a sharp mark whose middle sits ``shift`` mm off
        levels = [255 if abs(p.offset - (row.middle + shift)) <= (2.5 if row.mark == "square" else 0.3) else 170
                  for p in row.points]  # fmt: skip
        return sensing.see(row, levels)

    sq = sensing.square_rows(FRAME)
    seen = [at(sq[0], 1.5), at(sq[1], 0.0)]
    legs = sensing.leg_rows(FRAME, dx=1.5, dy=0.0)
    seen += [at(r, 1.5 if r.axis == "x" else 0.0) for r in legs]  # moved, not turned or stretched
    lines = sensing.verdict(seen, FRAME)
    assert lines[0] == "the sheet sits 1.50 mm right and 0.00 mm down of where its marks should be"
    assert lines[1].startswith("not turned, by the left edge") and lines[2].startswith(
        "not turned, by the top"
    )
    assert lines[3:5] == ["square to top-right L: 203.00 mm across (printed 203)",
                          "square to bottom-left L: 279.00 mm down (printed 279)"]  # fmt: skip
    assert len(lines) == 5  # sharp, full-ink marks on ordinary paper: nothing to warn about
