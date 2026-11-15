from __future__ import annotations

import pytest
from PIL import Image

from mtgproxy.testcards import H, W, write_test_cards


def test_dimensions_are_exactly_63x88mm_at_800ppi():
    assert (W, H) == (1984, 2772)


def test_write_test_cards(tmp_path):
    write_test_cards(3, tmp_path / "front")
    fronts = sorted((tmp_path / "front").iterdir())
    assert [f.name for f in fronts] == ["test_01.png", "test_02.png", "test_03.png"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["front"]  # fronts only: no back, no double-sided
    with Image.open(fronts[0]) as im:
        assert im.size == (W, H)
        assert im.info.get("dpi", (0, 0))[0] == pytest.approx(800, abs=0.01)
        # ink-light: overwhelmingly white
        white = sum(im.convert("L").histogram()[251:])
        assert white / (W * H) > 0.95
