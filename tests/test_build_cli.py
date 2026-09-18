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
    for n in ("deck.pdf", "deck-duplex.pdf", "x.studio3", NOTES_NAME, "deck.txt", "run.json", "keep.png"):
        (out / n).write_text("x")
    assert prepare_out(tmp_path, "deck") == out
    assert len(list(out.iterdir())) == 7  # a failed run must not have wiped the previous PDF
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


def test_find_template_prefers_project_base_and_highest_version(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "TEMPLATES", tmp_path / "t")
    monkeypatch.setattr(build, "SCM", tmp_path / "scm")
    (tmp_path / "scm" / "cutting_templates").mkdir(parents=True)
    for n in ("a4-standard-v3.studio3", "a4-standard-v10.studio3"):
        (tmp_path / "scm" / "cutting_templates" / n).write_bytes(b"")
    assert build.find_template("a4", "standard") == (
        tmp_path / "scm/cutting_templates/a4-standard-v10.studio3",
        False,
    )
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a4-standard-v5-alpha.studio3").write_bytes(b"")
    assert build.find_template("a4", "standard") == (tmp_path / "t/a4-standard-v5-alpha.studio3", True)
    assert build.find_template("a3", "standard") == (None, False)


@needs_engine
def test_cli_make_dry_run_resolves_without_network(decklist, tmp_path):
    r = runner.invoke(
        app, ["make", str(decklist), "-o", str(tmp_path), "--dry-run", "-r", "3", "--", "--prefer_set", "sld"]
    )
    assert r.exit_code == 0, r.output
    assert "dry run: mydeck" in r.output and "3-mark" in r.output and "'--prefer_set', 'sld'" in r.output
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
    r = runner.invoke(app, ["make", "x.txt", "-r", "5", "--dry-run"])
    assert r.exit_code == 1 and "registration" in r.output


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
    assert (captured["paper"], captured["registration"], captured["label"], captured["passes"]) == (
        "letter",
        "3",
        "orah",
        4,
    )
    r = runner.invoke(app, ["cut", "--run", str(tmp_path), "-r", "4"])
    assert captured["registration"] == "4"  # explicit flag wins
    assert isinstance(captured["out_dir"], Path)


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


def test_cli_trim_parsing():
    r = runner.invoke(app, ["make", "x.txt", "--trim", "Temple Garden:9", "--dry-run"])
    assert r.exit_code == 1 and "between 0 and 3" in r.output
