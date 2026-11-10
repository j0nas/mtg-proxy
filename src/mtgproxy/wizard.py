"""The wizard a bare ``mtg-proxy`` starts at a terminal: make, print and cut a deck from one menu.

Each step calls what the subcommand of the same name calls (cli.build_and_report, printing.print_pdf,
cli.cut_flow, ...), so the wizard only asks for arguments. A run made or printed is carried into
the next step: after a build it asks about printing, after a print about cutting. Ctrl-C at any
question leaves the wizard.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import questionary
import typer
from questionary import Choice

from . import build, cli, cutmenu, printing
from .paths import SIDECAR_NAME
from .sidecar import RunInfo, runs_parent


class Cancelled(Exception):
    """The operator cancelled a question (Ctrl-C): leave the wizard."""


def _ask(question):
    answer = question.ask()  # None on Ctrl-C
    if answer is None:
        raise Cancelled
    return answer


def _yes(message: str) -> bool:
    return _ask(questionary.confirm(message, default=True))


def run() -> None:
    cli._fresh(False)
    actions = {
        "make": ("Make sheets from a deck", make),
        "print": ("Print a sheet", print_sheet),
        "cut": ("Cut a sheet", cut_sheet),
        "redo": ("Requeue miscut cards", redo),
        "backlog": ("Make the backlog sheet", backlog_sheet),
        "notes": ("Show cut notes", notes),
    }
    menu = [Choice(title, key) for key, (title, _) in actions.items()] + [Choice("Quit", "quit")]
    try:
        while (pick := _ask(questionary.select("mtg-proxy:", choices=menu))) != "quit":
            with contextlib.suppress(typer.Exit):  # a step failed and said why; the menu is still useful
                actions[pick][1]()
    except (Cancelled, KeyboardInterrupt):
        typer.echo()


def _pick_run(message: str) -> Path | None:
    """A run.json, newest first; None (after saying so) when there is no run yet."""
    where = runs_parent()
    runs = cutmenu.runs_below(where)
    if not runs:
        typer.echo(f"no runs in {where} yet: make one first")
        return None
    return Path(
        _ask(questionary.select(message, choices=[Choice(cutmenu.describe(p), str(p)) for p in runs]))
    )


# --- make ---------------------------------------------------------------------
def make() -> None:
    picked = _ask(
        questionary.checkbox(
            "Options (space ticks, Enter goes on):",
            choices=[
                Choice("include basic lands", "basics"),
                Choice("plain frames (default prefers showcase, extended and full art)", "plain"),
                Choice("print double-sided cards' backs (separate duplex PDF)", "backs"),
                Choice("test gauge sheet instead of a deck (no Scryfall)", "test"),
            ],
        )
    )
    deck = None
    if "test" not in picked:
        deck = _ask(
            questionary.text(
                "Deck URL (Moxfield, Archidekt) or decklist path:", validate=lambda t: bool(t.strip())
            )
        )
        deck = str(Path(deck.strip().strip("'\"")).expanduser()) if "://" not in deck else deck.strip()
    result = cli.build_and_report(
        build.BuildOptions(
            deck=deck,
            fronts_only="backs" not in picked,
            fancy_art="plain" not in picked,
            include_basics="basics" in picked,
            test_mode="test" in picked,
        )
    )
    if result is not None:
        _after_build(result)


def _after_build(result: build.BuildResult) -> None:
    if not (result.pdf or result.duplex_pdf):  # everything went to the backlog
        return
    sidecar = result.out / SIDECAR_NAME
    if _yes("Print it now?"):
        print_sheet(sidecar)
    else:
        _offer_cut(sidecar)


# --- print --------------------------------------------------------------------
def print_sheet(run: Path | None = None) -> None:
    """Print one of a run's PDFs on the PC, once, then offer to cut the run."""
    sidecar = run or _pick_run("Print which run?")
    if sidecar is None:
        return
    info = RunInfo.read(sidecar)
    sheets = [
        (title, sidecar.parent / name)
        for title, name in (("sheet", info.pdf), ("duplex sheet (double-sided cards)", info.duplex_pdf))
        if name and (sidecar.parent / name).is_file()
    ]
    if not sheets:
        typer.echo(f"{info.name} has no PDF to print")
        return
    pdf = sheets[0][1]
    if len(sheets) > 1:
        pdf = Path(_ask(questionary.select("Which PDF?", choices=[Choice(t, str(p)) for t, p in sheets])))
    paper = build.paper_for(info.name)
    queue = printing.QUEUES[paper]
    if pdf.name == info.duplex_pdf:
        typer.echo("This is the duplex sheet: print it with manual duplex, long-edge flip.")
    if _yes(f"Print {pdf.name} on the {paper} queue ('{queue}')?"):
        try:  # once, never retried (printing.print_pdf says why)
            printing.print_pdf(pdf, paper, say=typer.echo)
        except printing.PrintError as e:
            typer.echo(f"error: {e}", err=True)
            return
    _offer_cut(sidecar)


def _offer_cut(sidecar: Path) -> None:
    if _yes("Cut it now?"):
        cut_sheet(sidecar)


# --- cut ----------------------------------------------------------------------
def cut_sheet(run: Path | None = None) -> None:
    """The cut menu (cutmenu.ask) with ``run`` as its default sheet, then cli.cut_flow's cut loop."""
    picked = cutmenu.ask(runs_parent(), "Bluetooth", run)
    if picked is None:
        return
    dry_run, proof, probe = (picked.mode == m for m in ("dry-run", "proof", "probe"))
    cli.cut_flow(
        run=picked.run,
        plain=picked.run is None,
        preset=picked.preset,
        dry_run=dry_run,
        proof=proof,
        probe=probe,
    )


# --- redo, backlog, notes -----------------------------------------------------
def redo() -> None:
    sidecar = _pick_run("Requeue cards of which run?")
    if sidecar is None:
        return
    refs: list[str] = []
    while ref := _ask(
        questionary.text(
            "Card name, p3 (page), p3.5 (page 3 slot 5), last, d1 (duplex page); Enter when done:"
        )
    ).strip():
        refs.append(ref)
    if refs:
        cli.queue_redo(refs, sidecar)


def backlog_sheet() -> None:
    out = runs_parent()
    cli._show_backlog(out)  # leaves the step when the backlog is empty
    picked = _ask(
        questionary.checkbox(
            "Sheet options (space ticks, Enter builds):",
            choices=[
                Choice("print double-sided cards' backs (separate duplex PDF)", "backs"),
                Choice("full sheets only, a partial last page stays queued", "full_only"),
            ],
        )
    )
    result = cli.build_backlog(out, backs="backs" in picked, full_only="full_only" in picked)
    if result is not None:
        _after_build(result)


def notes() -> None:
    sidecar = _pick_run("Notes of which run?")
    if sidecar is not None:
        cli._notes(sidecar.parent.name, sidecar.parent.parent)
