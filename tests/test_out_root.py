import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtgproxy import build
from mtgproxy.build import BuildError, prepare_out
from mtgproxy.cli import app
from mtgproxy.paths import out_root
from mtgproxy.sidecar import RunInfo, find_run, find_sidecar, newest_run, runs_parent

runner = CliRunner()


def write_run(folder: Path, name: str, mtime: float | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = RunInfo(name, "a4", "standard", cards=1, fronts_only=True, generated="2026-10-26").write(folder)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_out_root_is_desktop_proxies_unless_the_env_says_otherwise(monkeypatch, tmp_path):
    monkeypatch.setenv("MTG_PROXY_OUT", "~/elsewhere")
    assert out_root() == Path.home() / "elsewhere"
    monkeypatch.delenv("MTG_PROXY_OUT")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert out_root() == tmp_path / "Desktop" / "proxies"


def test_runs_parent_follows_where_you_stand(tmp_path, proxies_root):
    deck = tmp_path / "out" / "deck"
    write_run(deck, "deck")
    assert runs_parent(deck) == tmp_path / "out"  # inside a deck folder: its parent
    assert runs_parent(tmp_path / "out") == tmp_path / "out"  # deck folders right below
    assert runs_parent(tmp_path) == proxies_root  # anywhere else: the proxies folder


def test_newest_run_and_run_names(monkeypatch, tmp_path, proxies_root):
    write_run(proxies_root / "old", "old", mtime=1)
    new = write_run(proxies_root / "new", "new")
    assert newest_run(proxies_root) == new
    monkeypatch.chdir(tmp_path)
    assert find_sidecar(Path("old")) == proxies_root / "old" / "run.json"  # --run NAME
    assert find_sidecar(Path("missing")) is None
    assert find_sidecar(None) is None  # cut without --run outside a deck folder: a plain sheet
    assert find_run(None) == new  # redo / simulate: the newest run
    assert find_run(Path("missing")) is None  # a named run that isn't there is not replaced


def test_the_default_folder_is_created_but_a_mistyped_out_is_refused(tmp_path, proxies_root):
    assert not proxies_root.exists()
    assert prepare_out(proxies_root, "deck") == proxies_root.resolve() / "deck"
    assert proxies_root.is_dir()
    with pytest.raises(BuildError, match="--out dir not found"):
        prepare_out(tmp_path / "typo", "deck")
    assert build.BuildOptions().out_parent == proxies_root


def test_make_without_out_lands_in_the_proxies_folder(decklist, monkeypatch, tmp_path, proxies_root):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["make", str(decklist), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert (proxies_root / "mydeck").is_dir()
    assert not (tmp_path / "mydeck").exists()


def test_redo_outside_a_deck_folder_takes_the_newest_run_and_says_so(monkeypatch, tmp_path, proxies_root):
    monkeypatch.setattr("mtgproxy.cli._per_page", lambda paper, card, sheet_layout: 9)
    deck = proxies_root / "deck"
    deck.mkdir(parents=True)
    card = {
        "file": "1SolRing1.png",
        "name": "Sol Ring",
        "set": "cmm",
        "cn": "464",
        "token": False,
        "dfc": False,
    }
    (deck / "run.json").write_text(json.dumps({
        "name": "deck", "paper": "a4", "card_size": "standard", "registration": "3", "cards": 1,
        "fronts_only": True, "generated": "2026-10-26", "layout": "3x3",
        "sheets": {"main": {"pdf": "deck.pdf", "per_page": 9, "cards": [card]}},
    }))  # fmt: skip
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["redo", "Sol Ring", "-n"])
    assert r.exit_code == 0, r.output
    assert f"run: deck (the newest in {proxies_root}" in r.output
    assert str(proxies_root / "BACKLOG.txt") in r.output
