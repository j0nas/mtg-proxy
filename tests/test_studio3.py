from __future__ import annotations

import json

import pytest

from conftest import BASE_A4, STOCK_A4, needs_stock_template
from mtgproxy import studio3
from mtgproxy.studio3 import (
    FIELDS,
    Studio3Error,
    find_records,
    patch,
    place_template,
    rd,
    read_cut_offset,
    rebase,
)


@needs_stock_template
def test_patch_shifts_only_the_geometry_fields():
    data = STOCK_A4.read_bytes()
    marks, size = find_records(data)
    assert len(marks) == 8  # 8 cards on A4
    out = patch(data, 0.0, 1.0)
    assert len(out) == len(data)
    for m in marks:
        for o in FIELDS:
            assert rd(out, m - size + o) == pytest.approx(rd(data, m - size + o) + 1.0, abs=1e-4)
            assert rd(out, m - size + o - 4) == rd(data, m - size + o - 4)  # X untouched
    # Exactly the touched floats differ: 14 Y fields per record + 1 bbox copy.
    changed = sum(1 for a, b in zip(data, out, strict=True) if a != b)
    assert 0 < changed <= (len(FIELDS) * len(marks) + 1) * 4
    assert patch(data, 0.0, 0.0) == data


@needs_stock_template
def test_patch_roundtrip_and_x_axis():
    data = STOCK_A4.read_bytes()
    shifted = patch(data, 0.5, 1.0)
    assert shifted != data
    assert patch(shifted, -0.5, -1.0) == data


@needs_stock_template
def test_rebase_recovers_stock_geometry_and_measures_offset():
    data = STOCK_A4.read_bytes()
    base, dx, dy = rebase(patch(data, 0.0, 1.0), data)
    assert (dx, dy) == (0.0, 1.0)
    assert base == data


@needs_stock_template
@pytest.mark.skipif(not BASE_A4.is_file(), reason="project template base missing")
def test_project_base_is_stock_geometry_with_studio_settings():
    """The shipped A4 base must be offset-free stock geometry (that's what rebase proves)."""
    stock, base = STOCK_A4.read_bytes(), BASE_A4.read_bytes()
    assert base != stock  # carries Studio machine/media state
    rebased, dx, dy = rebase(patch(base, 0.0, 1.0), stock)
    assert (dx, dy) == (0.0, 1.0)
    assert rebased == base


@needs_stock_template
def test_patch_refuses_unexpected_layout():
    data = bytearray(STOCK_A4.read_bytes())
    marks, size = find_records(bytes(data))
    # Break the palindrome fingerprint of the first record.
    studio3.wr(data, marks[0] - size + studio3.Y_PATH[0], 9999.0)
    with pytest.raises(Studio3Error, match="palindrome"):
        patch(bytes(data), 0.0, 1.0)
    with pytest.raises(Studio3Error, match="expected multiple Polygon records"):
        patch(b"no records here", 0.0, 1.0)


@needs_stock_template
def test_place_template_naming(tmp_path):
    assert place_template(STOCK_A4, tmp_path, 0.0, 0.0).name == "a4-standard-v5.studio3"
    assert place_template(STOCK_A4, tmp_path, 0.0, 1.0).name == "a4-standard-v5+y1mm.studio3"
    assert place_template(STOCK_A4, tmp_path, 0.5, 1.25).name == "a4-standard-v5+x0.5mm+y1.25mm.studio3"


def test_read_cut_offset(tmp_path):
    assert read_cut_offset(tmp_path / "missing.json") == (0.0, 0.0)
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"y_mm": 1.0}))
    assert read_cut_offset(p) == (0.0, 1.0)
    p.write_text("garbage")
    assert read_cut_offset(p) == (0.0, 0.0)
