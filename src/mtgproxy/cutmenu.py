"""The menu a bare ``cut-proxies`` shows at a terminal.

Enter on the first question cuts the default: this folder's run.json, or a plain A4 sheet
(standard cards, the default layout) when there is none, with the laminate preset. "Choose…" asks
which run, which preset and what to do (cut, proof first, dry run, probe). The connection is
never asked: Bluetooth LE unless ``--usb`` is given.

After every cut at a terminal, however it was started, cut-proxies watches the mat: unloading
it and loading the next sheet starts that sheet by itself (cli._await_next_sheet). Enter instead
brings up ``after_cut``: the same job on a new sheet (without proof cuts once a first sheet
passed them, unless asked), one more pass on this one, or done. ``after_error`` offers a retry
when a job fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import questionary
from questionary import Choice

from .cutting import PRESETS
from .paths import SIDECAR_NAME
from .sidecar import RunInfo

PLAIN = "plain A4 sheet: standard cards, 4x2"
MODES = {
    "cut": "cut",
    "proof": "proof cuts on the marks first, then y cuts / r rescans / q stops (first sheet only)",
    "dry-run": "dry run: no machine, checks the job",
    "probe": "probe: scan the marks, log what the machine reports, cut nothing",
}


@dataclass
class Picked:
    run: Path | None  # run.json to cut; None = the plain sheet
    preset: str = "laminate"
    mode: str = "cut"  # a key of MODES


def runs_below(where: Path) -> list[Path]:
    """run.json here first, then the folders right below, newest first."""
    below = sorted(where.glob(f"*/{SIDECAR_NAME}"), key=lambda p: p.stat().st_mtime, reverse=True)
    here = where / SIDECAR_NAME
    return [p for p in [here, *below] if p.is_file() and describe(p)]


def describe(sidecar: Path) -> str | None:
    """One line for the menu; None when run.json can't be read."""
    try:
        i = RunInfo.read(sidecar)
    except (OSError, ValueError, TypeError):
        return None
    return f"{i.name}: {i.cards} cards, {i.paper}/{i.card_size} {i.layout}"


def ask(where: Path, via: str) -> Picked | None:
    """Ask at the terminal; None when the operator quits. Ctrl-C raises KeyboardInterrupt."""
    runs = runs_below(where)
    here = where / SIDECAR_NAME
    default = Picked(here if here in runs else None)
    sheet = describe(default.run) if default.run else PLAIN
    first = _select(
        "cut-proxies:",
        [
            Choice(f"Cut {sheet} · laminate · {via}", "default"),
            Choice("Choose run, preset, mode…", "choose"),
            Choice("Quit", "quit"),
        ],
    )
    if first == "default":
        return default
    if first == "quit":
        return None
    # The default sheet first, so Enter keeps it.
    sheets = [Choice(describe(p), str(p)) for p in runs]
    plain = Choice(PLAIN, "")
    sheets = [*sheets, plain] if default.run else [plain, *sheets]
    run = _select("Sheet:", sheets)
    preset = _select(
        "Preset:",
        [
            Choice(f"{n}: force {p.force} · speed {p.speed} · depth {p.depth} · {p.passes} pass(es)", n)
            for n, p in PRESETS.items()
        ],
    )
    mode = _select("Mode:", [Choice(title, key) for key, title in MODES.items()])
    return Picked(Path(run) if run else None, preset, mode)


def after_cut(sheet: int, proof: bool = False) -> str:
    """What's next: "next" (same job, new sheet), "pass" (one more pass on this sheet) or "done".
    A proofed job's next sheet skips the proof cuts; "next-proof" asks for them again."""
    nxt = "next sheet: it's loaded, cut it now, same job"
    choices = [Choice(f"{nxt}, no proof cuts" if proof else nxt, "next")]
    if proof:
        choices.append(Choice("next sheet, proof cuts first", "next-proof"))
    return _select(
        f"Sheet {sheet} done. Lift a corner:",
        [
            *choices,
            Choice("one more pass: not quite through (rescans, cuts once, no proof)", "pass"),
            Choice("done", "done"),
        ],
    )


def after_error(sheet: int) -> str:
    """After a failed job (marks not found, proof stopped, connection lost): "retry" or "done"."""
    return _select(
        f"Sheet {sheet} failed. Straighten or reload it, or check the Cameo, then:",
        [Choice("try again: same job", "retry"), Choice("done", "done")],
    )


def _select(message: str, choices: list[Choice]) -> str:
    return questionary.select(message, choices=choices).unsafe_ask()
