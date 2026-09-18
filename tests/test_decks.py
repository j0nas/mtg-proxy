from __future__ import annotations

import urllib.error

import pytest

from mtgproxy import decks
from mtgproxy.decks import (
    DeckError,
    archidekt_entries,
    fetch_deck,
    match_source,
    moxfield_entries,
    normalize_board,
)


def test_archidekt_main_board_follows_primary_category(archidekt_json):
    e = archidekt_entries(archidekt_json, "main")
    assert len(e.lines) == 4
    names = [line.split(" ", 1)[1] for line in e.lines]
    assert names == sorted(names, key=str.lower)
    assert any(
        line.startswith("1 Xyris, the Writhing Storm (") for line in e.lines
    )  # commander counts as main
    assert any(
        "Mischievous Catgeist // Catlike Curiosity (VOW) 69" in line for line in e.lines
    )  # DFC full name
    assert "1 Uncategorized Card (LEA) 1" in e.lines  # no category → in deck
    assert not any("Maybe" in line or "No Printing" in line for line in e.lines)
    assert e.nonempty_boards == ["considering", "main", "side"]


def test_archidekt_side_and_considering(archidekt_json):
    assert archidekt_entries(archidekt_json, "considering").lines == ["1 Zzz Maybe Card (M21) 12"]
    # No set/collector number → bare "qty name" line, quantity preserved.
    assert archidekt_entries(archidekt_json, "side").lines == ["2 No Printing Card"]


def test_moxfield_boards(moxfield_json):
    main = moxfield_entries(moxfield_json, "main").lines
    assert main == [
        "1 Agadeem's Awakening // Agadeem, the Undercrypt (ZNR) 90",
        "1 Orah, Skyclave Hierophant (ZNR) 237",
        "1 Sol Ring (CMM) 464",
    ]
    assert moxfield_entries(moxfield_json, "considering").lines == ["1 Smothering Tithe"]
    e = moxfield_entries(moxfield_json, "side")
    assert e.lines == [] and e.nonempty_boards == ["main", "considering"]


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("https://moxfield.com/decks/AbC-12_x", ("moxfield", "AbC-12_x")),
        ("https://www.moxfield.com/decks/AbC/primer", ("moxfield", "AbC")),
        ("https://archidekt.com/decks/1585124/baby_lasagna", ("archidekt", "1585124")),
        ("https://archidekt.com/api/decks/42/", ("archidekt", "42")),
        ("decks/mydeck.txt", None),
        ("https://example.com/decks/1", None),
    ],
)
def test_match_source(ref, expected):
    got = match_source(ref)
    assert (got and (got[0].name, got[1])) == expected


def test_normalize_board_aliases_and_errors():
    assert normalize_board("Maybeboard") == "considering"
    assert normalize_board("SIDEBOARD") == "side"
    with pytest.raises(DeckError, match="unknown board"):
        normalize_board("commander")


def test_fetch_deck_end_to_end_with_stubbed_http(monkeypatch, archidekt_json):
    seen = {}

    def fake(url, timeout=30):
        seen["url"] = url
        return archidekt_json

    monkeypatch.setattr(decks, "fetch_json", fake)
    d = fetch_deck("https://archidekt.com/decks/1585124/baby_lasagna", "maybe")
    assert seen["url"] == "https://archidekt.com/api/decks/1585124/"
    assert d.source == "archidekt" and d.board == "considering"
    assert d.slug == "buffs-by-hans" and d.file_stem == "buffs-by-hans-considering"
    assert d.text.endswith("\n")


def test_fetch_deck_accepts_bare_id_with_source(monkeypatch, archidekt_json):
    urls = []
    monkeypatch.setattr(decks, "fetch_json", lambda url, timeout=30: urls.append(url) or archidekt_json)
    assert fetch_deck("12345", source_name="archidekt").source == "archidekt"
    assert urls == ["https://archidekt.com/api/decks/12345/"]
    with pytest.raises(DeckError, match="can't find"):
        fetch_deck("not-numeric", source_name="archidekt")


def test_fetch_deck_errors(monkeypatch, moxfield_json):
    monkeypatch.setattr(decks, "fetch_json", lambda url, timeout=30: moxfield_json)
    with pytest.raises(
        DeckError, match="board 'side' of 'Orah Test Deck' is empty \\(non-empty boards: main, considering\\)"
    ):
        fetch_deck("https://moxfield.com/decks/x", "side")
    with pytest.raises(DeckError, match="can't find"):
        fetch_deck("not a url")

    def gone(url, timeout=30):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    monkeypatch.setattr(decks, "fetch_json", gone)
    with pytest.raises(DeckError, match="private decks"):
        fetch_deck("https://moxfield.com/decks/nope")
