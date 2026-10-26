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
    assert r.exit_code == 1 and "takes no decklist" in r.output  # --print is a flag, no printer argument
    r = runner.invoke(app, ["make", "--test", "--printer", "X"])
    assert r.exit_code == 2 and "No such option" in r.output
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
        out = (
            args[args.index("--output_path") + 1] if "--output_path" in args else game / "output" / "game.pdf"
        )
        Path(out).write_bytes(b"%PDF-1.4\n")

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
def test_a_3x3_deck_gets_pages_of_nine_and_a_duplex_sheet(tmp_path, monkeypatch, game):
    from mtgproxy import manifest
    from mtgproxy.sidecar import RunInfo

    for i in range(1, 11):
        (engine.FRONT / f"{i}Card{chr(64 + i)}1.png").write_bytes(b"x")  # 1CardA1.png ..
    files, _ = manifest.partition(fronts_only=True, duplex_dfc=True)  # in the engine's order
    per_page = build.per_page(BuildOptions(layout="3x3"))
    sheet = manifest.make_sheet(manifest.MAIN, files, "d.pdf", per_page, manifest.Recorder())
    assert (sheet.per_page, sheet.pages, len(sheet.page(2))) == (9, 2, 1)
    assert manifest.locate(sheet.cards[9], {manifest.MAIN: sheet}) == "p2.1"  # slot numbers per 9
    assert sheet.cards[9].name == "CardJ"
    assert build._defer(files, 9)[1] == files[9:]  # --defer-partial leaves the tenth for the backlog

    # A double-faced card goes on a 3x3 duplex sheet, asked of the engine as a 4x2 one is: pages
    # front and back (no --only_fronts) with the saved duplex offset, in the tighter layout.
    monkeypatch.setattr(build, "duplex_offset_args", lambda: ["--load_offset"])
    (engine.DOUBLE_SIDED / files[0].name).write_bytes(b"back")
    deck = tmp_path / "deck.txt"
    deck.write_text("1 Card\n")
    result = build.run_build(BuildOptions(deck=str(deck), skip_fetch=True, out_parent=tmp_path))
    fronts, duplex = game
    assert "--only_fronts" in fronts and "--load_offset" not in fronts and "--no_back_marks" not in fronts
    assert "--only_fronts" not in duplex and "--load_offset" in duplex and "--no_back_marks" in duplex
    assert duplex[duplex.index("--registration") + 1] == "3" and "--borderless" in duplex
    assert (result.cards, result.dfc_count, result.duplex_pdf.name) == (10, 1, "deck-duplex.pdf")
    info = RunInfo.read(result.out / "run.json")
    assert (info.layout, info.sheets["duplex"]["per_page"], info.duplex_pdf) == ("3x3", 9, "deck-duplex.pdf")

    game.clear()  # --backs: the whole deck double-sided, on 3x3 sheets
    both = build.run_build(
        BuildOptions(deck=str(deck), skip_fetch=True, out_parent=tmp_path, fronts_only=False)
    )
    (args,) = game
    assert "--only_fronts" not in args and "--load_offset" in args and "--borderless" in args
    assert "--no_back_marks" in args
    assert both.cards == 10 and both.duplex_pdf is None

    game.clear()
    split = build.run_build(
        BuildOptions(deck=str(deck), layout="3x3", skip_fetch=True, out_parent=tmp_path, duplex_dfc=False)
    )
    assert split.cards == 11 and len(game) == 1  # --split-faces: both faces on the 3x3 sheet


def _duplex_pages(tmp_path, sheet_layout: str, n: int, extra: tuple[str, ...] = (), ppi: int = 50):
    """The engine's real duplex PDF for ``n`` flat-coloured cards in ``sheet_layout`` at 50 ppi: a
    front's own top-right carries a white square, a back's own top-left a black one. Returns the
    two pages (landscape, as the engine saves every sheet) and the colours."""
    import numpy as np
    from PIL import Image, ImageDraw
    from pypdf import PdfReader

    colours = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180),
               (70, 240, 240), (240, 50, 230), (210, 245, 60), (128, 128, 0)][:n]  # fmt: skip
    dirs = {d: tmp_path / sheet_layout / d for d in ("front", "back", "ds")}
    for d in dirs.values():
        d.mkdir(parents=True)
    for i, c in enumerate(colours, 1):
        for side, (x0, fill) in (("front", (126 - 46, "white")), ("ds", (16, "black"))):
            im = Image.new("RGB", (126, 176), c)
            ImageDraw.Draw(im).rectangle([x0, 16, x0 + 30, 46], fill=fill)
            im.save(dirs[side] / f"{i}Card{i}1.png")
    Image.new("RGB", (126, 176), (128, 128, 128)).save(dirs["back"] / "back.png")
    out = tmp_path / f"{sheet_layout}.pdf"
    base = build.pdf_base_args(build.resolve_layout(BuildOptions(layout=sheet_layout)))
    engine.create_pdf([
        *base, "--front_dir_path", str(dirs["front"]), "--double_sided_dir_path", str(dirs["ds"]),
        "--back_dir_path", str(dirs["back"]), "--output_path", str(out), "--ppi", str(ppi), *extra,
    ])  # fmt: skip
    pages = [np.asarray(p.images[0].image.convert("RGB")).astype(int) for p in PdfReader(out).pages]
    return pages, colours


def _card(page, colour, marker):
    """(bbox incl. bleed, marker centre) of the card in ``colour`` on ``page``."""
    import numpy as np

    mask = np.abs(page - np.array(colour)).sum(axis=2) < 60
    cols, rows = mask.sum(axis=0), mask.sum(axis=1)  # JPEG fringes elsewhere hold a few pixels at most
    xs, ys = np.nonzero(cols > cols.max() / 2)[0], np.nonzero(rows > rows.max() / 2)[0]
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
    sub = page[y0 : y1 + 1, x0 : x1 + 1]
    my, mx = np.nonzero(sub.min(axis=2) > 235 if marker == "white" else sub.max(axis=2) < 60)
    return (x0, y0, x1, y1), (x0 + mx.mean(), y0 + my.mean())


@needs_engine
@pytest.mark.parametrize("sheet_layout, n", [("3x3", 9), ("4x2", 8)])
def test_each_back_sits_behind_its_front_for_a_long_edge_flip(tmp_path, sheet_layout, n):
    """Both pages are landscape, so a long-edge flip mirrors the page top to bottom: a front at
    (x, y) is backed by the back page's (x, h - y). Each back's box is its front's, mirrored, and its
    own top-left (the black square) sits behind its front's top-right (the white one), so the back
    reads upright once the card is turned over side to side. A 4x2 sheet follows the same rule, so
    a 3x3 sheet prints double-sided the same way. At 300 ppi (2026-10-25) each 3x3 back sat within
    0 px of its mirrored front, and the saved duplex offset moved the back pages alone, by
    (-2, +2) px, in both layouts."""
    (front, back), colours = _duplex_pages(tmp_path, sheet_layout, n)
    h = front.shape[0]
    for c in colours:  # within 2 px, 1 mm at 50 ppi (JPEG edges); a wrong flip is a card off
        (fx0, fy0, fx1, fy1), white = _card(front, c, "white")
        (bx0, by0, bx1, by1), black = _card(back, c, "black")
        assert abs(bx0 - fx0) <= 2 and abs(bx1 - fx1) <= 2
        assert abs(by0 - (h - 1 - fy1)) <= 2 and abs(by1 - (h - 1 - fy0)) <= 2
        assert abs(black[0] - white[0]) <= 2 and abs(black[1] - (h - 1 - white[1])) <= 2


@needs_engine
def test_back_pages_carry_no_registration_marks(tmp_path):
    """Turned over the long edge, a back page's square sits behind the front's top-right L and showed
    through enough to make the cutter misread it (2026-10-26), so mtg-proxy's back pages have none:
    nothing dark on a back page outside its cards. The engine's own default still prints them."""
    import numpy as np

    def stray_ink(page, colours):
        cards = np.zeros(page.shape[:2], bool)
        for c in colours:
            (x0, y0, x1, y1), _ = _card(page, c, "black")
            cards[max(0, y0 - 2) : y1 + 3, max(0, x0 - 2) : x1 + 3] = True  # JPEG fringes at the edges
        return int(((page.sum(axis=2) < 150) & ~cards).sum())

    (_, marked), colours = _duplex_pages(tmp_path / "marked", "3x3", 9)
    (_, plain), _ = _duplex_pages(tmp_path / "plain", "3x3", 9, extra=tuple(build.BACK_PAGES))
    assert stray_ink(marked, colours) > 20 and stray_ink(plain, colours) == 0


def test_cli_trim_parsing():
    r = runner.invoke(app, ["make", "x.txt", "--trim", "Temple Garden:9", "--dry-run"])
    assert r.exit_code == 1 and "between 0 and 3" in r.output
