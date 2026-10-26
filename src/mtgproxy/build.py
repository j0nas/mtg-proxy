"""The build: decklist → ./<deck>/{<deck>.pdf, <deck>-duplex.pdf?, CUT-NOTES.md, run.json}.

Output goes to ``<out_parent>/<name>/`` (default: paths.out_root(), ~/Desktop/proxies;
the repo itself is an implementation detail). Only OUR artifacts from a previous
run are removed, never the folder itself.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from . import engine, layout, local, manifest, mirror, notes, printing, printings, trim
from .backlog import Backlog, Entry
from .cache import ImageCache
from .decks import FetchedDeck, fetch_deck, is_deck_url
from .paths import DEFAULT_BACK, DUPLEX_OFFSET_FILE, NOTES_NAME, SCM, cache_dir, out_root
from .sidecar import RunInfo
from .testcards import write_test_cards

TEST_SHEET_NAME = "test-sheet"


class BuildError(Exception):
    """User-facing build failure."""


@dataclass
class BuildOptions:
    deck: str | None = None
    fmt: str = "mtga"
    out_parent: Path = field(default_factory=out_root)
    paper: str = "a4"
    card_size: str = "standard"
    layout: str | None = None  # 3x3 | 4x2, see layout.py; None: layout.default for the paper and card
    board: str = "main"
    fronts_only: bool = True
    duplex_dfc: bool = True
    fancy_art: bool = True
    alt_names: bool = False  # --alt-names: rank printings with another name on the card (printings.py)
    include_basics: bool = False
    token_copies: int = 0
    tokens_only: bool = False
    skip_fetch: bool = False
    test_mode: bool = False
    print_mode: bool = False
    fetch_args: list[str] = field(default_factory=list)
    use_cache: bool = True
    dry_run: bool = False
    back: Path = DEFAULT_BACK
    trims: list[trim.TrimSpec] = field(default_factory=list)
    defer_partial: bool = False  # partial last page of each sheet → BACKLOG.txt instead of the PDF
    front_only: list[str] = field(default_factory=list)  # DFC names to print as a single-sided front
    images: Path | None = None  # --images: your own card images, printed instead of fetching (local.py)

    def recorded(self) -> dict:
        """What manifest.rebuild needs to re-derive the sheets of this run later."""
        return {
            "fmt": self.fmt,
            "duplex_dfc": self.duplex_dfc,
            "fancy_art": self.fancy_art,
            "alt_names": self.alt_names,
            "include_basics": self.include_basics,
            "token_copies": self.token_copies,
            "tokens_only": self.tokens_only,
            "fetch_args": list(self.fetch_args),
            "images": str(self.images) if self.images else None,
        }


@dataclass
class BuildResult:
    name: str
    out: Path
    pdf: Path | None
    duplex_pdf: Path | None
    dfc_count: int
    cards: int
    windows_path: str | None = None
    mirror_failed: list[str] = field(default_factory=list)
    deferred: list[Entry] = field(default_factory=list)
    backlog: Path | None = None


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --- step 0: what are we building -------------------------------------------
@dataclass
class ResolvedDeck:
    name: str
    path: Path | None  # None for the test sheet
    fmt: str
    fetched: FetchedDeck | None = None


def _convert_windows_path(deck: str) -> Path | None:
    """Accept absolute Windows paths (C:\\...) pasted from Explorer under WSL."""
    if not shutil.which("wslpath"):
        return None
    try:
        conv = subprocess.run(
            ["wslpath", "-u", deck], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    p = Path(conv)
    return p if conv and p.is_file() else None


def resolve_deck(opts: BuildOptions) -> ResolvedDeck:
    if opts.test_mode:
        return ResolvedDeck(TEST_SHEET_NAME, None, opts.fmt)
    if not opts.deck:
        raise BuildError("no decklist given")
    if is_deck_url(opts.deck):
        fetched = fetch_deck(opts.deck, opts.board)
        name = fetched.file_stem
        if opts.tokens_only:
            name += "-tokens"
        # Written into the output folder by run_build once that folder exists.
        return ResolvedDeck(name, None, "mtga", fetched)
    path = Path(opts.deck)
    if not path.is_file():
        path = _convert_windows_path(opts.deck) or path
    if not path.is_file():
        raise BuildError(f"decklist not found: {opts.deck}")
    path = path.resolve()
    name = path.stem + ("-tokens" if opts.tokens_only else "")
    return ResolvedDeck(name, path, opts.fmt)


def prepare_out(out_parent: Path, name: str) -> Path:
    if not out_parent.is_dir():
        if out_parent != out_root():
            raise BuildError(f"--out dir not found: {out_parent}")
        out_parent.mkdir(parents=True)  # the default proxies folder, first build
    out = out_parent.resolve() / name
    out.mkdir(parents=True, exist_ok=True)
    return out


def clear_stale(out: Path, name: str) -> None:
    """Remove OUR artifacts from a previous run — called only once the new images are ready,
    so a run that fails while fetching leaves the previous PDF in place."""
    for stale in [out / f"{name}.pdf", out / f"{name}-duplex.pdf", out / NOTES_NAME]:
        stale.unlink(missing_ok=True)


# --- step 1-3: images --------------------------------------------------------
def fetch_args_for(opts: BuildOptions) -> list[str]:
    args: list[str] = []
    if not opts.include_basics:
        args.append("--skip_basics")
    if opts.token_copies > 0:
        args += ["--tokens", "--token_copies", str(opts.token_copies)]
    if opts.fancy_art:
        args += ["--prefer_showcase", "--prefer_extra_art"]
    return args + list(opts.fetch_args)


def local_split(opts: BuildOptions, deck: ResolvedDeck) -> local.Split | None:
    """--images: which deck lines print from the folder and which are fetched (None without it)."""
    if opts.images is None or opts.test_mode:
        return None
    if deck.fmt.lower() not in local.FORMATS:
        raise BuildError(f"--images works with the {', '.join(local.FORMATS)} formats, not {deck.fmt!r}")
    try:
        images = local.LocalImages.from_dir(opts.images)
    except local.LocalImageError as e:
        raise BuildError(str(e)) from e
    assert deck.path is not None
    return local.split_deck(
        deck.path.read_text(),
        deck.fmt,
        images,
        skip_basics="--skip_basics" in fetch_args_for(opts),
        read_lines=engine.read_deck_lines,
    )


def log_split(split: local.Split, fmt: str) -> None:
    log(split.summary())
    if note := split.unread_note(fmt):
        warn(note)


def stage_images(opts: BuildOptions, deck: ResolvedDeck, cache: ImageCache | None) -> None:
    if opts.test_mode:
        engine.clean_images()
        trim.reset_state()
        for f in engine.images_in(engine.BACK):
            f.unlink()
        count = per_page(opts)  # one full sheet
        # Double-sided test sheet: number each back so you can see which front it
        # belongs to (verifies back-page ordering, not just the offset).
        write_test_cards(count, engine.FRONT, engine.BACK, None if opts.fronts_only else engine.DOUBLE_SIDED)
        return
    if opts.skip_fetch:
        log("--skip-fetch: reusing images already in game/front/")
        return
    split = local_split(opts, deck)  # before clearing game/, so a bad --images keeps the last images
    engine.clean_images()
    trim.reset_state()
    if not opts.back.is_file():
        raise BuildError(f"card back image missing: {opts.back}")
    engine.set_back(opts.back)
    scryfall = engine.scryfall_module()
    if cache is not None:
        cache.install(scryfall)
    printings.install(scryfall, alt_names=opts.alt_names, foreign_ok=printings.prefers_lang(opts.fetch_args))
    rec = manifest.Recorder()
    rec.install(scryfall)
    local.place_indices(scryfall, split.positions if split else [])
    assert deck.path is not None
    if split is None:
        engine.fetch_cards(deck.path, deck.fmt, fetch_args_for(opts))
    else:
        log_split(split, deck.fmt)
        if split.fetched:
            # The engine reads a copy without the local lines, in the deck's own format.
            with tempfile.TemporaryDirectory() as tmp:
                rest = Path(tmp) / deck.path.name
                rest.write_text(split.fetch_text)
                engine.fetch_cards(rest, deck.fmt, fetch_args_for(opts))
        for card, _ in local.stage(split.local, engine.FRONT):
            rec.record(card.pos, local.clean(card.name), card.name, "", "", image=str(card.image))
    rec.save()
    if cache is not None and (cache.hits or cache.misses):
        log(f"image cache: {cache.hits} hit(s), {cache.misses} download(s) → {cache.dir}")


TOKEN_FILE = re.compile(r"^(\d+)(.+)(_token\d+\.\w+)$")
TOKEN_INDEX_BASE = 10000  # above any deck index, so tokens land after every card in the PDF


def move_tokens_last() -> int:
    """Re-index token images so they sort after the deck cards, ordered by token name.

    The engine names a token after the card that produces it (``<index><Token>_token<n>``),
    which scatters tokens through the PDF. Files are renamed in game/front and, for
    double-faced tokens, game/double_sided alike. Idempotent.
    """
    names = sorted(
        {m.group(2) for f in engine.images_in(engine.FRONT) if (m := TOKEN_FILE.match(f.name))}, key=str.lower
    )
    rank = {name: TOKEN_INDEX_BASE + i for i, name in enumerate(names)}
    moved = 0
    for d in (engine.FRONT, engine.DOUBLE_SIDED):
        for f in engine.images_in(d):
            m = TOKEN_FILE.match(f.name)
            if not m or m.group(2) not in rank:
                continue
            new = f.with_name(f"{rank[m.group(2)]}{m.group(2)}{m.group(3)}")
            if new != f:
                f.rename(new)
                moved += 1
    return moved


def prune_to_tokens() -> int:
    """--tokens-only: drop every non-token image (names are <index><Name>_token<n>.png)."""
    pruned = 0
    for d in (engine.FRONT, engine.DOUBLE_SIDED):
        for f in engine.images_in(d):
            if "_token" not in f.name:
                f.unlink()
                pruned += 1
    return pruned


def drop_backs(names: list[str]) -> list[str]:
    """Print these double-faced cards as ordinary single-sided cards: remove their back
    images so the engine lays them out with the fronts. Returns the names that matched."""
    keys = {trim.clean_name(n): n for n in names}
    hit: set[str] = set()
    for f in engine.images_in(engine.DOUBLE_SIDED):
        m = trim.CARD_FILE.match(f.name)
        if m and (name := keys.get(m.group(1).lower())):
            f.unlink()
            hit.add(name)
    return [n for n in names if n in hit]


def stage_and_index(opts: BuildOptions) -> manifest.Recorder:
    """Fetch (or reuse) the images of ``opts.deck`` and put them in sheet order;
    returns the printings recorder for the manifest. Shared by the build and by
    ``manifest.rebuild`` for runs made before manifests existed."""
    deck = resolve_deck(opts)
    cache = ImageCache(cache_dir()) if opts.use_cache else None
    stage_images(opts, deck, cache)
    if move_tokens_last():
        log("tokens moved to the end of the sheet order")
    if opts.tokens_only:
        prune_to_tokens()
    return manifest.Recorder.load()


# --- step 4: PDFs ------------------------------------------------------------
def per_page(opts: BuildOptions) -> int:
    """Cards on one sheet of this paper, card size and layout."""
    try:
        return manifest.cards_per_page(opts.paper, opts.card_size, opts.layout)
    except layout.LayoutError as e:
        raise BuildError(str(e)) from e


def pdf_base_args(opts: BuildOptions) -> list[str]:
    # --extend_corners 3.5mm: Scryfall scans have rounded corners; this fills the
    # corner bleed so cut cards don't get white corner slivers. Every sheet is 3-mark (cutting.py).
    return [
        "--card_size", opts.card_size,
        "--paper_size", opts.paper,
        "--registration", "3",
        "--extend_corners", "3.5mm",
        *layout.engine_flags(opts.layout),
    ]  # fmt: skip


# Back pages carry no registration marks: the cut always scans the front, and turned over the long
# edge a back's square lands right behind the front's top-right L. On double-sided glossy it showed
# through enough that TB123 misread that L on every scan of a sheet (2026-10-26).
BACK_PAGES = ["--no_back_marks"]


def duplex_offset_args() -> list[str]:
    """Saved duplex offset (data/offset_data.json) → --load_offset.

    create_pdf.py only reads its own data/, so the tracked file is copied there
    first — the printer's offset travels with this repo, not the engine clone.
    """
    if not DUPLEX_OFFSET_FILE.is_file():
        return []
    (SCM / "data").mkdir(exist_ok=True)
    shutil.copyfile(DUPLEX_OFFSET_FILE, SCM / "data" / "offset_data.json")
    return ["--load_offset"]


@dataclass
class PdfOutputs:
    pdf: Path | None
    duplex_pdf: Path | None
    dfc_count: int
    main: list[Path] = field(default_factory=list)  # files on the main sheet, in page order
    duplex: list[Path] = field(default_factory=list)  # files on the duplex sheet, in page order
    deferred: list[Path] = field(default_factory=list)  # partial-page files kept out (--defer-partial)


def _defer(files: list[Path], per_page: int) -> tuple[list[Path], list[Path]]:
    keep = len(files) // per_page * per_page
    return files[:keep], files[keep:]


def _view(parent: Path, name: str, files: list[Path]) -> Path:
    """A directory of symlinks: the engine reads a directory, and game/* must stay untouched."""
    d = parent / name
    d.mkdir()
    for f in files:
        (d / f.name).symlink_to(manifest.view_target(f))
    return d


def build_pdfs(opts: BuildOptions, out: Path, name: str) -> PdfOutputs:
    base = pdf_base_args(opts)
    offset = duplex_offset_args()
    game_pdf = engine.OUTPUT / "game.pdf"
    main, duplex = manifest.partition(opts.fronts_only, opts.duplex_dfc)
    deferred: list[Path] = []
    if opts.defer_partial:
        n = per_page(opts)
        main, d1 = _defer(main, n)
        duplex, d2 = _defer(duplex, n)
        deferred = d1 + d2
        if deferred:
            log(f"--defer-partial: {len(deferred)} card(s) of a partial page go to the backlog instead")
    dfc_count = 0

    if not opts.fronts_only:
        # --backs: one double-sided PDF; DFCs get their real backs, the rest the card back.
        if offset:
            log(f"applying saved duplex offset from {DUPLEX_OFFSET_FILE}")
        else:
            log(
                "NOTE: no saved duplex offset — run the calibration once before double-sided decks (see README)."
            )
        if not main:
            log("NOTE: nothing left to print on this sheet.")
            return PdfOutputs(None, None, 0, [], [], deferred)
        with tempfile.TemporaryDirectory() as tmp:
            front = _view(Path(tmp), "front", main)
            backs = _view(
                Path(tmp),
                "backs",
                [engine.DOUBLE_SIDED / f.name for f in main if (engine.DOUBLE_SIDED / f.name).is_file()],
            )
            engine.create_pdf(
                [
                    *base,
                    *offset,
                    *BACK_PAGES,
                    "--front_dir_path",
                    str(front),
                    "--double_sided_dir_path",
                    str(backs),
                ]
            )
        pdf = out / f"{name}.pdf"
        shutil.copyfile(game_pdf, pdf)
        return PdfOutputs(pdf, None, 0, main, [], deferred)

    # Fronts only. Symlink views leave game/* untouched (safe for --skip-fetch).
    with tempfile.TemporaryDirectory() as tmp:
        view = Path(tmp)
        main_front, dfc_front, no_backs = (
            _view(view, "main_front", main),
            _view(view, "dfc_front", duplex),
            _view(view, "no_backs", []),
        )
        if opts.duplex_dfc:
            dfc_count = len(duplex)
        else:
            # Each face of a double-faced card is its own single-sided card in the
            # main PDF (the back slots in next to its front, see manifest.partition).
            dfc_count = sum(1 for f in main if f.stem.endswith("-back"))
            if dfc_count:
                log(
                    f"double-faced cards: {dfc_count} — printing both faces as separate cards (--split-faces)"
                )

        pdf: Path | None = None
        if main:
            engine.create_pdf(
                [
                    *base,
                    "--only_fronts",
                    "--front_dir_path",
                    str(main_front),
                    "--double_sided_dir_path",
                    str(no_backs),
                ]
            )
            pdf = out / f"{name}.pdf"
            shutil.copyfile(game_pdf, pdf)
        elif deferred and not duplex:
            log("NOTE: nothing left to print on this sheet.")
        else:
            log("NOTE: every card in this deck is double-sided — no fronts-only PDF to build.")

        duplex_pdf: Path | None = None
        if opts.duplex_dfc and duplex:
            log(f"double-sided cards: {dfc_count} — building {name}-duplex.pdf")
            if not offset:
                log(
                    "NOTE: no saved duplex offset — run the calibration once for clean front/back alignment (see README)."
                )
            duplex_out = engine.OUTPUT / "duplex.pdf"
            engine.create_pdf(
                [
                    *base,
                    *offset,
                    *BACK_PAGES,
                    "--front_dir_path",
                    str(dfc_front),
                    "--output_path",
                    str(duplex_out),
                ]
            )
            duplex_pdf = out / f"{name}-duplex.pdf"
            shutil.copyfile(duplex_out, duplex_pdf)
    return PdfOutputs(pdf, duplex_pdf, dfc_count, main, duplex, deferred)


# --- the whole thing ---------------------------------------------------------
def resolve_layout(opts: BuildOptions) -> BuildOptions:
    """``opts`` with its layout settled (None: the default for the paper and card); raises
    BuildError for a layout this paper or card size can't have."""
    opts = replace(opts, layout=opts.layout or layout.default(opts.paper, opts.card_size))
    try:
        layout.check(opts.layout, opts.paper, opts.card_size)
    except layout.LayoutError as e:
        raise BuildError(str(e)) from e
    return opts


def run_build(opts: BuildOptions) -> BuildResult:
    if opts.tokens_only and opts.test_mode:
        raise BuildError("--tokens-only cannot be combined with --test")
    if opts.images and opts.test_mode:
        raise BuildError("--images cannot be combined with --test")
    opts = resolve_layout(opts)
    engine.ensure_engine()

    deck = resolve_deck(opts)
    name = deck.name
    out = prepare_out(opts.out_parent, name)
    if deck.fetched is not None:
        deck.path = out / f"{name}.txt"
        deck.path.write_text(deck.fetched.text)
        log(
            f"{deck.fetched.source}: {deck.fetched.name!r} [{deck.fetched.board}] — {len(deck.fetched.lines)} entries → {deck.path}"
        )
    if opts.paper == "a3":
        warn('NOTE: A3 sheets need the 12x24" cutting mat — the standard 12x12" mat is too short.')

    if opts.dry_run:
        split = local_split(opts, deck)
        log(
            f"dry run: {name} → {out} (paper {opts.paper}, {opts.layout} layout of {per_page(opts)} cards, "
            f"format {deck.fmt}, fetch args {fetch_args_for(opts)}"
            f"{', alternate names allowed' if opts.alt_names else ''})"
        )
        if split:
            log_split(split, deck.fmt)
        return BuildResult(name, out, None, None, None, 0, 0)

    cache = ImageCache(cache_dir()) if opts.use_cache and not opts.test_mode else None
    stage_images(opts, deck, cache)
    if move_tokens_last():
        log("tokens moved to the end of the sheet order")
    rec = manifest.Recorder.load()
    local_files = {f.name for f in engine.images_in(engine.FRONT) if rec.lookup(f).image}
    if opts.trims:
        trimmed, unmatched = trim.apply_trims(opts.trims, local_files)
        if unmatched:
            raise BuildError(f"--trim: no card named {', '.join(repr(n) for n in unmatched)} in this deck")
        log(
            f"edge trim: {len(trimmed)} image(s) — " + ", ".join(f"{s.name} ({s.mm:g}mm)" for s in opts.trims)
        )

    if opts.front_only:
        dropped = drop_backs(opts.front_only)
        missing = [n for n in opts.front_only if n not in dropped]
        if missing:
            raise BuildError(f"front-only: no double-faced card named {', '.join(repr(n) for n in missing)}")
        log(f"front face only (single-sided): {', '.join(dropped)}")

    if opts.tokens_only:
        pruned = prune_to_tokens()
        log(f"--tokens-only: dropped {pruned} non-token image(s)")
        if not engine.images_in(engine.FRONT):
            raise BuildError("this deck produces no tokens")

    fronts = engine.images_in(engine.FRONT)
    cards = len(fronts)
    if cards == 0:
        raise BuildError("no card images in game/front/")
    if own := sum(f.name in local_files for f in fronts):
        log(f"cards: {cards} ({cards - own} fetched, {own} from local images)")
    else:
        log(f"cards fetched: {cards}")

    clear_stale(out, name)
    pdfs = build_pdfs(opts, out, name)

    # Per-slot manifest: what is on which page, and the exact printing, so
    # `mtg-proxy redo` can queue a miscut or a skipped page later.
    sheets: dict[str, manifest.Sheet] = {}
    deferred: list[Entry] = []
    if not opts.test_mode:
        rec = manifest.Recorder.load()
        n = per_page(opts)
        sheets[manifest.MAIN] = manifest.make_sheet(
            manifest.MAIN, pdfs.main, pdfs.pdf.name if pdfs.pdf else None, n, rec
        )
        if pdfs.duplex:
            sheets[manifest.DUPLEX] = manifest.make_sheet(
                manifest.DUPLEX, pdfs.duplex, pdfs.duplex_pdf.name if pdfs.duplex_pdf else None, n, rec
            )
        trims = {s.name: s.mm for s in opts.trims}
        for f in pdfs.deferred:
            c = rec.lookup(f)
            c.dfc = (engine.DOUBLE_SIDED / f.name).is_file()
            deferred.append(Entry.from_slot(c, name, "deferred", _trim_for(c, trims)))
    printed = len(pdfs.main) + len(pdfs.duplex)

    (out / NOTES_NAME).write_text(
        notes.render(
            notes.NotesContext(
                name=name,
                paper=opts.paper,
                card_size=opts.card_size,
                cards=printed,
                fronts_only=opts.fronts_only,
                duplex_dfc=opts.duplex_dfc,
                dfc_count=pdfs.dfc_count,
                layout=opts.layout,
            )
        )
    )
    RunInfo(
        name=name,
        paper=opts.paper,
        card_size=opts.card_size,
        cards=printed,
        fronts_only=opts.fronts_only,
        generated=date.today().isoformat(),
        layout=opts.layout,
        pdf=pdfs.pdf.name if pdfs.pdf else None,
        duplex_pdf=pdfs.duplex_pdf.name if pdfs.duplex_pdf else None,
        dfc_count=pdfs.dfc_count,
        trims={s.name: s.mm for s in opts.trims},
        decklist=deck.path.name
        if deck.path and deck.path.parent == out
        else (str(deck.path) if deck.path else None),
        source=deck.fetched.source if deck.fetched else None,
        deferred=len(deferred),
        options=opts.recorded(),
        sheets={k: v.as_dict() for k, v in sheets.items()},
    ).write(out)

    result = BuildResult(name, out, pdfs.pdf, pdfs.duplex_pdf, pdfs.dfc_count, printed)
    if deferred:
        backlog = Backlog.at(out.parent)
        backlog.add(deferred)
        backlog.save()
        result.deferred, result.backlog = deferred, backlog.path

    # Optional: print through the PC's Epson driver. The duplex PDF is deliberately NOT
    # sent — manual duplex on photo paper is a hands-on job.
    if opts.print_mode and pdfs.pdf:
        try:
            printing.print_pdf(pdfs.pdf, "plain" if opts.test_mode else "glossy", say=log)
        except printing.PrintError as e:
            warn(f"warning: print failed: {e}. Print {pdfs.pdf} with: print-sheet {pdfs.pdf}")
        if pdfs.duplex_pdf:
            log(
                f"NOTE: {pdfs.duplex_pdf.name} not sent — print it by hand with manual duplex (long-edge flip)."
            )

    win_out = mirror.windows_out_dir()
    if win_out is not None and (win_out.parent.is_dir() or Path("/mnt/c/Users").is_dir()):
        m = mirror.mirror(out, name, win_out)
        result.windows_path, result.mirror_failed = m.windows_path, m.failed
    return result


def _trim_for(c: manifest.SlotCard, trims: dict[str, float]) -> float | None:
    """The --trim width that applied to this card in the run (run.json keys trims by the name as typed).
    ``all`` skips tokens and local images, as trim.apply_trims does."""
    if c.token:
        return None
    by_key = {trim.clean_name(n): mm for n, mm in trims.items()}
    return by_key.get(trim.clean_name(c.name), None if c.image else by_key.get("all"))


def print_summary(r: BuildResult, opts: BuildOptions) -> None:
    log("")
    log("=== DONE ===")
    if not r.pdf and not r.duplex_pdf:
        log(f"  (no sheet to print: all {len(r.deferred)} card(s) went to the backlog)")
    if r.pdf:
        log(f"  PDF:      {r.pdf}")
    if r.duplex_pdf:
        log(f"  Duplex:   {r.duplex_pdf} ({r.dfc_count} double-sided cards — manual duplex, long-edge flip)")
    log(f"  Notes:    make-proxies --notes {r.name}")
    if r.out.parent == out_root().resolve():
        log("  Cut:      cut-proxies   (Enter cuts the newest run, this one)")
    else:
        log(f"  Cut:      cd {os.path.relpath(r.out)} && cut-proxies   (reads run.json)")
    if r.deferred:
        log(f"  Backlog:  {len(r.deferred)} card(s) deferred → {r.backlog}   (mtg-proxy backlog)")
    if r.windows_path:
        log(f"  Windows:  {r.windows_path}")
    if r.mirror_failed:
        log("")
        log("  *** MIRROR INCOMPLETE — the Windows copy of these files is STALE (old run!):")
        for f in r.mirror_failed:
            log(f"  ***   {f}")
        log("  *** Close the file on the Windows side (PDF viewer) and rerun,")
        log("  *** or copy it manually from the WSL output path above. Do NOT print the stale copy.")
