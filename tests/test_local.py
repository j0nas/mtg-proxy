from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from natsort import natsorted
from typer.testing import CliRunner

from conftest import needs_engine
from mtgproxy import build, engine, local, manifest, trim
from mtgproxy.build import BuildError, BuildOptions, ResolvedDeck
from mtgproxy.cli import app
from mtgproxy.local import LocalImageError, LocalImages, read_line, slug, split_deck

runner = CliRunner()

# The engine's moxfield parser (plugins/mtg/patterns.py): a line without "(SET) number" is skipped.
MOXFIELD = re.compile(r"(\d+)x?\s+(.+?)\s+\((\w+)\)\s+([^\s]+)")

DECK = """\
1 Aminatou, the Fateshifter
1 Sol Ring (SLZ) 114
1 Fell the Profane // Fell Mire
2 Plains
1 Delver of Secrets // Insectile Aberration (SLD) 2367
1 Counterspell (SLZ) 99
1 Lightning Bolt
"""


def moxfield_lines(lines: list[str], fmt: str) -> list[tuple[str, str, str, int] | None]:
    """Stand-in for engine.read_deck_lines with the moxfield format."""
    out = []
    for line in lines:
        m = MOXFIELD.match(line)
        out.append((m[2], m[3], m[4], int(m[1])) if m else None)
    return out


@pytest.fixture
def images(tmp_path: Path) -> Path:
    d = tmp_path / "cards"
    d.mkdir()
    for n in ("aminatou-the-fateshifter.jpg", "fell-the-profane.jpg", "plains.png", "Counterspell.JPG"):
        (d / n).write_bytes(n.encode())
    (d / "unused-card.jpg").write_bytes(b"x")
    (d / "notes.txt").write_text("not an image")
    (d / "._plains.png").write_bytes(b"macOS resource fork")
    return d


def test_slug_clean_and_basics():
    assert slug("Fell the Profane // Fell Mire") == "fell-the-profane"
    assert slug("Norn's Annex") == "norn-s-annex"
    assert slug("Elesh Norn, Mother of Machines") == "elesh-norn-mother-of-machines"
    assert slug("Aminatou, the Fateshifter") == "aminatou-the-fateshifter"
    assert slug("norn-s-annex") == "norn-s-annex"  # file stems map to themselves
    assert local.clean("Fell the Profane // Fell Mire") == "FelltheProfaneFellMire"  # the engine's file name
    assert local.is_basic("Plains") and local.is_basic("Snow-Covered Island") and local.is_basic("Wastes")
    assert not local.is_basic("Plains of Doom") and not local.is_basic("Island of Wak-Wak")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1 Sol Ring (SLZ) 114", ("Sol Ring", 1)),
        ("4x Plains (MOM) 277 *F*", ("Plains", 4)),
        ("1 Fell the Profane // Fell Mire", ("Fell the Profane // Fell Mire", 1)),
        ("1x Ashnod's Altar (ema) 218 *F* [Mana Advantage]", ("Ashnod's Altar", 1)),
        ("1 [2XM#310] Ash Barrens", ("Ash Barrens", 1)),
        ("Arid Mesa", ("Arid Mesa", 1)),
        ("  2 Island  ", ("Island", 2)),
        ("", None),
        ("# comment", None),
        ("// Sideboard", None),
        ("SIDEBOARD:", None),
        ("Deck", None),
        ("Name Death & Taxes", None),
    ],
)
def test_read_line(line, expected):
    assert read_line(line) == expected


def test_local_images_from_dir(images, tmp_path):
    imgs = LocalImages.from_dir(images)
    assert sorted(imgs.by_slug) == [
        "aminatou-the-fateshifter", "counterspell", "fell-the-profane", "plains", "unused-card",
    ]  # fmt: skip
    assert imgs.match("COUNTERSPELL") == images / "Counterspell.JPG"
    assert imgs.match("Fell the Profane // Fell Mire") == images / "fell-the-profane.jpg"
    assert imgs.match("Fell the Profane") == images / "fell-the-profane.jpg"
    assert imgs.match("Sol Ring") is None
    with pytest.raises(LocalImageError, match="--images dir not found"):
        LocalImages.from_dir(tmp_path / "nowhere")
    (tmp_path / "empty").mkdir()
    with pytest.raises(LocalImageError, match=r"no \.jpg or \.png"):
        LocalImages.from_dir(tmp_path / "empty")
    (images / "plains.jpg").write_bytes(b"x")
    with pytest.raises(LocalImageError, match="two images for 'plains'"):
        LocalImages.from_dir(images)


def test_split_deck(images):
    imgs = LocalImages.from_dir(images)
    s = split_deck(DECK, "moxfield", imgs, skip_basics=False, read_lines=moxfield_lines)
    assert [(c.pos, c.name, c.qty, c.image.name) for c in s.local] == [
        (1, "Aminatou, the Fateshifter", 1, "aminatou-the-fateshifter.jpg"),
        (3, "Fell the Profane // Fell Mire", 1, "fell-the-profane.jpg"),
        (4, "Plains", 2, "plains.png"),
        (6, "Counterspell", 1, "Counterspell.JPG"),  # an image wins over the printing on the line
    ]
    assert s.positions == [2, 5]
    assert s.fetch_text == "1 Sol Ring (SLZ) 114\n1 Delver of Secrets // Insectile Aberration (SLD) 2367\n"
    assert [c.text for c in s.unread] == ["1 Lightning Bolt"]  # moxfield needs a printing
    assert [p.name for p in s.unused] == ["unused-card.jpg"]
    assert (s.local_cards, s.fetched_cards, s.basics_left_out) == (5, 2, 0)
    assert s.summary() == (
        f"local images: 5 card(s) from {images}, 2 from Scryfall; 1 image(s) match no deck line"
    )
    note = s.unread_note("moxfield")
    assert note and "1 line(s)" in note and "'1 Lightning Bolt'" in note and "-f mtga" in note

    # Without --basics a local basic is left out, never fetched; so is a fetched one.
    s = split_deck(
        DECK + "1 Island (MOM) 278\n", "moxfield", imgs, skip_basics=True, read_lines=moxfield_lines
    )
    assert [c.name for c in s.basics] == ["Plains"] and "Plains" not in [c.name for c in s.local + s.fetched]
    assert (s.local_cards, s.fetched_cards, s.basics_left_out) == (3, 2, 3)
    assert "3 basic land(s) left out (--basics prints them)" in s.summary()


@needs_engine
def test_split_deck_with_the_engines_own_parsers(images):
    imgs = LocalImages.from_dir(images)
    s = split_deck(DECK, "moxfield", imgs, skip_basics=False, read_lines=engine.read_deck_lines)
    assert s.positions == [2, 5] and [c.text for c in s.unread] == ["1 Lightning Bolt"]
    s = split_deck(DECK, "mtga", imgs, skip_basics=False, read_lines=engine.read_deck_lines)
    assert s.positions == [2, 5, 7] and s.unread == []  # mtga reads bare lines too


def test_stage_copies_under_the_engines_names(images, tmp_path):
    front = tmp_path / "front"
    front.mkdir()
    s = split_deck(
        DECK, "moxfield", LocalImages.from_dir(images), skip_basics=False, read_lines=moxfield_lines
    )
    staged = local.stage(s.local, front)
    assert natsorted(p.name for p in front.iterdir()) == [
        "1AminatoutheFateshifter1.jpg", "3FelltheProfaneFellMire1.jpg", "4Plains1.png", "4Plains2.png",
        "6Counterspell1.jpg",
    ]  # fmt: skip
    assert len(staged) == 5 and (front / "4Plains2.png").read_bytes() == b"plains.png"
    assert not (front / "1AminatoutheFateshifter1.jpg").is_symlink()  # --trim rewrites staged files
    for f in front.iterdir():  # trim/manifest/backlog read the names like any fetched card's
        assert trim.CARD_FILE.match(f.name) and manifest.IMAGE_FILE.match(f.name)
    assert trim.CARD_FILE.match("3FelltheProfaneFellMire1.jpg")[1].lower() == trim.clean_name(
        "Fell the Profane // Fell Mire"
    )


def test_place_indices_renumbers_and_repoints():
    calls = []
    mod = SimpleNamespace(fetch_card=lambda index, qty, *a, **kw: calls.append(index))
    local.place_indices(mod, [])
    assert not hasattr(mod.fetch_card, "_mtgproxy_positions")  # nothing to renumber: left alone
    local.place_indices(mod, [2, 5, 9])
    mod.fetch_card(1, 1), mod.fetch_card(3, 1, "x", skip_basics=True), mod.fetch_card(4, 1)
    assert calls == [2, 9, 4]  # past the table: unchanged
    wrapped = mod.fetch_card
    local.place_indices(mod, [])
    assert mod.fetch_card is wrapped  # no stacked wrappers; a later run without --images numbers as-is
    mod.fetch_card(1, 1)
    assert calls[-1] == 1


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """game/ in tmp_path and a stand-in for the engine's fetch: it numbers the lines it can read
    1.., and its fetch_card / fetch_card_art write images the way the real ones name them."""
    game = tmp_path / "game"
    front, ds = game / "front", game / "double_sided"
    for name, d in (
        ("FRONT", front),
        ("DOUBLE_SIDED", ds),
        ("BACK", game / "back"),
        ("OUTPUT", game / "out"),
    ):
        d.mkdir(parents=True)
        monkeypatch.setattr(engine, name, d)
    monkeypatch.setattr(manifest, "STATE_FILE", game / ".manifest.json")
    monkeypatch.setattr(trim, "STATE_FILE", game / ".trimmed.json")
    monkeypatch.setattr(
        engine, "clean_images", lambda: [f.unlink() for d in (front, ds) for f in d.iterdir()]
    )
    looked_up: list[str] = []
    fetched_decks: list[str] = []

    def fetch_card_art(
        index,
        quantity,
        clean_card_name,
        card_set,
        card_collector_number,
        layout,
        all_parts=None,
        card_name=None,
        prefer_langs=None,
        front_img_dir=None,
        double_sided_dir=None,
        card_json=None,
    ):
        for n in range(1, quantity + 1):
            (Path(front_img_dir) / f"{index}{clean_card_name}{n}.png").write_bytes(b"front")
            if layout == "transform":
                (Path(double_sided_dir) / f"{index}{clean_card_name}{n}.png").write_bytes(b"back")

    def fetch_card(index, quantity, card_set, cn, ignore, name, *rest, skip_basics=False, token_copies=0):
        looked_up.append(name)
        if skip_basics and local.is_basic(name):
            return
        layout = "transform" if "//" in name else "normal"
        front_dir, ds_dir = str(engine.FRONT), str(engine.DOUBLE_SIDED)
        scry.fetch_card_art(
            index, quantity, local.clean(name), card_set, cn, layout, None, name, None, front_dir, ds_dir
        )

    scry = SimpleNamespace(fetch_card=fetch_card, fetch_card_art=fetch_card_art)

    def fetch_cards(deck_path, fmt, args):
        text = Path(deck_path).read_text()
        fetched_decks.append(text)
        lines = text.splitlines()
        cards = [v for v in moxfield_lines(lines, fmt) if v is not None]
        for k, (name, set_code, cn, qty) in enumerate(cards, 1):
            scry.fetch_card(k, qty, set_code, cn, False, name, skip_basics="--skip_basics" in args)

    monkeypatch.setattr(engine, "scryfall_module", lambda: scry)
    monkeypatch.setattr(engine, "fetch_cards", fetch_cards)
    monkeypatch.setattr(engine, "read_deck_lines", moxfield_lines)
    back = tmp_path / "back.png"
    back.write_bytes(b"back")
    return SimpleNamespace(front=front, ds=ds, looked_up=looked_up, decks=fetched_decks, back=back)


def _stage(tmp_path, fake, images, deck_text=DECK, **kw) -> Path:
    deck = tmp_path / "aminatou.txt"
    deck.write_text(deck_text)
    opts = BuildOptions(deck=str(deck), fmt="moxfield", images=images, back=fake.back, use_cache=False, **kw)
    build.stage_images(opts, ResolvedDeck("aminatou", deck, "moxfield"), None)
    return deck


def test_stage_images_interleaves_local_and_fetched_cards(tmp_path, images, fake_engine):
    _stage(tmp_path, fake_engine, images, include_basics=True)
    assert fake_engine.decks == [
        "1 Sol Ring (SLZ) 114\n1 Delver of Secrets // Insectile Aberration (SLD) 2367\n"
    ]  # the engine sees only the lines it fetches, as written, in the deck's format
    assert fake_engine.looked_up == ["Sol Ring", "Delver of Secrets // Insectile Aberration"]
    assert natsorted(p.name for p in fake_engine.front.iterdir()) == [
        "1AminatoutheFateshifter1.jpg", "2SolRing1.png", "3FelltheProfaneFellMire1.jpg", "4Plains1.png",
        "4Plains2.png", "5DelverofSecretsInsectileAberration1.png", "6Counterspell1.jpg",
    ]  # fmt: skip
    # Local images are one face: a local DFC name prints with the fronts, the fetched DFC goes duplex.
    assert [p.name for p in fake_engine.ds.iterdir()] == ["5DelverofSecretsInsectileAberration1.png"]
    main, duplex = manifest.partition(fronts_only=True, duplex_dfc=True)
    assert "3FelltheProfaneFellMire1.jpg" in [p.name for p in main]
    assert [p.name for p in duplex] == ["5DelverofSecretsInsectileAberration1.png"]
    # The manifest knows where each card came from.
    rec = manifest.Recorder.load()
    fell = rec.lookup(fake_engine.front / "3FelltheProfaneFellMire1.jpg")
    assert (fell.name, fell.set, fell.image) == (
        "Fell the Profane // Fell Mire",
        "",
        str(images / "fell-the-profane.jpg"),
    )
    sol = rec.lookup(fake_engine.front / "2SolRing1.png")
    assert (sol.name, sol.set, sol.cn, sol.image) == ("Sol Ring", "slz", "114", "")
    sheet = manifest.make_sheet(manifest.MAIN, main, "aminatou.pdf", 8, rec)
    assert [c.image != "" for c in sheet.cards] == [True, False, True, True, True, True]


def test_stage_images_basics_follow_the_basics_rule(tmp_path, images, fake_engine):
    _stage(tmp_path, fake_engine, images)  # default: basics skipped
    names = [p.name for p in fake_engine.front.iterdir()]
    assert not any("Plains" in n for n in names) and "Plains" not in fake_engine.looked_up
    assert len(names) == 5


def test_stage_images_all_local_never_runs_the_fetch(tmp_path, images, fake_engine):
    _stage(tmp_path, fake_engine, images, deck_text="1 Counterspell\n2 Plains\n", include_basics=True)
    assert fake_engine.decks == [] and fake_engine.looked_up == []
    assert sorted(p.name for p in fake_engine.front.iterdir()) == [
        "1Counterspell1.jpg", "2Plains1.png", "2Plains2.png",
    ]  # fmt: skip


def test_local_split_errors_and_bad_dir_keeps_the_last_images(tmp_path, images, fake_engine):
    deck = tmp_path / "d.txt"
    deck.write_text("1 Counterspell\n")
    with pytest.raises(BuildError, match="--images dir not found"):
        build.local_split(BuildOptions(images=tmp_path / "nope"), ResolvedDeck("d", deck, "mtga"))
    with pytest.raises(BuildError, match="works with the mtga, moxfield"):
        build.local_split(BuildOptions(images=images), ResolvedDeck("d", deck, "scryfall_json"))
    assert build.local_split(BuildOptions(), ResolvedDeck("d", deck, "mtga")) is None
    (fake_engine.front / "1Old1.png").write_bytes(b"x")
    with pytest.raises(BuildError, match="not found"):
        _stage(tmp_path, fake_engine, tmp_path / "nope")
    assert (fake_engine.front / "1Old1.png").exists()


def test_images_are_recorded_and_rebuild_passes_them_back(tmp_path, monkeypatch):
    o = BuildOptions(images=Path("/cards"))
    assert o.recorded()["images"] == "/cards" and BuildOptions().recorded()["images"] is None
    deck = tmp_path / "d.txt"
    deck.write_text("1 Counterspell\n")
    seen = {}

    def fake_stage(opts):
        seen["opts"] = opts
        return manifest.Recorder()

    monkeypatch.setattr(build, "stage_and_index", fake_stage)
    monkeypatch.setattr(manifest, "partition", lambda fronts_only, duplex_dfc: ([], []))
    monkeypatch.setattr(manifest, "cards_per_page", lambda paper, card: 8)
    info = SimpleNamespace(
        decklist=str(deck), options={"images": "/cards", "include_basics": True}, paper="a4",
        card_size="standard", fronts_only=True, cards=0, deferred=0, pdf=None, duplex_pdf=None,
    )  # fmt: skip
    manifest.rebuild(tmp_path, info)
    assert seen["opts"].images == Path("/cards") and seen["opts"].include_basics


def test_gather_copies_queued_images_by_slug(tmp_path, images):
    dest = tmp_path / "run" / "images"
    dest.mkdir(parents=True)
    (dest / "stale.png").write_bytes(b"old")
    cards = [
        ("Fell the Profane // Fell Mire", images / "fell-the-profane.jpg"),
        ("Plains", images / "plains.png"),
        ("Plains", images / "plains.png"),
    ]
    assert local.gather(cards, dest) == 2
    assert sorted(p.name for p in dest.iterdir()) == ["fell-the-profane.jpg", "plains.png"]
    assert LocalImages.from_dir(dest).match("Fell the Profane // Fell Mire")
    with pytest.raises(LocalImageError, match="local image missing"):
        local.gather([("Gone", images / "gone.jpg")], tmp_path / "x")
    assert not (tmp_path / "x").exists()  # checked before anything is written
    other = tmp_path / "plains.jpg"
    other.write_bytes(b"other deck's plains")
    with pytest.raises(LocalImageError, match="two different local images for 'Plains'"):
        local.gather([("Plains", images / "plains.png"), ("Plains", other)], dest)


@needs_engine
def test_cli_make_images_dry_run_counts(tmp_path, images):
    deck = tmp_path / "aminatou.txt"
    deck.write_text(DECK)
    out = tmp_path / "out"
    out.mkdir()
    args = ["make", str(deck), "-f", "moxfield", "-o", str(out), "--images", str(images), "--dry-run"]
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    assert f"local images: 3 card(s) from {images}, 2 from Scryfall; 2 basic land(s) left out" in r.output
    assert "1 image(s) match no deck line" in r.output and "'1 Lightning Bolt'" in r.output
    r = runner.invoke(app, [*args, "--basics"])
    assert f"local images: 5 card(s) from {images}, 2 from Scryfall; 1 image(s)" in r.output
    r = runner.invoke(
        app, ["make", str(deck), "-o", str(out), "--images", str(tmp_path / "nope"), "--dry-run"]
    )
    assert r.exit_code == 1 and "--images dir not found" in r.output
    r = runner.invoke(app, ["make", "--test", "--images", str(images), "-o", str(out)])
    assert r.exit_code == 1 and "cannot be combined with --test" in r.output
