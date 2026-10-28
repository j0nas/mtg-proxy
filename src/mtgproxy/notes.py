"""CUT-NOTES.md: the print & cut checklist generated for one exact run."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .cutting import PRESETS
from .paths import NOTES_NAME


@dataclass
class NotesContext:
    name: str
    paper: str
    card_size: str
    cards: int
    fronts_only: bool
    duplex_dfc: bool
    dfc_count: int
    today: str = ""
    layout: str = "3x3"

    def __post_init__(self) -> None:
        self.today = self.today or date.today().isoformat()


# What a 3x3 sheet (layout.py) changes in the checklist; a 4x2 sheet's stays as it was.
PRINT_3X3 = """\
1. **Actual size / 100%** scale, borderless **OFF**. The sheet is **portrait A4**; the PDF page is
   landscape like every sheet's (the engine stores them that way), so print it as you print a 4x2
   sheet. The marks' outer edges are **3.0 mm** from the paper's sides (8.5 mm from the top and
   bottom), 0.04 mm inside what the ET-8550 can print (2.96 mm margin all round, its CUPS
   ImageableArea): any scaling clips or moves them."""
MAT_3X3 = """\
1. Sheet on the mat **portrait, the black square top-left** (turn the sheet round if it printed
   the other way): its top-left on the grid's top-left, aligned to the *paper* edge, not the
   laminate edge. If the scan fails, cover the mat's grid lines and text around that corner with
   masking tape, the paper overlapping it (the card-maker's own advice for marks this close to
   the edge)."""


def render(c: NotesContext) -> str:
    sides = "fronts only" if c.fronts_only else "double-sided"
    dfc_note = ""
    if c.dfc_count > 0:
        dfc_note = (
            f" | double-faced cards: {c.dfc_count} (separate duplex PDF)"
            if c.duplex_dfc
            else f" | double-faced cards: {c.dfc_count} (both faces as separate cards)"
        )
    if c.fronts_only:
        step2 = "Fronts only (default) — print single-sided. Rerun with --backs for double-sided."
    else:
        step2 = (
            "Double-sided = manual duplex, **long-edge flip**. Check front/back alignment "
            "against a light before laminating a whole batch."
        )
    if c.dfc_count > 0 and c.duplex_dfc:
        step2 += (
            f"\n   - **{c.name}-duplex.pdf** holds the {c.dfc_count} double-sided card image(s): print that file "
            "with manual duplex\n     (**long-edge flip**), then laminate and cut it like any other sheet."
        )
    elif c.dfc_count > 0 and c.fronts_only:
        step2 += (
            f"\n   - {c.dfc_count} double-faced card(s): both faces are in the main PDF as separate single-sided"
            "\n     cards (--split-faces). Rerun without it for one physical double-sided card."
        )
    paper_flag = "" if c.paper == "a4" else f" -p {c.paper}"
    lam, paper = PRESETS["laminate"], PRESETS["paper"]
    three = c.layout == "3x3"
    print1 = (
        PRINT_3X3
        if three
        else """1. **Actual size / 100%** scale, borderless **OFF** — any scaling shifts the registration
   marks off their expected positions and registration fails."""
    )
    mat1 = (
        MAT_3X3
        if three
        else """1. Sheet on the mat: its top-left on the grid's top-left, aligned to the *paper* edge, not the
   laminate edge."""
    )
    return f"""# {c.name} — print & cut checklist

Generated: {c.today} | paper: {c.paper} | card: {c.card_size} | layout: {c.layout} | registration: 3-mark | cards: {c.cards} | sides: {sides}{dfc_note}

## Print (your usual ET-8550 profile)
{print1}
2. {step2}
3. Let ink dry before laminating.

## Laminate
- 80 µm pouches; run one grade hotter than the pouch rating if lamination looks cloudy.
- Feed the sealed edge first. Re-laminate cut cards once more at the end to seal edges.

## Cut (Cameo 5 Alpha)
With the Cameo on and in Bluetooth range of the Mac (`--usb` for the cable), `cd` into this folder
and run `cut-proxies`. It reads run.json here: {c.paper} paper, {c.layout} layout. Explicit
form: `cut-proxies --layout {c.layout}{paper_flag}`.
{mat1}
2. Laminate settings by default: **Force {lam.force} · Speed {lam.speed} · Depth {lam.depth} · Passes {lam.passes}**.
   Plain paper: `--preset paper` (Force {paper.force} · Speed {paper.speed} · Depth {paper.depth} · Passes {paper.passes}).
3. Another pass on the same sheet: Enter after it is cut, then "one more pass" (it rescans the
   marks and cuts once). Rippled or torn edges mean too much force or a dull blade.
"""


def latest_notes(parent: Path) -> Path | None:
    """Newest CUT-NOTES.md one level below ``parent``."""
    candidates = [p for p in parent.glob(f"*/{NOTES_NAME}") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def show(path: Path) -> None:
    # glow pages nicely in a terminal; piped/captured output gets the raw markdown.
    if sys.stdout.isatty() and shutil.which("glow"):
        subprocess.run(["glow", "-p", str(path)], check=False)
    else:
        print(path.read_text())
