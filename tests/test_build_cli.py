from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import needs_engine
from mtgproxy import build, engine
from mtgproxy.build import (
    BuildError,
    BuildOptions,
    fetch_args_for,
    prepare_out,
    prune_to_tokens,
    resolve_deck,
)
from mtgproxy.cli import app
from mtgproxy.decks import FetchedDeck
from mtgproxy.paths import NOTES_NAME

runner = CliRunner()


def test_fetch_args_defaults_and_flags():
    assert fetch_args_for(BuildOptions()) == ["--skip_basics", "--prefer_showcase", "--prefer_extra_art"]
    o = BuildOptions(include_basics=True, fancy_art=False, token_copies=2, fetch_args=["--prefer_set", "sld"])
    assert fetch_args_for(o) == ["--tokens", "--token_copies", "2", "--prefer_set", "sld"]


def test_resolve_deck_file_and_errors(decklist):
    d = resolve_deck(BuildOptions(deck=str(decklist)))
    assert d.name == "mydeck" and d.path == decklist.resolve() and d.fmt == "mtga"
    assert resolve_deck(BuildOptions(deck=str(decklist), tokens_only=True)).name == "mydeck-tokens"
    assert resolve_deck(BuildOptions(test_mode=True)).name == "test-sheet"
    with pytest.raises(BuildError, match="no decklist given"):
        resolve_deck(BuildOptions())
    with pytest.raises(BuildError, match="decklist not found"):
        resolve_deck(BuildOptions(deck="nope.txt"))


def test_resolve_deck_url_uses_fetched_name(monkeypatch):
    fetched = FetchedDeck("archidekt", "Baby Lasagna", "considering", ["1 Sol Ring"])
    monkeypatch.setattr(build, "fetch_deck", lambda ref, board: fetched)
    d = resolve_deck(BuildOptions(deck="https://archidekt.com/decks/1/x", board="considering"))
    assert d.name == "baby-lasagna-considering" and d.path is None and d.fetched is fetched


def test_prepare_out_keeps_everything_and_clear_stale_removes_only_our_artifacts(tmp_path):
    out = tmp_path / "deck"
    out.mkdir()
    for n in ("deck.pdf", "deck-duplex.pdf", NOTES_NAME, "deck.txt", "run.json", "keep.png"):
        (out / n).write_text("x")
    assert prepare_out(tmp_path, "deck") == out
    assert len(list(out.iterdir())) == 6  # a failed run must not have wiped the previous PDF
    build.clear_stale(out, "deck")
    assert sorted(p.name for p in out.iterdir()) == ["deck.txt", "keep.png", "run.json"]
    with pytest.raises(BuildError, match="--out dir not found"):
        prepare_out(tmp_path / "missing", "deck")


def test_prune_to_tokens(tmp_path, monkeypatch):
    front, ds = tmp_path / "front", tmp_path / "ds"
    front.mkdir(), ds.mkdir()
    for n in ("1SolRing1.png", "2Ophiomancer_token1.png", "3Card1.jpg"):
        (front / n).write_bytes(b"x")
    (ds / "4Dfc1.png").write_bytes(b"x")
    monkeypatch.setattr(engine, "FRONT", front)
    monkeypatch.setattr(engine, "DOUBLE_SIDED", ds)
    assert prune_to_tokens() == 3
    assert [p.name for p in front.iterdir()] == ["2Ophiomancer_token1.png"]


@needs_engine
def test_cli_make_dry_run_resolves_without_network(decklist, tmp_path):
    r = runner.invoke(
        app, ["make", str(decklist), "-o", str(tmp_path), "--dry-run", "--", "--prefer_set", "sld"]
    )
    assert r.exit_code == 0, r.output
    assert "dry run: mydeck" in r.output and "'--prefer_set', 'sld'" in r.output
    assert (tmp_path / "mydeck").is_dir()


@needs_engine
def test_cli_make_dry_run_url_writes_decklist_into_output(monkeypatch, tmp_path):
    fetched = FetchedDeck("moxfield", "My Deck", "main", ["1 Sol Ring (CMM) 464"])
    monkeypatch.setattr(build, "fetch_deck", lambda ref, board: fetched)
    r = runner.invoke(app, ["make", "https://moxfield.com/decks/abc", "-o", str(tmp_path), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "my-deck" / "my-deck.txt").read_text() == "1 Sol Ring (CMM) 464\n"


@needs_engine
def test_engine_usage_errors_become_clean_messages(decklist):
    with pytest.raises(RuntimeError, match="fetch: No such option '--nope'"):
        engine.fetch_cards(decklist, "mtga", ["--nope"])


def test_cli_validation():
    assert runner.invoke(app, ["--version"]).output.startswith("mtg-proxy ")
    r = runner.invoke(app, ["make", "x.txt", "-t", "2", "--tokens-only", "2"])
    assert r.exit_code == 1 and "mutually exclusive" in r.output
    r = runner.invoke(app, ["make", "--bogus"])
    assert r.exit_code == 2 and "No such option" in r.output  # strict parsing: typos never reach the engine
    r = runner.invoke(app, ["make", "x.txt", "--bogus"])
    assert r.exit_code == 2 and "No such option" in r.output
    r = runner.invoke(app, ["make", "--test", "--print", "EPSON_ET_8550_Series"])
    assert (
        r.exit_code == 1 and "--printer" in r.output
    )  # old `--print PRINTER` form is rejected, not swallowed
    r = runner.invoke(app, ["cut", "--bogus"])
    assert r.exit_code == 2 and "No such option" in r.output
    r = runner.invoke(app, ["make", "missing.txt", "--dry-run"])
    assert r.exit_code == 1 and "decklist not found" in r.output
    r = runner.invoke(app, ["make", "x.txt", "-r", "3", "--dry-run"])
    assert r.exit_code == 2 and "No such option" in r.output  # every sheet is 3-mark: no -r


def test_cli_notes_and_cut_errors(tmp_path):
    r = runner.invoke(app, ["notes", "-o", str(tmp_path)])
    assert r.exit_code == 1 and "no make-proxies output" in r.output
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / NOTES_NAME).write_text("# d notes\n")
    r = runner.invoke(app, ["make", "--notes", "d", "-o", str(tmp_path)])
    assert r.exit_code == 0 and "# d notes" in r.output
    r = runner.invoke(app, ["cut", "--run", str(tmp_path / "nowhere")])
    assert r.exit_code == 1 and "no run.json" in r.output


def test_cli_cut_takes_geometry_from_sidecar(tmp_path, monkeypatch):
    (tmp_path / "run.json").write_text(json.dumps({
        "name": "orah", "paper": "letter", "card_size": "standard", "registration": "3",
        "cards": 8, "fronts_only": True, "generated": "2026-09-17",
    }))  # fmt: skip
    captured = {}

    def fake_run_cut(o):
        captured.update(vars(o))
        return 0

    from mtgproxy import cutting

    monkeypatch.setattr(cutting, "run_cut", fake_run_cut)
    r = runner.invoke(app, ["cut", "--run", str(tmp_path), "--passes", "4"])
    assert r.exit_code == 0, r.output
    assert (captured["paper"], captured["layout"], captured["label"], captured["passes"]) == (
        "letter",
        "4x2",  # run.json from before layouts
        "orah",
        4,
    )
    r = runner.invoke(app, ["cut", "--run", str(tmp_path), "-p", "a4"])
    assert captured["paper"] == "a4"  # explicit flag wins
    assert isinstance(captured["out_dir"], Path)
    assert captured["connection"] == "ble"  # Bluetooth unless asked otherwise
    r = runner.invoke(app, ["cut", "--run", str(tmp_path), "--usb"])
    assert captured["connection"] == "usb"


def test_move_tokens_last_reindexes_by_token_name(tmp_path, monkeypatch):
    front, ds = tmp_path / "front", tmp_path / "ds"
    front.mkdir(), ds.mkdir()
    for n in (
        "1SolRing1.png",
        "5FaerieDragon_token1.png",
        "5FaerieDragon_token2.png",
        "9Treasure_token1.png",
        "14Beast_token1.png",
        "96Forest3.png",
    ):
        (front / n).write_bytes(b"x")
    (ds / "5FaerieDragon_token1.png").write_bytes(b"back")  # double-faced token back
    monkeypatch.setattr(engine, "FRONT", front)
    monkeypatch.setattr(engine, "DOUBLE_SIDED", ds)
    assert build.move_tokens_last() == 5
    from natsort import natsorted

    assert natsorted(p.name for p in front.iterdir()) == [
        "1SolRing1.png", "96Forest3.png",
        "10000Beast_token1.png", "10001FaerieDragon_token1.png", "10001FaerieDragon_token2.png", "10002Treasure_token1.png",
    ]  # fmt: skip
    assert [p.name for p in ds.iterdir()] == ["10001FaerieDragon_token1.png"]
    assert build.move_tokens_last() == 0  # idempotent
    assert build.prune_to_tokens() == 2  # --tokens-only still recognises the renamed files


def test_cli_make_3x3_refuses_what_it_cant_print(decklist):
    r = runner.invoke(app, ["make", str(decklist), "--layout", "3x3", "-p", "letter", "--dry-run"])
    assert r.exit_code == 1 and "A4 paper with standard cards only, not letter" in r.output
    r = runner.invoke(app, ["make", str(decklist), "--layout", "3x3", "--backs", "--dry-run"])
    assert r.exit_code == 1 and "fronts only for now" in r.output and "Drop --backs" in r.output
    r = runner.invoke(app, ["make", "--test", "--layout", "3x3", "--backs"])
    assert r.exit_code == 1 and "fronts only for now" in r.output
    r = runner.invoke(app, ["make", str(decklist), "--layout", "5x5", "--dry-run"])
    assert r.exit_code == 1 and "unknown layout '5x5' (known: 3x3, 4x2)" in r.output


@needs_engine
def test_cli_make_is_nine_a_sheet_unless_told(decklist, tmp_path):
    r = runner.invoke(app, ["make", str(decklist), "-o", str(tmp_path), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "(paper a4, 3x3 layout of 9 cards, format" in r.output
    r = runner.invoke(app, ["make", str(decklist), "-o", str(tmp_path), "--layout", "4x2", "--dry-run"])
    assert "(paper a4, 4x2 layout of 8 cards, format" in r.output
    r = runner.invoke(app, ["make", str(decklist), "-o", str(tmp_path), "-p", "letter", "--dry-run"])
    assert "(paper letter, 4x2 layout of 8 cards, format" in r.output  # 3x3 is A4 only


def test_every_sheet_is_3_mark_and_3x3_takes_the_engines_tighter_layout():
    three = build.pdf_base_args(build.resolve_layout(BuildOptions(layout="3x3")))
    assert three[three.index("--registration") + 1] == "3"
    assert three[-3:] == ["--borderless", "--label", ""]
    four = build.pdf_base_args(build.resolve_layout(BuildOptions(layout="4x2")))
    assert four[four.index("--registration") + 1] == "3" and "--borderless" not in four
    assert build.resolve_layout(BuildOptions()).layout == "3x3"
    assert build.resolve_layout(BuildOptions(paper="letter")).layout == "4x2"


@pytest.fixture
def game(tmp_path, monkeypatch):
    """The engine's game/ dirs in tmp_path, its PDF step a stand-in that records its arguments."""
    from mtgproxy import manifest, trim

    game = tmp_path / "game"
    for name in ("FRONT", "BACK", "DOUBLE_SIDED", "OUTPUT"):
        (game / name.lower()).mkdir(parents=True)
        monkeypatch.setattr(engine, name, game / name.lower())
    monkeypatch.setattr(manifest, "STATE_FILE", game / ".manifest.json")
    monkeypatch.setattr(trim, "STATE_FILE", game / ".trimmed.json")
    monkeypatch.setattr(engine, "clean_images", lambda: [f.unlink() for f in (game / "front").iterdir()])
    monkeypatch.setattr(build, "duplex_offset_args", lambda: [])
    calls: list[list[str]] = []

    def create_pdf(args):
        calls.append(args)
        (game / "output" / "game.pdf").write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(engine, "create_pdf", create_pdf)
    return calls


@needs_engine
def test_a_3x3_run_records_its_layout_for_cut_proxies(tmp_path, monkeypatch, game):
    """The 3x3 test sheet through the whole build (the engine's PDF step stood in): the engine is
    asked for its tighter layout with 3 marks, one sheet holds 9, and run.json and the notes say so."""
    from mtgproxy.sidecar import RunInfo

    monkeypatch.setattr(
        build,
        "write_test_cards",
        lambda n, front, back, ds: [(front / f"test_{i:02d}.png").write_bytes(b"x") for i in range(1, n + 1)],
    )
    out = tmp_path / "out"
    out.mkdir()
    result = build.run_build(BuildOptions(test_mode=True, layout="3x3", out_parent=out))
    assert result.cards == 9  # one full sheet of gauges
    args = game[0]
    assert "--borderless" in args and args[args.index("--registration") + 1] == "3"
    info = RunInfo.read(result.out / "run.json")
    assert (info.layout, info.registration, info.cards, info.paper) == ("3x3", "3", 9, "a4")
    notes = (result.out / NOTES_NAME).read_text()
    assert "| layout: 3x3 | registration: 3-mark | cards: 9" in notes
    assert "`cut-proxies --layout 3x3`" in notes


@needs_engine
def test_a_3x3_deck_gets_pages_of_nine_and_no_duplex_sheet(tmp_path, game):
    from mtgproxy import manifest

    for i in range(1, 11):
        (engine.FRONT / f"{i}Card{chr(64 + i)}1.png").write_bytes(b"x")  # 1CardA1.png ..
    files, _ = manifest.partition(fronts_only=True, duplex_dfc=True)  # in the engine's order
    per_page = build.per_page(BuildOptions(layout="3x3"))
    sheet = manifest.make_sheet(manifest.MAIN, files, "d.pdf", per_page, manifest.Recorder())
    assert (sheet.per_page, sheet.pages, len(sheet.page(2))) == (9, 2, 1)
    assert manifest.locate(sheet.cards[9], {manifest.MAIN: sheet}) == "p2.1"  # slot numbers per 9
    assert sheet.cards[9].name == "CardJ"
    assert build._defer(files, 9)[1] == files[9:]  # --defer-partial leaves the tenth for the backlog

    # A double-faced card would need a duplex sheet: refused before anything is written.
    (engine.DOUBLE_SIDED / files[0].name).write_bytes(b"back")
    deck = tmp_path / "deck.txt"
    deck.write_text("1 Card\n")
    with pytest.raises(
        BuildError, match=r"1 double-faced card\(s\) would need a duplex sheet.*--split-faces"
    ):
        build.run_build(BuildOptions(deck=str(deck), layout="3x3", skip_fetch=True, out_parent=tmp_path))
    assert game == [] and not (tmp_path / "deck" / "run.json").exists()
    split = build.run_build(
        BuildOptions(deck=str(deck), layout="3x3", skip_fetch=True, out_parent=tmp_path, duplex_dfc=False)
    )
    assert split.cards == 11 and len(game) == 1  # --split-faces: both faces on the 3x3 sheet


def test_cli_trim_parsing():
    r = runner.invoke(app, ["make", "x.txt", "--trim", "Temple Garden:9", "--dry-run"])
    assert r.exit_code == 1 and "between 0 and 3" in r.output
