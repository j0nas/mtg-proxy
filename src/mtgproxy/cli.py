"""mtg-proxy command line. ``make-proxies.sh`` / ``cut-proxies.sh`` / ``save-offset.sh`` are thin shims onto it."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, build, cutting, engine, notes, printing, studio3, trim
from .cache import ImageCache
from .decks import DeckError
from .layout import LayoutError
from .paths import DEFAULT_BACK, DRV_PY, NOTES_NAME, SCM, cache_dir
from .sidecar import RunInfo, find_sidecar

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Decklist in, print-ready PDF + Silhouette cut file out (ET-8550 + Cameo 5 Alpha).",
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
    registration: Annotated[str, typer.Option("-r", "--registration", help="4 | 3 registration marks")] = "4",
    board: Annotated[str, typer.Option(help="deck URL board: main | side | considering")] = "main",
    backs: Annotated[
        bool, typer.Option("--backs/--fronts-only", help="double-sided cards (default: fronts only)")
    ] = False,
    basics: Annotated[
        bool, typer.Option("--basics", help="include basic lands (skipped by default)")
    ] = False,
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
        registration=registration,
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
    )
    try:
        result = build.run_build(opts)
    except (build.BuildError, DeckError, engine.EngineMissing, studio3.Studio3Error, RuntimeError) as e:
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
    context_settings={"allow_extra_args": True},
    help=(
        "Cut a printed sheet on the Cameo 5 Alpha directly (no Studio). Reads run.json from the current "
        "directory (or --run) for paper/card/registration; flags override. Anything after -- goes to sendto_silhouette.py."
    ),
)
def cut(
    ctx: typer.Context,
    run: Annotated[
        Path | None, typer.Option("--run", help="run.json (or its folder) of the sheet to cut")
    ] = None,
    paper: Annotated[str | None, typer.Option("-p", "--paper")] = None,
    card: Annotated[str | None, typer.Option("-c", "--card")] = None,
    registration: Annotated[
        str | None, typer.Option("-r", "--registration", help="4 | 3 — MUST match the printed marks")
    ] = None,
    force: Annotated[int, typer.Option(min=1, max=40)] = 25,
    speed: Annotated[int, typer.Option(min=1, max=30)] = 25,
    depth: Annotated[int, typer.Option(min=0, max=10, help="AutoBlade depth")] = 5,
    passes: Annotated[int, typer.Option(min=1, max=8)] = 3,
    y_off: Annotated[
        float | None, typer.Option("--y-off", help="shift cuts down by MM (default: data/cut_offset.json)")
    ] = None,
    x_off: Annotated[
        float | None, typer.Option("--x-off", help="shift cuts right by MM (default: data/cut_offset.json)")
    ] = None,
    ble: Annotated[bool, typer.Option("--ble", help="connect over Bluetooth LE (default USB)")] = False,
    ble_name: Annotated[str, typer.Option("--ble-name", help="advertised BLE name")] = "CAMEO 5 ALPHA",
    scan: Annotated[bool, typer.Option("--scan", help="list nearby BLE devices, then stop")] = False,
    svg: Annotated[
        Path | None, typer.Option("--svg", help="cut this SVG instead of generating one (page-sized, mm)")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("-n", "--dry-run", help="no machine needed; writes output/cut/<name>.cmds")
    ] = False,
    preview: Annotated[
        bool, typer.Option("--preview", help="matplotlib preview window before sending")
    ] = False,
) -> None:
    if scan:
        raise typer.Exit(cutting.ble_scan())
    info: RunInfo | None = None
    sidecar = find_sidecar(run)
    if sidecar is not None:
        info = RunInfo.read(sidecar)
        typer.echo(
            f"cut-proxies: using {sidecar} ({info.name}: {info.paper}/{info.card_size}, {info.registration}-mark)"
        )
    elif run is not None:
        fail(f"no run.json at {run}")
    else:
        typer.echo("cut-proxies: no run.json in the current directory — using flags/defaults", err=True)
    o = cutting.CutOptions(
        paper=paper or (info.paper if info else "a4"),
        card_size=card or (info.card_size if info else "standard"),
        registration=registration or (info.registration if info else "4"),
        force=force, speed=speed, depth=depth, passes=passes,
        x_off=x_off, y_off=y_off,
        connection="ble" if ble else "usb", ble_name=ble_name,
        svg=svg, dry_run=dry_run, preview=preview,
        extra=list(ctx.args),
        label=info.name if info else None,
    )  # fmt: skip
    try:
        rc = cutting.run_cut(o)
    except (cutting.CutError, engine.EngineMissing, LayoutError, RuntimeError) as e:
        fail(str(e))
    raise typer.Exit(rc)


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


@app.command(
    name="rebase-template", help="Rebase a Studio-saved template into an offset-free base (README §2¾)."
)
def rebase_template(
    saved: Annotated[
        Path,
        typer.Argument(
            help="template saved from Silhouette Studio (settings changed, shapes untouched)",
            exists=True,
            dir_okay=False,
        ),
    ],
    stock: Annotated[
        Path,
        typer.Argument(
            help="pristine stock template (silhouette-card-maker/cutting_templates/...)",
            exists=True,
            dir_okay=False,
        ),
    ],
    out: Annotated[Path, typer.Argument(help="output base template (templates/...)", dir_okay=False)],
) -> None:
    try:
        base, dx, dy = studio3.rebase(saved.read_bytes(), stock.read_bytes())
    except studio3.Studio3Error as e:
        fail(str(e))
    typer.echo(f"baked offset measured: x={dx:g}mm y={dy:g}mm", err=True)
    typer.echo("verified: base + offset reproduces the Studio save byte-for-byte", err=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base)
    typer.echo(str(out))


@app.command(name="make-back", help="Regenerate the generic proxy card back at assets/back.png.")
def make_back(out: Annotated[Path, typer.Option("--out", dir_okay=False)] = DEFAULT_BACK) -> None:
    from .back import PPI, H, W, write_back

    typer.echo(f"wrote {write_back(out)} ({W}x{H}px @ {PPI} PPI)")


@app.command(help="Check that the vendored engine, cutter driver and data files are in place.")
def doctor() -> None:
    from .paths import CUT_OFFSET_FILE, DUPLEX_OFFSET_FILE, TEMPLATES

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
            "cutter driver venv (inkscape-silhouette/.venv)",
            DRV_PY.is_file(),
            "run ./setup.sh (only needed for cut-proxies)",
        ),
        (
            "machine cut offset (data/cut_offset.json)",
            CUT_OFFSET_FILE.is_file(),
            "commit one; see README §2¾",
        ),
        (
            "duplex offset (data/offset_data.json)",
            DUPLEX_OFFSET_FILE.is_file(),
            "mtg-proxy offset -x .. -y .. (README §3)",
        ),
        ("A4 template base (templates/)", any(TEMPLATES.glob("a4-standard-*.studio3")), "see README §2¾"),
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
