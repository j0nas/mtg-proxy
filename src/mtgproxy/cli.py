"""mtg-proxy command line. ``make-proxies.sh`` / ``cut-proxies.sh`` / ``save-offset.sh`` are thin shims onto it."""

from __future__ import annotations

import select
import shutil
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from click.core import ParameterSource

from . import __version__, build, cutmenu, cutting, engine, local, manifest, notes, printing, trim
from . import backlog as backlog_mod
from .backlog import Backlog, BacklogError, Entry
from .cache import ImageCache
from .decks import DeckError, is_double_sided
from .layout import NOMINAL_CARDS, SINGLE_SIDED, LayoutError
from .layout import check as check_layout
from .layout import default as default_layout
from .manifest import ManifestError
from .paths import DEFAULT_BACK, NOTES_NAME, SCM, cache_dir
from .sidecar import RunInfo, find_sidecar

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Decklist in, print-ready PDF out, cut on the Cameo (ET-8550 + Cameo 5 Alpha).",
)


def parse_start(value: str) -> tuple[float, float]:
    try:
        top, left = (float(v) for v in value.split(","))
    except ValueError:
        fail(f"--scan-start wants TOP,LEFT in mm, got {value!r}")
    return top, left


LAYOUT_HELP = (
    "3x3 (the default on A4 with standard cards): 9 cards on portrait A4, marks 3.5 mm from the sides "
    "and 9 mm from the top and bottom, fronts only | 4x2: 8 cards on landscape A4, the engine's own "
    "layout (the default on any other paper or card)"
)


def fail(msg: str, code: int = 1) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mtg-proxy {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    pass


@app.command(
    context_settings={"allow_extra_args": True},
    help=(
        "Build ./<deck>/ from a decklist file or a Moxfield/Archidekt deck URL. "
        "Anything after -- is passed to the engine's fetch.py (e.g. -- --prefer_set sld)."
    ),
)
def make(
    ctx: typer.Context,
    deck: Annotated[
        str | None, typer.Argument(help="decklist path (cwd-relative), deck URL, or run name with --notes")
    ] = None,
    fmt: Annotated[str, typer.Option("-f", "--format", help="decklist format")] = "mtga",
    out: Annotated[
        Path, typer.Option("-o", "--out", help="parent dir for <deckname>/", file_okay=False)
    ] = Path("."),
    paper: Annotated[str, typer.Option("-p", "--paper", help="a4 | letter | a3 ...")] = "a4",
    sheet_layout: Annotated[str | None, typer.Option("--layout", help=LAYOUT_HELP)] = None,
    board: Annotated[str, typer.Option(help="deck URL board: main | side | considering")] = "main",
    backs: Annotated[
        bool, typer.Option("--backs/--fronts-only", help="double-sided cards (default: fronts only)")
    ] = False,
    basics: Annotated[
        bool, typer.Option("--basics", help="include basic lands (skipped by default)")
    ] = False,
    images: Annotated[
        Path | None,
        typer.Option(
            "--images",
            help=(
                "folder of your own card images, one per card, named by its front face "
                "(fell-the-profane.jpg, norn-s-annex.png): those deck lines print from the image, "
                "single-sided, the rest is fetched"
            ),
            file_okay=False,
        ),
    ] = None,
    tokens: Annotated[
        int | None, typer.Option("-t", "--tokens", help="also N of each distinct token (no emblems)", min=1)
    ] = None,
    tokens_only: Annotated[
        int | None, typer.Option("--tokens-only", help="ONLY N of each token, no deck cards", min=1)
    ] = None,
    split_faces: Annotated[
        bool,
        typer.Option(
            "--split-faces/--duplex",
            help="each DFC face as its own card (default: separate manual-duplex PDF)",
        ),
    ] = False,
    plain: Annotated[
        bool, typer.Option("--plain", help="normal frames (default prefers showcase/extended/full art)")
    ] = False,
    skip_fetch: Annotated[
        bool, typer.Option("--skip-fetch", help="reuse already-downloaded card images")
    ] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="bypass the persistent image cache")] = False,
    test: Annotated[
        bool, typer.Option("--test", help="ink-light placeholder gauge sheet, no Scryfall")
    ] = False,
    print_: Annotated[bool, typer.Option("--print", help="send the PDF straight to CUPS at 100%")] = False,
    printer: Annotated[
        str | None,
        typer.Option(
            "--printer",
            help="CUPS destination for --print (default: 4x2-glossy instance, else default printer)",
        ),
    ] = None,
    back: Annotated[
        Path, typer.Option(help="card back image for --backs runs", dir_okay=False)
    ] = DEFAULT_BACK,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="resolve the decklist and stop (no Scryfall, no PDF)")
    ] = False,
    show_notes: Annotated[
        bool,
        typer.Option("--notes", help="show CUT-NOTES of a previous run (DECK = run name, default latest)"),
    ] = False,
    defer_partial: Annotated[
        bool,
        typer.Option(
            "--defer-partial/--all-pages",
            help="leave each sheet's partial last page out of the PDF and queue those cards in BACKLOG.txt",
        ),
    ] = False,
    trims: Annotated[
        list[str] | None,
        typer.Option(
            "--trim",
            help=(
                "replace a card's outer edge ring before the bleed is built (borderless scans with a dark rim): "
                '--trim "Temple Garden" (0.3mm), --trim "Temple Garden:0.5", --trim all. Repeatable.'
            ),
        ),
    ] = None,
) -> None:
    if show_notes:
        _notes(deck, out)
        return
    if test and deck is not None:
        fail(f"--test takes no decklist (got {deck!r}; a printer name goes in --printer)")
    if tokens is not None and tokens_only is not None:
        fail("--tokens and --tokens-only are mutually exclusive")
    try:
        trim_specs = [trim.parse_spec(t) for t in trims or []]
    except trim.TrimError as e:
        fail(str(e))
    opts = build.BuildOptions(
        deck=deck,
        fmt=fmt,
        out_parent=out,
        paper=paper,
        layout=sheet_layout,
        board=board,
        fronts_only=not backs,
        duplex_dfc=not split_faces,
        fancy_art=not plain,
        include_basics=basics,
        token_copies=tokens_only or tokens or 0,
        tokens_only=tokens_only is not None,
        skip_fetch=skip_fetch,
        test_mode=test,
        print_mode=print_,
        printer=printer,
        fetch_args=list(ctx.args),
        use_cache=not no_cache,
        dry_run=dry_run,
        back=back,
        trims=trim_specs,
        defer_partial=defer_partial,
        images=images.resolve() if images else None,  # absolute: run.json keeps it for rebuilds
    )
    try:
        result = build.run_build(opts)
    except (build.BuildError, DeckError, engine.EngineMissing, RuntimeError) as e:
        fail(str(e))
    if opts.dry_run:
        return
    build.print_summary(result, opts)
    if result.mirror_failed:
        raise typer.Exit(1)


def _notes(name: str | None, parent: Path) -> None:
    parent = parent.resolve()
    if name:
        path = parent / name / NOTES_NAME
        if not path.is_file():
            fail(f"no notes for '{name}' ({path})")
    else:
        found = notes.latest_notes(parent)
        if found is None:
            fail(f"no make-proxies output under {parent}")
        path = found
    notes.show(path)


@app.command(name="notes", help="Show the CUT-NOTES of a previous run (newest below cwd by default).")
def notes_cmd(
    name: Annotated[str | None, typer.Argument(help="run folder name")] = None,
    out: Annotated[Path, typer.Option("-o", "--out", help="parent dir to search", file_okay=False)] = Path(
        "."
    ),
) -> None:
    _notes(name, out)


@app.command(
    help=(
        "Cut a printed sheet on the Cameo 5 Alpha. Reads run.json from the current directory (or --run) "
        "for paper/card/layout; flags override. With no flags at a terminal, asks first "
        "(Enter cuts the default)."
    ),
)
def cut(
    ctx: typer.Context,
    run: Annotated[
        Path | None, typer.Option("--run", help="run.json (or its folder) of the sheet to cut")
    ] = None,
    paper: Annotated[str | None, typer.Option("-p", "--paper")] = None,
    card: Annotated[str | None, typer.Option("-c", "--card")] = None,
    sheet_layout: Annotated[
        str | None,
        typer.Option(
            "--layout", help="3x3 | 4x2 — MUST match the printed sheet (default: run.json's, else 3x3 on A4)"
        ),
    ] = None,
    preset: Annotated[
        str,
        typer.Option(
            "--preset",
            help="blade settings: laminate (force 20, speed 25, depth 5, 3 passes) | "
            "paper (force 10, speed 5, depth 1, 1 pass); the flags below override",
        ),
    ] = "laminate",
    force: Annotated[int | None, typer.Option(min=1, max=40)] = None,
    speed: Annotated[int | None, typer.Option(min=1, max=30)] = None,
    depth: Annotated[int | None, typer.Option(min=0, max=10, help="AutoBlade depth")] = None,
    passes: Annotated[int | None, typer.Option(min=1, max=8)] = None,
    y_off: Annotated[
        float | None, typer.Option("--y-off", help="shift cuts down by MM (default: data/cut_offset.json)")
    ] = None,
    x_off: Annotated[
        float | None, typer.Option("--x-off", help="shift cuts right by MM (default: data/cut_offset.json)")
    ] = None,
    ble: Annotated[
        bool, typer.Option("--ble/--usb", help="connect over Bluetooth LE (default) or the USB cable")
    ] = True,
    ble_name: Annotated[
        str, typer.Option("--ble-name", help="advertised BLE name; a substring is enough")
    ] = "CAMEO 5 ALPHA",
    scan: Annotated[bool, typer.Option("--scan", help="list nearby BLE devices, then stop")] = False,
    svg: Annotated[
        Path | None, typer.Option("--svg", help="cut this SVG instead of generating one (page-sized, mm)")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("-n", "--dry-run", help="no machine needed; writes output/cut/<name>.cmds")
    ] = False,
    reg_inset: Annotated[
        float | None,
        typer.Option(
            "--reg-inset",
            min=3,
            max=30,
            help="mark corners' distance from the paper edges in mm, all round (default: the layout's, "
            "10; 3x3: 3.5 from the sides, 9 from the top and bottom)",
        ),
    ] = None,
    reg_length: Annotated[
        float | None,
        typer.Option(
            "--reg-length", help="printed mark leg length in mm (default: the layout's, 9.4; 3x3: 6)"
        ),
    ] = None,
    reg_thickness: Annotated[
        float, typer.Option("--reg-thickness", help="printed mark line thickness in mm")
    ] = 1.0,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe", help="scan the marks and log what the machine reports before and after; cut nothing"
        ),
    ] = False,
    proof: Annotated[
        bool,
        typer.Option(
            "--proof",
            help="after the scan, score the square's outline and each L over the print and wait: y cuts, "
            "r rescans, q stops",
        ),
    ] = False,
    scan_start: Annotated[
        list[str] | None,
        typer.Option(
            "--scan-start",
            metavar="TOP,LEFT",
            help="where the mark search starts, mm from the loaded origin; repeat to retry in order "
            "(default: 2 mm into the square, 6 mm left of it or 1 mm from the edge: 11,1 on a 3x3 "
            "sheet, 12,4 on a 4x2)",
        ),
    ] = None,
) -> None:
    if scan:
        raise typer.Exit(cutting.ble_scan())
    if preset not in cutting.PRESETS:
        fail(f"--preset must be one of {', '.join(cutting.PRESETS)}, got {preset!r}")
    plain = False
    if _cut_menu_wanted(ctx):
        picked = cutmenu.ask(Path.cwd(), "Bluetooth" if ble else "USB")
        if picked is None:
            raise typer.Exit()
        run, plain, preset = picked.run, picked.run is None, picked.preset
        dry_run, proof, probe = (picked.mode == m for m in ("dry-run", "proof", "probe"))
    blade = cutting.PRESETS[preset]
    info: RunInfo | None = None
    sidecar = None if plain else find_sidecar(run)
    if sidecar is not None:
        info = RunInfo.read(sidecar)
        if info.registration != "3":
            fail(
                f"{info.name} was printed with {info.registration}-mark registration ({sidecar}). "
                "4-mark sheets are no longer supported: rebuild and reprint"
            )
        typer.echo(
            f"cut-proxies: using {sidecar} ({info.name}: {info.paper}/{info.card_size}, {info.layout})"
        )
    elif run is not None:
        fail(f"no run.json at {run}")
    elif not plain:
        typer.echo("cut-proxies: no run.json in the current directory — using flags/defaults", err=True)
    paper = paper or (info.paper if info else "a4")
    card = card or (info.card_size if info else "standard")
    o = cutting.CutOptions(
        paper=paper,
        card_size=card,
        layout=sheet_layout or (info.layout if info else default_layout(paper, card)),
        force=blade.force if force is None else force,
        speed=blade.speed if speed is None else speed,
        depth=blade.depth if depth is None else depth,
        passes=blade.passes if passes is None else passes,
        x_off=x_off, y_off=y_off,
        connection="ble" if ble else "usb", ble_name=ble_name,
        svg=svg, dry_run=dry_run,
        reg_length=reg_length, reg_thickness=reg_thickness, reg_inset=reg_inset,
        label=info.name if info else None,
        probe=probe, proof=proof, scan_starts=[parse_start(v) for v in scan_start or []] or None,
    )  # fmt: skip
    try:
        cutting.check(o)  # before the loop: a retry can't fix the options
    except cutting.CutError as e:
        fail(str(e))
    job, sheet = o, 1
    while True:
        try:
            rc = cutting.run_cut(o)
        except cutting.CutError as e:
            if not _at_terminal():
                fail(str(e))
            typer.echo(f"error: {e}", err=True)
            if cutmenu.after_error(sheet) == "retry":
                continue
            raise typer.Exit(1) from None
        except (engine.EngineMissing, LayoutError, RuntimeError) as e:
            fail(str(e))
        if rc or o.dry_run or o.probe:
            break
        if not _at_terminal():
            typer.echo("cut-proxies: not through? Rerun with --passes 1 (it rescans the marks).")
            break
        then = _await_next_sheet(o, sheet)
        if then == "menu":
            then = cutmenu.after_cut(sheet, proof=job.proof)
        if then == "next":  # a batch is proofed once: its first sheet's marks passed
            o, sheet = replace(job, proof=False), sheet + 1
        elif then == "next-proof":
            o, sheet = job, sheet + 1
        elif then == "pass":
            o = replace(job, passes=1, proof=False)  # same sheet, marks already proven
        else:
            break
    raise typer.Exit(rc)


COUNTDOWN_S = 5  # between the mat coming back in and the next sheet's scan


def _await_next_sheet(o: cutting.CutOptions, sheet: int) -> str:
    """Watch the mat after a sheet: "next" once it went out and came back and the countdown ran
    out, "menu" when the operator pressed Enter or the mat can't be watched."""
    typer.echo(
        f"cut-proxies: sheet {sheet} done. Lift a corner. Through? Unload, load the next sheet and it "
        "starts by itself. Enter: menu (one more pass, done). Ctrl-C: stop."
    )
    try:
        if not cutting.wait_for_next_sheet(o, _enter_within):
            return "menu"
    except cutting.CutError as e:
        typer.echo(f"cut-proxies: {e}; choose from the menu", err=True)
        return "menu"
    for left in range(COUNTDOWN_S, 0, -1):
        typer.echo(
            f"\rcut-proxies: mat loaded: sheet {sheet + 1} starts in {left} s (Enter: menu) ", nl=False
        )
        if _enter_within(1.0):
            typer.echo()
            return "menu"
    typer.echo()
    return "next"


def _enter_within(seconds: float) -> bool:
    """True when the operator pressed Enter within ``seconds`` (the line is consumed)."""
    ready, _, _ = select.select([sys.stdin], [], [], seconds)
    if ready:
        sys.stdin.readline()
    return bool(ready)


# The connection is never asked in the menu, only set by flag, so these keep it.
CUT_MENU_KEEPS = {"ble", "ble_name"}


def _at_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _cut_menu_wanted(ctx: typer.Context) -> bool:
    """A bare `cut` at a terminal asks first; any other flag, or a pipe, cuts as told."""
    return _at_terminal() and all(
        ctx.get_parameter_source(p) is ParameterSource.DEFAULT for p in ctx.params if p not in CUT_MENU_KEEPS
    )


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help=(
        "Store the printer's duplex offset (see README §3): offset -x <x> -y <y> [-a <deg>]. "
        "All options are passed to the engine's offset_pdf.py with --save; the result is kept in data/offset_data.json."
    ),
)
def offset(ctx: typer.Context) -> None:
    from .paths import DUPLEX_OFFSET_FILE

    try:
        engine.offset_pdf(["--save", *ctx.args])
    except (engine.EngineMissing, RuntimeError) as e:
        fail(str(e))
    # offset_pdf.py writes the engine clone's (gitignored) data/offset_data.json; keep the
    # tracked copy in this repo's data/ as the source of truth so it follows the printer.
    src = SCM / "data" / "offset_data.json"
    if src.is_file():
        DUPLEX_OFFSET_FILE.parent.mkdir(exist_ok=True)
        shutil.copyfile(src, DUPLEX_OFFSET_FILE)
        typer.echo(f"offset saved to {DUPLEX_OFFSET_FILE} — commit it.")


@app.command(help="Persistent Scryfall image cache: show stats, or --clear it.")
def cache(clear: Annotated[bool, typer.Option("--clear", help="delete every cached image")] = False) -> None:
    c = ImageCache(cache_dir())
    if clear:
        typer.echo(f"removed {c.clear()} cached image(s) from {c.dir}")
        return
    s = c.stats()
    typer.echo(f"{c.dir}: {s.files} image(s), {s.bytes / 1e6:.1f} MB")


# --- backlog: miscuts and skipped pages, across decks ------------------------
def _load_run(run: Path | None) -> tuple[Path, RunInfo]:
    sidecar = find_sidecar(run)
    if sidecar is None:
        where = run or Path.cwd()
        fail(
            f"no run.json in {where} — run this inside a deck's output folder "
            "(the real one, not the flat Windows mirror), or point --run at it"
        )
    return sidecar.parent, RunInfo.read(sidecar)


def _sheets_of(run_dir: Path, info: RunInfo, overrides: dict) -> dict[str, manifest.Sheet]:
    if info.sheets:
        return manifest.sheets_from_dict(info.sheets)
    info.options = {**info.options, **overrides}
    typer.echo(
        f"{info.name}: run.json has no card manifest (older run) — re-deriving it from {info.decklist} "
        "via the image cache (no PDF is written)…"
    )
    try:
        sheets = manifest.rebuild(run_dir, info)
    except (ManifestError, build.BuildError, engine.EngineMissing, RuntimeError) as e:
        fail(str(e))
    info.sheets = {k: v.as_dict() for k, v in sheets.items()}
    info.write(run_dir)
    return sheets


@app.command(
    help=(
        "Queue cards of this run for reprinting (miscuts, bad laminations, the page you skipped). "
        "REF: a card name (unique match), p3 (whole page 3), p3.5 (page 3 slot 5), last (last page); "
        "d1 / d1.2 / dlast for the duplex sheet. Repeat a name for more copies. Appends to BACKLOG.txt "
        "next to the deck folder."
    )
)
def redo(
    refs: Annotated[list[str], typer.Argument(help="card names, pN, pN.S, last, dN, dN.S, dlast")],
    run: Annotated[
        Path | None, typer.Option("--run", help="run.json (or its folder) — default: current directory")
    ] = None,
    dry_run: Annotated[bool, typer.Option("-n", "--dry-run", help="show what would be queued")] = False,
    tokens: Annotated[
        int | None,
        typer.Option("-t", "--tokens", help="older run without manifest: the -t N it was built with", min=1),
    ] = None,
    basics: Annotated[
        bool, typer.Option("--basics", help="older run without manifest: it was built with --basics")
    ] = False,
) -> None:
    run_dir, info = _load_run(run)
    overrides: dict = {}
    if tokens is not None:
        overrides["token_copies"] = tokens
    if basics:
        overrides["include_basics"] = True
    sheets = _sheets_of(run_dir, info, overrides)
    entries: list[Entry] = []
    for ref in refs:
        try:
            cards = manifest.resolve(ref, sheets)
        except ManifestError as e:
            fail(str(e))
        for c in cards:
            entries.append(
                Entry.from_slot(c, info.name, manifest.locate(c, sheets), build._trim_for(c, info.trims))
            )
            typer.echo(
                f"  + {c.line:<50} {entries[-1].ref}"
                + ("  [dfc]" if c.dfc else "")
                + ("  [local]" if c.image else "")
            )
    if not entries:
        fail("nothing matched")
    bl = Backlog.at(run_dir.parent)
    if dry_run:
        typer.echo(f"dry run: {len(entries)} card(s) would be added to {bl.path}")
        return
    bl.add(entries)
    bl.save()
    typer.echo(f"queued {len(entries)} card(s) → {bl.path}")
    typer.echo(bl.summary(_per_page(info.paper, info.card_size, info.layout)))


def _per_page(paper: str, card_size: str, sheet_layout: str) -> int:
    try:
        check_layout(sheet_layout, paper, card_size)
    except LayoutError as e:
        fail(str(e))
    try:
        return manifest.cards_per_page(paper, card_size, sheet_layout)
    except (KeyError, LayoutError, engine.EngineMissing, OSError):
        return NOMINAL_CARDS.get(sheet_layout, 8)


backlog_app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=False,
    help="Cards still owed across decks (BACKLOG.txt in the current directory): show, build, drop.",
)
app.add_typer(backlog_app, name="backlog")

OutOpt = Annotated[
    Path,
    typer.Option("-o", "--out", help="directory holding BACKLOG.txt and the deck folders", file_okay=False),
]


def _resolve_faces(bl: Backlog) -> None:
    """Hand-added lines: find out on Scryfall whether they are double-sided, once."""
    if any(not e.faces_known for e in bl.entries):
        n = bl.resolve_faces(lambda e: is_double_sided(e.name, e.set, e.cn))
        if n:
            bl.save()
        if unknown := [e.card for e in bl.entries if not e.faces_known]:
            typer.echo(
                f"warning: could not look up on Scryfall, counted as single-sided: {', '.join(unknown)}",
                err=True,
            )


def _show_backlog(out: Path, paper: str, sheet_layout: str | None, card_size: str = "standard") -> Backlog:
    sheet_layout = sheet_layout or default_layout(paper, card_size)
    bl = Backlog.at(out.resolve())
    if not bl.entries:
        typer.echo(f"backlog empty ({bl.path})")
        raise typer.Exit()
    _resolve_faces(bl)
    typer.echo(bl.summary(_per_page(paper, card_size, sheet_layout)))
    typer.echo(bl.listing())
    return bl


SheetLayoutOpt = Annotated[
    str | None, typer.Option("--layout", help="3x3 | 4x2: count sheets of this layout (default: 3x3 on A4)")
]


@backlog_app.callback()
def backlog_main(
    ctx: typer.Context,
    out: OutOpt = Path("."),
    paper: Annotated[str, typer.Option("-p", "--paper")] = "a4",
    sheet_layout: SheetLayoutOpt = None,
) -> None:
    if ctx.invoked_subcommand is None:
        _show_backlog(out, paper, sheet_layout)


@backlog_app.command(name="show", help="List the queued cards and how many full sheets they make.")
def backlog_show(
    out: OutOpt = Path("."),
    paper: Annotated[str, typer.Option("-p", "--paper")] = "a4",
    sheet_layout: SheetLayoutOpt = None,
) -> None:
    _show_backlog(out, paper, sheet_layout)


@backlog_app.command(name="drop", help="Remove entries: by listed number or by (unique) card name.")
def backlog_drop(refs: Annotated[list[str], typer.Argument()], out: OutOpt = Path(".")) -> None:
    bl = Backlog.at(out.resolve())
    for ref in refs:
        try:
            for e in bl.drop(ref):
                typer.echo(f"  - {e.format()}")
        except BacklogError as e:
            fail(str(e))
    bl.save()
    typer.echo(f"{len(bl.cards)} card(s) left")


@backlog_app.command(
    name="build",
    help=(
        "Turn the backlog into a run folder ./backlog-<date>/ (PDF, duplex PDF, cut file, run.json) like any "
        "deck, then clear the built cards from BACKLOG.txt. --full-only keeps a partial last page queued."
    ),
)
def backlog_build(
    out: OutOpt = Path("."),
    paper: Annotated[str, typer.Option("-p", "--paper", help="a4 | letter | a3 ...")] = "a4",
    sheet_layout: Annotated[str | None, typer.Option("--layout", help=LAYOUT_HELP)] = None,
    backs: Annotated[
        bool, typer.Option("--backs/--fronts-only", help="double-sided (default: fronts only)")
    ] = False,
    full_only: Annotated[
        bool, typer.Option("--full-only", help="build full sheets only; the rest stays queued")
    ] = False,
    split_faces: Annotated[
        bool,
        typer.Option(
            "--split-faces/--duplex", help="each DFC face as its own card (default: separate duplex PDF)"
        ),
    ] = False,
    front_only: Annotated[
        list[str] | None,
        typer.Option(
            "--front-only", help="print this double-faced card's front as a single-sided card. Repeatable."
        ),
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="run folder name (default backlog-<date>)")
    ] = None,
    print_: Annotated[bool, typer.Option("--print", help="send the PDF straight to CUPS at 100%")] = False,
    printer: Annotated[str | None, typer.Option("--printer", help="CUPS destination for --print")] = None,
    dry_run: Annotated[bool, typer.Option("-n", "--dry-run", help="show what would be built")] = False,
) -> None:
    out = out.resolve()
    try:  # before anything is looked up or written; run_build checks the same
        opts = build.resolve_layout(
            build.BuildOptions(paper=paper, layout=sheet_layout, fronts_only=not backs)
        )
        sheet_layout = opts.layout
    except build.BuildError as e:
        fail(str(e))
    bl = Backlog.at(out)
    if not bl.entries:
        fail(f"backlog empty ({bl.path})")
    _resolve_faces(bl)
    for want in front_only or []:
        hits = [e for e in bl.entries if trim.clean_name(want) in trim.clean_name(e.name)]
        if not hits:
            fail(f"--front-only: nothing in the backlog matching {want!r}")
        for e in hits:
            e.front = True
    per_page = _per_page(paper, "standard", sheet_layout)
    cards, keep = bl.take(per_page, full_only)
    if not cards:
        fail(f"no full sheet yet ({bl.summary(per_page)}) — drop --full-only to print a partial one")
    dfc = [c for c in cards if c.dfc and not c.front]
    if sheet_layout in SINGLE_SIDED and dfc and not split_faces:
        fail(
            f"{len(dfc)} double-faced card(s) would need a duplex sheet, and --layout {sheet_layout} prints "
            f"fronts only for now: add --split-faces or --front-only NAME, or build them without --layout "
            f"{sheet_layout}"
        )
    run_name = name or _fresh_name(out, f"backlog-{date.today().isoformat()}")
    typer.echo(f"{len(cards)} card(s) → {out / run_name}/" + (f", {len(keep)} stay queued" if keep else ""))
    if dry_run:
        typer.echo(backlog_mod.decklist_text(cards), nl=False)
        return
    run_dir = out / run_name
    images: Path | None = None
    if own := [(c.name, Path(c.image)) for c in cards if c.image]:
        # Cards printed from local images: their images go into the run folder, so the run
        # (and a later rebuild of its manifest) reads them with --images like any deck.
        images = run_dir / "images"
        try:
            local.gather(own, images)
        except local.LocalImageError as e:
            fail(str(e))
    run_dir.mkdir(parents=True, exist_ok=True)
    deck = run_dir / f"{run_name}.txt"
    deck.write_text(backlog_mod.decklist_text(cards))
    trims = {c.name: c.trim for c in cards if c.trim is not None}
    opts = build.BuildOptions(
        deck=str(deck),
        out_parent=out,
        paper=paper,
        layout=sheet_layout,
        fronts_only=not backs,
        duplex_dfc=not split_faces,
        front_only=[c.name for c in cards if c.front],
        include_basics=True,  # every line here is explicit
        print_mode=print_,
        printer=printer,
        trims=[trim.TrimSpec(n, mm) for n, mm in trims.items()],
        images=images,
    )
    try:
        result = build.run_build(opts)
    except (build.BuildError, DeckError, engine.EngineMissing, RuntimeError) as e:
        fail(str(e))
    bl.entries = keep
    bl.save()
    build.print_summary(result, opts)
    typer.echo(
        f"  Queue:    {len(keep)} card(s) left in {bl.path}"
        if keep
        else f"  Queue:    empty ({bl.path.name} removed)"
    )
    if result.mirror_failed:
        raise typer.Exit(1)


def _fresh_name(parent: Path, base: str) -> str:
    name, n = base, 2
    while (parent / name).exists():
        name, n = f"{base}-{n}", n + 1
    return name


@app.command(name="make-back", help="Regenerate the generic proxy card back at assets/back.png.")
def make_back(out: Annotated[Path, typer.Option("--out", dir_okay=False)] = DEFAULT_BACK) -> None:
    from .back import PPI, H, W, write_back

    typer.echo(f"wrote {write_back(out)} ({W}x{H}px @ {PPI} PPI)")


@app.command(help="Check that the vendored engine and data files are in place.")
def doctor() -> None:
    from .paths import CUT_OFFSET_FILE, DUPLEX_OFFSET_FILE

    checks = [
        ("engine (silhouette-card-maker)", (SCM / "create_pdf.py").is_file(), "run ./setup.sh"),
        (
            "engine local patches (plugins/mtg/fetch.py --token_copies)",
            "token_copies"
            in (
                (SCM / "plugins/mtg/fetch.py").read_text() if (SCM / "plugins/mtg/fetch.py").is_file() else ""
            ),
            "checkout branch local-patches of j0nas/silhouette-card-maker",
        ),
        (
            "machine cut offset (data/cut_offset.json)",
            CUT_OFFSET_FILE.is_file(),
            "see README (Cut offset)",
        ),
        (
            "duplex offset (data/offset_data.json)",
            DUPLEX_OFFSET_FILE.is_file(),
            "mtg-proxy offset -x .. -y .. (README §3)",
        ),
        ("card back (assets/back.png)", DEFAULT_BACK.is_file(), "mtg-proxy make-back"),
        ("CUPS lp", printing.lp_available(), "needed only for --print"),
    ]
    bad = 0
    for label, ok, hint in checks:
        typer.echo(f"  [{'ok' if ok else '--'}] {label}" + ("" if ok else f"  → {hint}"))
        bad += not ok
    typer.echo(f"python {sys.version.split()[0]} · cache {cache_dir()}")
    raise typer.Exit(1 if bad else 0)


if __name__ == "__main__":
    app()
