from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtgproxy import build, engine, manifest
from mtgproxy.backlog import Backlog, BacklogError, Entry, decklist_text
from mtgproxy.cli import app
from mtgproxy.manifest import DUPLEX, MAIN, ManifestError, Recorder, Sheet, SlotCard

runner = CliRunner()


def _card(name, set_="", cn="", dfc=False, token=False, file="x.png"):
    return SlotCard(file, name, set_, cn, token, dfc)


@pytest.fixture
def sheets() -> dict[str, Sheet]:
    main = Sheet(MAIN, "d.pdf", 4, [
        _card("Sol Ring", "cmm", "464"), _card("Lightning Bolt", "sta", "42"), _card("Lightning Bolt", "sta", "42"),
        _card("Llanowar Elves"), _card("Counterspell", "sta", "15"), _card("Treasure", "tneo", "17", token=True),
    ])  # fmt: skip
    duplex = Sheet(
        DUPLEX,
        "d-duplex.pdf",
        4,
        [_card("Delver of Secrets // Insectile Aberration", "sld", "2367", dfc=True)],
    )
    return {MAIN: main, DUPLEX: duplex}


def test_sheet_pages_and_slots(sheets):
    s = sheets[MAIN]
    assert s.pages == 2 and [c.name for c in s.page(2)] == ["Counterspell", "Treasure"]
    assert s.slot(1, 2).name == "Lightning Bolt" and s.position(5) == (2, 2)
    with pytest.raises(ManifestError, match="no page 3"):
        s.page(3)
    with pytest.raises(ManifestError, match="no slot 3"):
        s.slot(2, 3)
    assert Sheet(MAIN, None, 8).pages == 0
    rt = manifest.sheets_from_dict({k: v.as_dict() for k, v in sheets.items()})
    assert rt[DUPLEX].cards[0].dfc and rt[MAIN].cards[5].token and rt[MAIN].per_page == 4


def test_resolve_references(sheets):
    names = lambda refs: [c.name for r in refs for c in manifest.resolve(r, sheets)]  # noqa: E731
    assert names(["last"]) == ["Counterspell", "Treasure"]
    assert names(["p1"]) == ["Sol Ring", "Lightning Bolt", "Lightning Bolt", "Llanowar Elves"]
    assert names(["p1.4", "P2:1", "d1.1", "dlast"]) == [
        "Llanowar Elves", "Counterspell", "Delver of Secrets // Insectile Aberration",
        "Delver of Secrets // Insectile Aberration",
    ]  # fmt: skip
    assert names(["sol ring", "Sol", "elves", "delver"]) == [
        "Sol Ring", "Sol Ring", "Llanowar Elves", "Delver of Secrets // Insectile Aberration",
    ]  # fmt: skip
    with pytest.raises(ManifestError, match="ambiguous: Lightning Bolt, Llanowar Elves"):
        manifest.resolve("l", sheets)
    with pytest.raises(ManifestError, match="no card matching"):
        manifest.resolve("Black Lotus", sheets)
    with pytest.raises(ManifestError, match="no duplex sheet"):
        manifest.resolve("dlast", {MAIN: sheets[MAIN]})
    with pytest.raises(ManifestError, match="empty"):
        manifest.resolve("last", {MAIN: Sheet(MAIN, None, 8)})
    c = manifest.resolve("p2.1", sheets)[0]
    assert manifest.locate(c, sheets) == "p2.1"
    assert manifest.locate(sheets[DUPLEX].cards[0], sheets) == "d1.1"


def test_recorder_lookup_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "STATE_FILE", tmp_path / ".manifest.json")
    r = Recorder()
    r.record(3, "TempleGarden", "Temple Garden", "TRK", "491")
    r.record(5, "Treasure_token", "Treasure", "tneo", "17")
    r.save()
    r = Recorder.load()
    c = r.lookup(Path("3TempleGarden2.png"))
    assert (c.name, c.set, c.cn, c.token, c.line) == (
        "Temple Garden",
        "trk",
        "491",
        False,
        "Temple Garden (TRK) 491",
    )
    # tokens get re-indexed by move_tokens_last: matched by name
    t = r.lookup(Path("10000Treasure_token1.png"))
    assert (t.name, t.set, t.cn, t.token) == ("Treasure", "tneo", "17", True)
    # nothing recorded: the clean name is all we have
    u = r.lookup(Path("9SolRing1.png"))
    assert (u.name, u.set, u.line) == ("SolRing", "", "SolRing")
    assert r.lookup(Path("3TempleGarden1-back.png")).name == "Temple Garden"


def test_partition_orders_like_the_engine(tmp_path, monkeypatch):
    front, ds = tmp_path / "front", tmp_path / "ds"
    front.mkdir(), ds.mkdir()
    for n in ("1A1.png", "2Dfc1.png", "3B1.png", "10C1.png", "10000Tok_token1.png"):
        (front / n).write_bytes(b"x")
    (ds / "2Dfc1.png").write_bytes(b"x")
    monkeypatch.setattr(engine, "FRONT", front)
    monkeypatch.setattr(engine, "DOUBLE_SIDED", ds)
    nm = lambda fs: [f.name for f in fs]  # noqa: E731
    main, duplex = manifest.partition(fronts_only=True, duplex_dfc=True)
    assert nm(main) == ["1A1.png", "3B1.png", "10C1.png", "10000Tok_token1.png"] and nm(duplex) == [
        "2Dfc1.png"
    ]
    main, duplex = manifest.partition(fronts_only=False, duplex_dfc=True)
    assert nm(main) == ["1A1.png", "3B1.png", "10C1.png", "10000Tok_token1.png", "2Dfc1.png"] and duplex == []
    main, duplex = manifest.partition(fronts_only=True, duplex_dfc=False)
    assert "2Dfc1-back.png" in nm(main) and duplex == []
    back = next(f for f in main if f.name == "2Dfc1-back.png")
    assert manifest.view_target(back) == ds / "2Dfc1.png" and manifest.view_target(main[0]) == main[0]
    assert build._defer(main, 4) == (main[:4], main[4:]) and build._defer(main[:3], 4) == ([], main[:3])


def test_entry_round_trip_and_bare_lines():
    e = Entry.from_slot(
        _card("Delver of Secrets // Insectile Aberration", "sld", "2367", dfc=True), "deck-a", "d1.1", 0.5
    )
    line = e.format()
    assert line.startswith(
        "1 Delver of Secrets // Insectile Aberration (SLD) 2367  # deck=deck-a ref=d1.1 date="
    )
    assert line.endswith(" trim=0.5 dfc")
    back = Entry.parse(line)
    assert back == e
    bare = Entry.parse("2x Llanowar Elves")
    assert (bare.name, bare.set, bare.qty, bare.card) == ("Llanowar Elves", "", 2, "Llanowar Elves")
    assert [u.qty for u in bare.units()] == [1, 1]
    assert Entry.parse("# comment") is None and Entry.parse("   ") is None
    with pytest.raises(BacklogError, match="unreadable"):
        Entry.parse("Sol Ring (CMM) 464")
    assert (
        decklist_text([e, bare])
        == "1 Delver of Secrets // Insectile Aberration (SLD) 2367\n1 Llanowar Elves\n"
    )


def test_backlog_take_drop_save(tmp_path):
    bl = Backlog.at(tmp_path)
    assert bl.entries == [] and not bl.path.exists()
    bl.add(
        [Entry("Sol Ring", "cmm", "464", 3), Entry("Delver", "sld", "1", 1, dfc=True), Entry("Bolt", qty=6)]
    )
    bl.save()
    bl = Backlog.at(tmp_path)
    assert len(bl.cards) == 10 and [len(g) for g in bl.split()] == [9, 1]
    now, later = bl.take(8, full_only=True)
    assert len(now) == 8 and [c.name for c in later] == ["Bolt", "Delver"]
    now, later = bl.take(8, full_only=False)
    assert len(now) == 10 and later == []
    assert "fronts: 9 → 1 full sheet(s) of 8 + 1 waiting" in bl.summary(8) and "duplex: 1" in bl.summary(8)
    assert bl.listing().splitlines()[0].strip().startswith("1  3 Sol Ring (CMM) 464")
    assert [e.name for e in bl.drop("2")] == ["Delver"]
    assert [e.name for e in bl.drop("bolt")] == ["Bolt"]
    with pytest.raises(BacklogError, match="nothing"):
        bl.drop("nope")
    with pytest.raises(BacklogError, match="no backlog entry #9"):
        bl.drop("9")
    bl.entries = []
    bl.save()
    assert not bl.path.exists()


def test_trim_for_matches_by_clean_name():
    trims = {"Temple Garden": 0.3}
    assert build._trim_for(_card("Temple Garden"), trims) == 0.3
    assert build._trim_for(_card("Sol Ring"), trims) is None
    assert build._trim_for(_card("Sol Ring"), {"all": 0.2}) == 0.2
    assert build._trim_for(_card("Treasure", token=True), {"all": 0.2}) is None


def test_cli_redo_and_backlog(tmp_path, monkeypatch):
    monkeypatch.setattr("mtgproxy.cli._per_page", lambda paper, card: 8)
    r = runner.invoke(app, ["redo", "last", "--run", str(tmp_path / "nowhere")])
    assert r.exit_code == 1 and "no run.json" in r.output and "Windows mirror" in r.output
    deck = tmp_path / "deck"
    deck.mkdir()
    sheets = {
        MAIN: Sheet(
            MAIN, "deck.pdf", 8, [_card("Sol Ring", "cmm", "464"), _card("Temple Garden", "trk", "491")]
        )
    }
    (deck / "run.json").write_text(json.dumps({
        "name": "deck", "paper": "a4", "card_size": "standard", "registration": "4", "cards": 2,
        "fronts_only": True, "generated": "2026-09-19", "trims": {"Temple Garden": 0.3},
        "sheets": {k: v.as_dict() for k, v in sheets.items()},
    }))  # fmt: skip
    r = runner.invoke(app, ["redo", "temple", "p1.1", "--run", str(deck)])
    assert r.exit_code == 0, r.output
    lines = (tmp_path / "BACKLOG.txt").read_text().splitlines()
    assert lines[1].startswith("1 Temple Garden (TRK) 491  # deck=deck ref=p1.2") and lines[1].endswith(
        "trim=0.3 single"
    )
    assert lines[2].startswith("1 Sol Ring (CMM) 464  # deck=deck ref=p1.1")
    r = runner.invoke(app, ["redo", "bogus", "--run", str(deck)])
    assert r.exit_code == 1 and "no card matching" in r.output
    r = runner.invoke(app, ["backlog", "-o", str(tmp_path)])
    assert r.exit_code == 0 and "2 card(s)" in r.output and "  2  1 Sol Ring" in r.output
    r = runner.invoke(app, ["backlog", "build", "-o", str(tmp_path), "--full-only"])
    assert r.exit_code == 1 and "no full sheet yet" in r.output
    r = runner.invoke(app, ["backlog", "build", "-o", str(tmp_path), "-n"])
    assert r.exit_code == 0 and "2 card(s) → " in r.output and "1 Sol Ring (CMM) 464\n" in r.output
    assert not (tmp_path / "backlog-").exists()
    r = runner.invoke(app, ["backlog", "drop", "sol", "-o", str(tmp_path)])
    assert r.exit_code == 0 and "1 card(s) left" in r.output
    r = runner.invoke(app, ["backlog", "drop", "1", "-o", str(tmp_path)])
    assert r.exit_code == 0 and not (tmp_path / "BACKLOG.txt").exists()
    r = runner.invoke(app, ["backlog", "-o", str(tmp_path)])
    assert r.exit_code == 0 and "backlog empty" in r.output


def test_cli_make_has_defer_flag_and_records_it():
    assert "--defer-partial" in runner.invoke(app, ["make", "--help"]).output
    o = build.BuildOptions(token_copies=2, fetch_args=["--prefer_set", "sld"])
    assert o.recorded()["token_copies"] == 2 and o.recorded()["fetch_args"] == ["--prefer_set", "sld"]


def test_hand_written_lines_get_their_faces_resolved_once(tmp_path):
    from mtgproxy.decks import DOUBLE_SIDED_LAYOUTS

    assert "reversible_card" in DOUBLE_SIDED_LAYOUTS  # the two-art shocklands print double-sided
    bl = Backlog.at(tmp_path)
    bl.add([Entry.parse("1 Hallowed Fountain // Hallowed Fountain (ECL) 347"), Entry.parse("1 Sol Ring"),
            Entry.from_slot(_card("Delver", "sld", "1", dfc=True), "d", "d1.1", None)])  # fmt: skip
    assert [e.faces_known for e in bl.entries] == [False, False, True]
    asked = []

    def lookup(e):
        asked.append(e.name)
        return {"Hallowed Fountain // Hallowed Fountain": True, "Sol Ring": False}.get(e.name)

    assert bl.resolve_faces(lookup) == 2 and asked == ["Hallowed Fountain // Hallowed Fountain", "Sol Ring"]
    assert [len(g) for g in bl.split()] == [1, 2]  # Fountain now counts as duplex
    bl.save()
    lines = bl.path.read_text().splitlines()
    assert lines[1].endswith("# dfc") and lines[2].endswith("# single")
    bl = Backlog.at(tmp_path)
    assert bl.resolve_faces(lambda e: (_ for _ in ()).throw(AssertionError("must not ask again"))) == 0
    # Scryfall unreachable: stays unknown, counted single, asked again next time
    bl.add([Entry.parse("1 Brainstorm")])
    assert bl.resolve_faces(lambda e: None) == 0 and not bl.entries[-1].faces_known


def test_cli_backlog_resolves_faces_via_scryfall(tmp_path, monkeypatch):
    monkeypatch.setattr("mtgproxy.cli._per_page", lambda paper, card: 8)
    monkeypatch.setattr("mtgproxy.cli.is_double_sided", lambda name, s, cn: name.startswith("Hallowed"))
    (tmp_path / "BACKLOG.txt").write_text("1 Hallowed Fountain // Hallowed Fountain (ECL) 347\n1 Sol Ring\n")
    r = runner.invoke(app, ["backlog", "-o", str(tmp_path)])
    assert r.exit_code == 0 and "duplex: 1" in r.output and "fronts: 1" in r.output, r.output
    assert "[dfc]" in r.output
    assert "# dfc" in (tmp_path / "BACKLOG.txt").read_text()


def test_front_marker_prints_a_dfc_as_a_single_sided_card(tmp_path, monkeypatch):
    e = Entry.parse("1 Hallowed Fountain // Hallowed Fountain (ECL) 347  # dfc front")
    assert e.dfc and e.front and e.format().endswith("# dfc front")
    bl = Backlog(tmp_path / "b.txt", [e, Entry("Delver", dfc=True)])
    assert [[c.name for c in g] for g in bl.split()] == [
        ["Hallowed Fountain // Hallowed Fountain"],
        ["Delver"],
    ]
    front, ds = tmp_path / "front", tmp_path / "ds"
    front.mkdir(), ds.mkdir()
    for n in ("1HallowedFountainHallowedFountain1.png", "2Delver1.png"):
        (front / n).write_bytes(b"x"), (ds / n).write_bytes(b"x")
    monkeypatch.setattr(engine, "FRONT", front)
    monkeypatch.setattr(engine, "DOUBLE_SIDED", ds)
    assert build.drop_backs(["Hallowed Fountain // Hallowed Fountain", "Sol Ring"]) == [
        "Hallowed Fountain // Hallowed Fountain"
    ]
    assert [p.name for p in ds.iterdir()] == ["2Delver1.png"]
    main, duplex = manifest.partition(fronts_only=True, duplex_dfc=True)
    assert [p.name for p in main] == ["1HallowedFountainHallowedFountain1.png"] and [
        p.name for p in duplex
    ] == ["2Delver1.png"]
