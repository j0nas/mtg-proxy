from __future__ import annotations

import json

import pytest
from PIL import Image

from mtgproxy import engine, trim
from mtgproxy.trim import TrimError, TrimSpec, apply_trims, parse_spec, trim_image


def test_parse_spec():
    assert parse_spec("Temple Garden") == TrimSpec("Temple Garden", 0.3)
    assert parse_spec("Temple Garden:0.5") == TrimSpec("Temple Garden", 0.5)
    assert parse_spec("all") == TrimSpec("all", 0.3)
    assert (
        parse_spec("Ratchet: Rescue Racer").name == "Ratchet: Rescue Racer"
    )  # colon without a number stays in the name
    assert parse_spec("Ratchet: Rescue Racer").key == "ratchetrescueracer"
    with pytest.raises(TrimError, match="between 0 and 3"):
        parse_spec("x:9")
    with pytest.raises(TrimError, match="empty"):
        parse_spec(":0.5")


def test_trim_image_replaces_outer_ring_with_inner_ring():
    im = Image.new("RGB", (20, 30), (200, 100, 50))
    px = im.load()
    for x in range(20):
        px[x, 0] = (0, 0, 0)  # dark scan line on top
    for y in range(30):
        px[19, y] = (255, 255, 255)  # bright rim on the right
    out = trim_image(im, 2)
    assert out.size == im.size
    o = out.load()
    assert all(o[x, y] == (200, 100, 50) for x in range(20) for y in range(30))


def _card(path, size=(745, 1040), rim=(0, 0, 0)):
    im = Image.new("RGBA", size, (170, 105, 85, 255))
    px = im.load()
    for x in range(size[0]):
        px[x, 0] = (*rim, 255)
    im.save(path)


def test_apply_trims_matches_names_and_is_idempotent(tmp_path, monkeypatch):
    front, ds, game = tmp_path / "front", tmp_path / "ds", tmp_path
    front.mkdir(), ds.mkdir()
    monkeypatch.setattr(engine, "FRONT", front)
    monkeypatch.setattr(engine, "DOUBLE_SIDED", ds)
    monkeypatch.setattr(engine, "GAME", game)
    monkeypatch.setattr(trim, "STATE_FILE", game / ".trimmed.json")
    _card(front / "12BattleAngelsofTyr1.png")
    _card(front / "84TempleGarden1.png")
    _card(front / "76SolRing1.png")
    _card(front / "10000Treasure_token1.png")
    _card(ds / "84TempleGarden1.png")
    done, unmatched = apply_trims([TrimSpec("Temple Garden", 0.5), TrimSpec("battle angels of tyr")])
    assert sorted(done) == [
        "ds/84TempleGarden1.png",
        "front/12BattleAngelsofTyr1.png",
        "front/84TempleGarden1.png",
    ]
    assert unmatched == []
    with Image.open(front / "84TempleGarden1.png") as im:
        assert im.getpixel((300, 0)) == (170, 105, 85, 255)  # rim gone
    with Image.open(front / "76SolRing1.png") as im:
        assert im.getpixel((300, 0)) == (0, 0, 0, 255)  # untouched
    assert json.loads((game / ".trimmed.json").read_text()) == {
        "front/12BattleAngelsofTyr1.png": 0.3, "front/84TempleGarden1.png": 0.5, "ds/84TempleGarden1.png": 0.5,
    }  # fmt: skip
    assert apply_trims([TrimSpec("Temple Garden", 0.5)]) == ([], [])  # already done at this width
    done, unmatched = apply_trims([TrimSpec("all"), TrimSpec("Nope")])
    assert "front/76SolRing1.png" in done and "front/10000Treasure_token1.png" not in done
    assert unmatched == ["Nope"]
