"""mtg-proxy command line. A bare ``mtg-proxy`` at a terminal starts the wizard (wizard.py). ``make-proxies.sh`` / ``cut-proxies.sh`` / ``save-offset.sh`` are thin shims onto it."""

from __future__ import annotations

import math
import select
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from click.core import ParameterSource
from click.exceptions import NoArgsIsHelpError

from . import __version__, build, cutmenu, cutting, engine, layout, local, manifest, notes, printing, trim
from . import backlog as backlog_mod
from .backlog import Backlog, BacklogError, Entry
from .cache import ImageCache
from .decks import DeckError, is_double_sided
from .layout import NOMINAL_CARDS, LayoutError
from .manifest import ManifestError
from .paths import DEFAULT_BACK, NOTES_NAME, SCM, SIDECAR_NAME, cache_dir, out_root
from .sidecar import RunInfo, find_run, find_sidecar, runs_parent

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Decklist in, print-ready PDF out, cut on the Cameo (ET-8550 + Cameo 5 Alpha). "
        "Bare at a terminal, it walks through make, print and cut."
    ),
)


def parse_start(value: str) -> tuple[float, float]:
    try:
        top, left = (float(v) for v in value.split(","))
    except ValueError:
        fail(f"--scan-start wants TOP,LEFT in mm, got {value!r}")
    return top, left


PAPER_AT_REACH = 20.0  # mm either way from the grid's corner a --paper-at may put the paper


def parse_paper_at(value: str) -> tuple[float, float]:
    """--paper-at RIGHT,DOWN (x first, unlike --scan-start), or one number for both; negative is
    left of or above the grid's corner, where the 2026-10-28 proxy sheets lay (cutting.LAMINATED_AT)."""
    try:
        parts = [float(v) for v in value.split(",")]
    except ValueError:
        parts = []
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2 or not all(math.isfinite(v) and abs(v) <= PAPER_AT_REACH for v in parts):
        fail(
            f"--paper-at wants RIGHT,DOWN in mm, each within {PAPER_AT_REACH:g} of 0 (one number for both), "
            f"got {value!r}"
        )
    right, down = parts
    return right, down


def default_paper_at(preset: str) -> tuple[float, float]:
    """Where the paper is expected without --paper-at: a laminated sheet where the cut logs found
    them (cutting.LAMINATED_AT), plain paper on the grid's corner."""
    return cutting.LAMINATED_AT if preset == "laminate" else (0.0, 0.0)


def parse_target(value: str) -> tuple[float, float]:
    """--proof-target X,Y: a target's centre, page mm."""
    try:
        x, y = (float(v) for v in value.split(","))
    except ValueError:
        x = y = math.nan
    if not (math.isfinite(x) and math.isfinite(y) and x > 0 and y > 0):
        fail(f"--proof-target wants X,Y in mm from the page's top-left corner, got {value!r}")
    return x, y


def parse_blade(value: str) -> tuple[int, int, int]:
    """--proof-blade FORCE,SPEED,DEPTH, each in the Cameo's range."""
    try:
        force, speed, depth = (int(v) for v in value.split(","))
    except ValueError:
        force = speed = depth = -1
    if not (1 <= force <= 40 and 1 <= speed <= 30 and 0 <= depth <= 10):
        fail(f"--proof-blade wants FORCE,SPEED,DEPTH (1-40, 1-30, 0-10), got {value!r}")
    return force, speed, depth


def fail(msg: str, code: int = 1) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mtg-proxy {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        if not _at_terminal():
            raise NoArgsIsHelpError(ctx)  # as no_args_is_help did: the help, exit 2
        from . import wizard

        wizard.run()


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
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="parent dir for <deckname>/ (default ~/Desktop/proxies, $MTG_PROXY_OUT)",
            file_okay=False,
        ),
    ] = None,
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
    alt_names: Annotated[
        bool,
        typer.Option(
            "--alt-names",
            help="also pick printings that show another name on the card (Universes Beyond names like "
            "'Rathalos, King of the Skies', Japanese-only printings); a set and number in the deck line always wins",
        ),
    ] = False,
    skip_fetch: Annotated[
        bool, typer.Option("--skip-fetch", help="reuse already-downloaded card images")
    ] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="bypass the persistent image cache")] = False,
    test: Annotated[
        bool, typer.Option("--test", help="ink-light placeholder gauge sheet, no Scryfall")
    ] = False,
    print_: Annotated[
        bool, typer.Option("--print", help="print the PDF on the PC (glossy queue; plain for --test)")
    ] = False,
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
    allow_stale: Annotated[
        bool,
        typer.Option(
            "--allow-stale", help="run even when this checkout or its engine is behind GitHub (freshness.py)"
        ),
    ] = False,
) -> None:
    if show_notes:
        _notes(deck, out or runs_parent())
        return
    _fresh(allow_stale)
    if test and deck is not None:
        fail(f"--test takes no decklist (got {deck!r})")
    if tokens is not None and tokens_only is not None:
        fail("--tokens and --tokens-only are mutually exclusive")
    try:
        trim_specs = [trim.parse_spec(t) for t in trims or []]
    except trim.TrimError as e:
        fail(str(e))
    opts = build.BuildOptions(
        deck=deck,
        fmt=fmt,
        out_parent=out or out_root(),
        board=board,
        fronts_only=not backs,
        duplex_dfc=not split_faces,
        fancy_art=not plain,
        alt_names=alt_names,
        include_basics=basics,
        token_copies=tokens_only or tokens or 0,
        tokens_only=tokens_only is not None,
        skip_fetch=skip_fetch,
        test_mode=test,
        print_mode=print_,
        fetch_args=list(ctx.args),
        use_cache=not no_cache,
        dry_run=dry_run,
        back=back,
        trims=trim_specs,
        defer_partial=defer_partial,
        images=images.resolve() if images else None,  # absolute: run.json keeps it for rebuilds
    )
    build_and_report(opts)


def build_and_report(opts: build.BuildOptions) -> build.BuildResult | None:
    """What `make` does with its options (and the wizard): build, then the summary. None for a dry run."""
    try:
        result = build.run_build(opts)
    except (build.BuildError, DeckError, engine.EngineMissing, RuntimeError) as e:
        fail(str(e))
    if opts.dry_run:
        return None
    build.print_summary(result, opts)
    return result


def _fresh(allow_stale: bool) -> None:
    """Stop before a build or a cut on a checkout that is behind GitHub (``freshness``)."""
    from . import freshness

    try:
        freshness.ensure_fresh(lambda m: typer.echo(f"mtg-proxy: {m}", err=True), allow_stale)
    except freshness.StaleError as e:
        fail(str(e))


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


@app.command(
    name="notes", help="Show the CUT-NOTES of a previous run (the newest in ~/Desktop/proxies by default)."
)
def notes_cmd(
    name: Annotated[str | None, typer.Argument(help="run folder name")] = None,
    out: Annotated[
        Path | None, typer.Option("-o", "--out", help="parent dir to search", file_okay=False)
    ] = None,
) -> None:
    _notes(name, out or runs_parent())


@app.command(
    help=(
        "Cut a printed sheet on the Cameo 5 Alpha. Reads run.json from the current directory (or --run) "
        "for the printed marks; flags override. With no flags at a terminal, asks first "
        "(Enter cuts this folder's run, else the newest in ~/Desktop/proxies)."
    ),
)
def cut(
    ctx: typer.Context,
    run: Annotated[
        Path | None, typer.Option("--run", help="run.json, its folder, or a run name in ~/Desktop/proxies")
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
    pass_by_pass: Annotated[
        bool,
        typer.Option(
            "--pass-by-pass",
            help="cut every path once, then every path again, --passes times, saying when each whole "
            "pass is done (pause the Cameo there to check the sheet); default: each loop's passes in a row",
        ),
    ] = False,
    score_force: Annotated[
        int,
        typer.Option(
            "--score-force",
            min=1,
            max=40,
            help="force for the SVG's scored lines (stroke #00ff00), cut first in one pass; an untested guess",
        ),
    ] = cutting.SCORE_FORCE,
    score_speed: Annotated[
        int | None,
        typer.Option("--score-speed", min=1, max=30, help="speed for the scored lines (default: the cut's)"),
    ] = None,
    score_depth: Annotated[
        int, typer.Option("--score-depth", min=0, max=10, help="AutoBlade depth for the scored lines")
    ] = cutting.SCORE_DEPTH,
    no_scores: Annotated[
        bool,
        typer.Option(
            "--no-scores", help="leave the SVG's scored lines uncut: another pass on a sheet already scored"
        ),
    ] = False,
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
            help="mark corners' distance from the paper edges in mm, all round (default: the 3x3's, "
            "3.5 from the sides, 9 from the top and bottom; an --svg sheet must give it)",
        ),
    ] = None,
    reg_length: Annotated[
        float | None,
        typer.Option(
            "--reg-length", help="printed mark leg length in mm (default: the 3x3's, 6; --svg: 9.4)"
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
    diagnose: Annotated[
        bool,
        typer.Option(
            "--diagnose",
            help="before any scan, read across every mark and say where it sits and how it reads (about 4 "
            "minutes); cut nothing. For a sheet that won't register",
        ),
    ] = False,
    no_check: Annotated[
        bool,
        typer.Option(
            "--no-check",
            help="skip reading the marks with the sensor before and after the scan (about 80-90 s a sheet); "
            "the scan then starts where the sheet should be",
        ),
    ] = False,
    register: Annotated[
        str | None,
        typer.Option(
            "--register",
            help="how the marks are registered: 'sensor' (this driver finds each mark with the Cameo's "
            "debug sensor and cuts through its own fit, no scan; needs firmware V1.04; the default) or "
            "'scan' (the Cameo's own scan, TB123, the fallback; the default with --no-check)",
        ),
    ] = None,
    sense: Annotated[
        bool,
        typer.Option(
            "--sense",
            help="scan the marks, then read them back with the sensor across each mark's edges; cut nothing",
        ),
    ] = False,
    proof: Annotated[
        bool,
        typer.Option(
            "--proof",
            help="after the scan, trace one of the sheet's proof targets (small black squares printed with "
            "the marks) and wait: y cuts, r rescans and traces the next, q stops",
        ),
    ] = False,
    proof_target: Annotated[
        list[str] | None,
        typer.Option(
            "--proof-target",
            metavar="X,Y",
            help="a proof target's centre, mm from the page's top-left corner (y down); repeat for each, "
            "in the order the rounds take them (default: the layout's; an --svg sheet has only these)",
        ),
    ] = None,
    proof_target_size: Annotated[
        float | None,
        typer.Option("--proof-target-size", min=1, max=10, help="the proof targets' side in mm (default 3)"),
    ] = None,
    proof_from: Annotated[
        int,
        typer.Option(
            "--proof-from",
            min=0,
            help="the sheet's first N proof targets are cut already, by an earlier run: start at the next",
        ),
    ] = 0,
    proof_blade: Annotated[
        str | None,
        typer.Option(
            "--proof-blade",
            metavar="FORCE,SPEED,DEPTH",
            help="the proof cut's blade, one pass (default 10,5,2: scores laminate, nowhere near through)",
        ),
    ] = None,
    scan_start: Annotated[
        list[str] | None,
        typer.Option(
            "--scan-start",
            metavar="TOP,LEFT",
            help="where the mark search starts, mm from the loaded origin, used as given (--paper-at "
            "moves only the default); repeat to retry in order (default: 2 mm or more into the square, 6 mm "
            "left of it or 1 mm from the paper's edge: 11,1 on a 3x3 sheet)",
        ),
    ] = None,
    paper_at: Annotated[
        str | None,
        typer.Option(
            "--paper-at",
            metavar="RIGHT,DOWN",
            help="where the paper's top-left corner is expected: RIGHT mm right of the mat grid's corner, "
            "then DOWN mm below it (x first, the other way round from --scan-start's TOP,LEFT; negative "
            "is left or up); one number sets both. The sensor finds the sheet within a few mm of it "
            "either way and reads it there. Default: where laminated proxy sheets lie (5,-1.75), "
            "0,0 with --preset paper",
        ),
    ] = None,
    allow_stale: Annotated[
        bool,
        typer.Option(
            "--allow-stale", help="run even when this checkout or its engine is behind GitHub (freshness.py)"
        ),
    ] = False,
) -> None:
    # The options as Typer converted them, for cut_flow: ctx.params holds Click's raw values, a str
    # where the option is a Path (--svg, --run).
    given = dict(locals())
    if scan:
        raise typer.Exit(cutting.ble_scan())
    _fresh(allow_stale)
    if preset not in cutting.PRESETS:
        fail(f"--preset must be one of {', '.join(cutting.PRESETS)}, got {preset!r}")
    plain = False
    if _cut_menu_wanted(ctx):
        here = Path.cwd()
        picked = cutmenu.ask(
            here if (here / SIDECAR_NAME).is_file() else runs_parent(), "Bluetooth" if ble else "USB"
        )
        if picked is None:
            raise typer.Exit()
        run, plain, preset = picked.run, picked.run is None, picked.preset
        dry_run, proof, probe = (picked.mode == m for m in ("dry-run", "proof", "probe"))
    params = {k: v for k, v in given.items() if k not in ("ctx", "scan", "allow_stale")}
    cut_flow(
        **{
            **params,
            "run": run,
            "plain": plain,
            "preset": preset,
            "dry_run": dry_run,
            "proof": proof,
            "probe": probe,
        }
    )


def cut_flow(
    run: Path | None = None,
    plain: bool = False,
    preset: str = "laminate",
    force: int | None = None,
    speed: int | None = None,
    depth: int | None = None,
    passes: int | None = None,
    pass_by_pass: bool = False,
    score_force: int = cutting.SCORE_FORCE,
    score_speed: int | None = None,
    score_depth: int = cutting.SCORE_DEPTH,
    no_scores: bool = False,
    y_off: float | None = None,
    x_off: float | None = None,
    ble: bool = True,
    ble_name: str = "CAMEO 5 ALPHA",
    svg: Path | None = None,
    dry_run: bool = False,
    reg_inset: float | None = None,
    reg_length: float | None = None,
    reg_thickness: float = 1.0,
    probe: bool = False,
    diagnose: bool = False,
    no_check: bool = False,
    register: str | None = None,
    sense: bool = False,
    proof: bool = False,
    proof_target: list[str] | None = None,
    proof_target_size: float | None = None,
    proof_from: int = 0,
    proof_blade: str | None = None,
    scan_start: list[str] | None = None,
    paper_at: str | None = None,
) -> None:
    """Cut a sheet and then watch the mat for the next, as `cut` does once its menu is answered. The
    defaults are `cut`'s (tests/test_cutting.py checks they agree). Ends in typer.Exit."""
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
        typer.echo(f"cut-proxies: using {sidecar} ({info.name})")
        if proof and not info.proof_targets and not proof_target:
            fail(
                f"{info.name} was printed before its sheets carried proof targets ({sidecar}): rebuild and "
                "reprint it for --proof, or cut it without; the sensor check still reads every mark"
            )
    elif run is not None:
        fail(f"no run.json at {run}")
    elif not plain:
        typer.echo("cut-proxies: no run.json in the current directory — using flags/defaults", err=True)
    o = cutting.CutOptions(
        force=blade.force if force is None else force,
        speed=blade.speed if speed is None else speed,
        depth=blade.depth if depth is None else depth,
        passes=blade.passes if passes is None else passes,
        pass_by_pass=pass_by_pass,
        scores=not no_scores, score_force=score_force, score_speed=score_speed, score_depth=score_depth,
        x_off=x_off, y_off=y_off,
        connection="ble" if ble else "usb", ble_name=ble_name,
        svg=svg, dry_run=dry_run,
        reg_length=reg_length, reg_thickness=reg_thickness, reg_inset=reg_inset,
        label=info.name if info else None,
        probe=probe, sense=sense, diagnose=diagnose, check=not no_check, register=register or ("scan" if no_check else "sensor"), proof=proof, scan_starts=[parse_start(v) for v in scan_start or []] or None,
        proof_targets=[parse_target(v) for v in proof_target] if proof_target else None,
        proof_target_size=proof_target_size, proof_from=proof_from,
        proof_blade=parse_blade(proof_blade) if proof_blade else None,
        paper_at=parse_paper_at(paper_at) if paper_at is not None else default_paper_at(preset),
        printed_marks=info.marks if info else None,
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
        if rc or o.dry_run or o.probe or o.sense or o.diagnose:
            break
        if not _at_terminal():
            break
        then = _await_next_sheet(o, sheet)
        if then == "menu":
            then = cutmenu.after_cut(sheet, proof=job.proof)
        if then == "next":  # a batch is proofed once: its first sheet's marks passed
            o, sheet = replace(job, proof=False), sheet + 1
        elif then == "next-proof":  # a new sheet: its proof targets are all uncut
            o, sheet = replace(job, proof_from=0), sheet + 1
        elif then == "pass":
            # Same sheet, marks already proven, folds already scored.
            o = replace(job, passes=1, proof=False, scores=False)
        else:
            break
    raise typer.Exit(rc)


COUNTDOWN_S = 5  # between the mat coming back in and the next sheet's scan


def _await_next_sheet(o: cutting.CutOptions, sheet: int) -> str:
    """Watch the mat after a sheet: "next" once it went out and came back and the countdown ran
    out, "menu" when the operator pressed Enter or the mat can't be watched."""
    typer.echo(
        f"cut-proxies: sheet {sheet} done. Unload, load the next sheet and it starts by itself. "
        "Enter: menu (one more pass, done). Ctrl-C: stop."
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


@app.command(
    help=(
        "Walk the Cameo's TB123 mark scan over a sheet's PDF and report, per pass, the dark runs it "
        "crosses and the nearest print that is not a mark; exits 1 when print sits where a pass "
        "walks. Needs no machine. Reads run.json from --run, the current directory, or the newest run for the "
        "sheet's PDF; flags override (README, Sheet layout)."
    ),
)
def simulate(
    pdf: Annotated[Path | None, typer.Argument(help="the sheet's PDF (default: run.json's)")] = None,
    run: Annotated[
        Path | None,
        typer.Option("--run", help="run.json, its folder, or a run name (default: the newest run)"),
    ] = None,
    reg_inset: Annotated[
        float | None,
        typer.Option("--reg-inset", min=3, max=30, help="as cut-proxies: marks MM from every paper edge"),
    ] = None,
    reg_length: Annotated[
        float | None, typer.Option("--reg-length", help="printed leg length in mm (default: the 3x3's)")
    ] = None,
    reg_thickness: Annotated[float, typer.Option("--reg-thickness")] = 1.0,
    scan_start: Annotated[
        str | None,
        typer.Option(
            "--scan-start",
            metavar="TOP,LEFT",
            help="where the mark search starts, as cut-proxies (default: 2 mm or more into the square)",
        ),
    ] = None,
    page: Annotated[int, typer.Option("--page", min=1, help="which page of the PDF")] = 1,
) -> None:
    from . import simulate as sim
    from .cameo import driver
    from .cameo.geometry import load_kinds

    sidecar = find_run(run)
    if run is not None and sidecar is None:
        fail(f"no run.json at {run}")
    _say_implicit_run(run, sidecar)
    info = RunInfo.read(sidecar) if sidecar else None
    if pdf is None:
        if info is None or not info.pdf:
            fail("no PDF given and no run.json to name one")
        pdf = sidecar.parent / info.pdf
    if not pdf.is_file():
        fail(f"no PDF at {pdf}")
    try:
        with (
            tempfile.TemporaryDirectory() as tmp
        ):  # the cut outlines, for the scan's run-ups as cut-proxies tells it
            svg = Path(tmp) / "cut.svg"
            geom = layout.build_cut_svg(svg)
            if reg_inset is not None:
                geom = cutting.with_inset(geom, reg_inset)
            frame = cutting.frame_of(geom, reg_length, reg_thickness)
            frame = replace(frame, run_up=driver.run_ups(frame, load_kinds(svg)[0]))
        report = sim.run(pdf, frame, page, parse_start(scan_start) if scan_start else None)
    except (LayoutError, engine.EngineMissing, sim.SimulationError, RuntimeError) as e:
        fail(str(e))
    top, left = report.start
    down, across = frame.run_up
    typer.echo(
        f"simulate: {pdf.name} page {page}, scan starts at ({top:g}, {left:g}) mm, "
        f"run-ups {down:g} mm down and {across:g} mm across"
    )
    for line in report.lines():
        typer.echo(line)
    if not report.ok:
        raise typer.Exit(1)


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
def _say_implicit_run(run: Path | None, sidecar: Path | None) -> None:
    """Without --run, outside a deck folder, the newest run was taken: say which."""
    if run is None and sidecar is not None and sidecar.parent != Path.cwd():
        typer.echo(
            f"run: {sidecar.parent.name} (the newest in {sidecar.parent.parent}; --run NAME for another)"
        )


def _load_run(run: Path | None) -> tuple[Path, RunInfo]:
    sidecar = find_run(run)
    if sidecar is None:
        where = run or runs_parent()
        fail(f"no run.json in {where} — point --run at a deck's output folder or name it")
    _say_implicit_run(run, sidecar)
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
        "next to the deck folder. Takes the deck folder you're in, else the newest run (--run NAME for another)."
    )
)
def redo(
    refs: Annotated[list[str], typer.Argument(help="card names, pN, pN.S, last, dN, dN.S, dlast")],
    run: Annotated[
        Path | None,
        typer.Option("--run", help="run.json, its folder, or a run name — default: the newest run"),
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
    queue_redo(refs, run, dry_run, tokens, basics)


def queue_redo(
    refs: list[str],
    run: Path | None = None,
    dry_run: bool = False,
    tokens: int | None = None,
    basics: bool = False,
) -> None:
    """What `redo` does (and the wizard): put the cards ``refs`` name onto the backlog."""
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
    typer.echo(bl.summary(_per_page()))


def _per_page() -> int:
    try:
        return manifest.cards_per_page()
    except (KeyError, LayoutError, engine.EngineMissing, OSError):
        return NOMINAL_CARDS


backlog_app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=False,
    help="Cards still owed across decks (BACKLOG.txt beside the deck folders, ~/Desktop/proxies): show, build, drop.",
)
app.add_typer(backlog_app, name="backlog")

OutOpt = Annotated[
    Path | None,
    typer.Option(
        "-o",
        "--out",
        help="directory holding BACKLOG.txt and the deck folders (default ~/Desktop/proxies)",
        file_okay=False,
    ),
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


def _show_backlog(out: Path) -> Backlog:
    bl = Backlog.at(out.resolve())
    if not bl.entries:
        typer.echo(f"backlog empty ({bl.path})")
        raise typer.Exit()
    _resolve_faces(bl)
    typer.echo(bl.summary(_per_page()))
    typer.echo(bl.listing())
    return bl


@backlog_app.callback()
def backlog_main(ctx: typer.Context, out: OutOpt = None) -> None:
    if ctx.invoked_subcommand is None:
        _show_backlog(out or runs_parent())


@backlog_app.command(name="show", help="List the queued cards and how many full sheets they make.")
def backlog_show(out: OutOpt = None) -> None:
    _show_backlog(out or runs_parent())


@backlog_app.command(name="drop", help="Remove entries: by listed number or by (unique) card name.")
def backlog_drop(refs: Annotated[list[str], typer.Argument()], out: OutOpt = None) -> None:
    bl = Backlog.at((out or runs_parent()).resolve())
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
        "Turn the backlog into a run folder backlog-<date>/ next to the decks (PDF, duplex PDF, cut file, run.json) like any "
        "deck, then clear the built cards from BACKLOG.txt. --full-only keeps a partial last page queued."
    ),
)
def backlog_build(
    out: OutOpt = None,
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
    print_: Annotated[
        bool, typer.Option("--print", help="print the PDF on the PC (glossy queue; plain for --test)")
    ] = False,
    dry_run: Annotated[bool, typer.Option("-n", "--dry-run", help="show what would be built")] = False,
) -> None:
    build_backlog(out or runs_parent(), backs, full_only, split_faces, front_only, name, print_, dry_run)


def build_backlog(
    out: Path,
    backs: bool = False,
    full_only: bool = False,
    split_faces: bool = False,
    front_only: list[str] | None = None,
    name: str | None = None,
    print_: bool = False,
    dry_run: bool = False,
) -> build.BuildResult | None:
    """What `backlog build` does (and the wizard): build the queued cards as a run folder. None for a dry run."""
    out = out.resolve()
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
    per_page = _per_page()
    cards, keep = bl.take(per_page, full_only)
    if not cards:
        fail(f"no full sheet yet ({bl.summary(per_page)}) — drop --full-only to print a partial one")
    run_name = name or _fresh_name(out, f"backlog-{date.today().isoformat()}")
    typer.echo(f"{len(cards)} card(s) → {out / run_name}/" + (f", {len(keep)} stay queued" if keep else ""))
    if dry_run:
        typer.echo(backlog_mod.decklist_text(cards), nl=False)
        return None
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
        fronts_only=not backs,
        duplex_dfc=not split_faces,
        front_only=[c.name for c in cards if c.front],
        include_basics=True,  # every line here is explicit
        print_mode=print_,
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
    return result


def _fresh_name(parent: Path, base: str) -> str:
    name, n = base, 2
    while (parent / name).exists():
        name, n = f"{base}-{n}", n + 1
    return name


@app.command(name="print", help="Print a PDF on the Windows PC's Epson queues (never from the Mac).")
def print_cmd(
    pdf: Annotated[Path, typer.Argument(help="the sheet PDF", dir_okay=False)],
    paper: Annotated[str, typer.Option("--paper", help="plain | glossy | premium")] = "glossy",
    dry_run: Annotated[
        bool, typer.Option("-n", "--dry-run", help="copy to the PC, show the print command, stop")
    ] = False,
) -> None:
    try:
        printing.print_pdf(pdf, paper, dry_run=dry_run, say=typer.echo)
    except printing.PrintError as e:
        fail(str(e))


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
        ("PC ssh key", printing.KEY.is_file(), "needed only for --print (~/.ssh/pomelomadness_ed25519)"),
    ]
    bad = 0
    for label, ok, hint in checks:
        typer.echo(f"  [{'ok' if ok else '--'}] {label}" + ("" if ok else f"  → {hint}"))
        bad += not ok
    typer.echo(f"python {sys.version.split()[0]} · cache {cache_dir()}")
    raise typer.Exit(1 if bad else 0)


if __name__ == "__main__":
    app()
