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
    cards: int
    duplex_dfc: bool
    dfc_count: int
    today: str = ""
    foil: bool = False  # holographic sticker paper (make --foil): no laminate, a kiss cut, then a common

    def __post_init__(self) -> None:
        self.today = self.today or date.today().isoformat()


PRINT = """\
1. **Actual size / 100%** scale, borderless **OFF**. The sheet is **portrait A4**; the PDF page is
   landscape (the engine stores it that way), and `--print` turns it upright for the driver. The
   marks' outer edges are **3.0 mm** from the paper's sides (8.5 mm from the top and
   bottom), 0.04 mm inside what the ET-8550 can print (2.96 mm margin all round, its CUPS
   ImageableArea): any scaling clips or moves them."""
MAT = """\
1. Sheet on the mat **portrait, the black square top-left** (turn the sheet round if it printed
   the other way): its top-left on the grid's top-left, aligned to the *paper* edge, not the
   laminate edge. If the scan fails, cover the mat's grid lines and text around that corner with
   masking tape, the paper overlapping it (the card-maker's own advice for marks this close to
   the edge)."""


def render_foil(c: NotesContext) -> str:
    foil = PRESETS["foil"]
    faces = (
        f"\n   {c.dfc_count} double-faced card(s): each face is its own sticker, one for each side of the common."
        if c.dfc_count
        else ""
    )
    return f"""# {c.name} — foil print & cut checklist

Generated: {c.today} | 3x3 on A4 | registration: 3-mark | cards: {c.cards} | holographic sticker paper

## Print
{PRINT}
2. One sheet of holographic sticker paper in the rear feeder, with the printer's **Thick Paper**
   setting on and its drying time set to **Longer**. `mtg-proxy print --paper holo` (or the
   wizard) sends it to the `Holographic sticker proxies` queue: Epson Ultra Glossy, High.{faces}
3. Let the ink dry fully before touching the film. No laminating.

## Cut (Cameo 5 Alpha)
`cd` into this folder and run `mtg-proxy cut`: run.json says foil, so it kiss-cuts with the foil preset.
1. Sheet on the mat **portrait, the black square top-left**, its top edge on the grid's top line and
   its corner about **2.5 mm right** of the grid's corner: further left, the sensor can't reach the
   paper beside the left marks and the run stops.
2. **Force {foil.force} · Speed {foil.speed} · Depth {foil.depth} · Passes {foil.passes}**, through the film and
   not the backing. On the first sheet choose "proof cut first" in the menu: it traces a printed
   square's edge at this same blade. To look at it, lift the lid if you can; if you unload the mat
   to peel the square, the run stops there (the sheet no longer lies where its marks were read),
   and a rerun without the proof registers it again. Square peels off cleanly, backing whole: `y`.
   Film torn or not cut through: `q`, then `--force 8` or more.

## Finish
Peel each card and line it up with a bulk common's edges before pressing it down.
"""


def render(c: NotesContext) -> str:
    if c.foil:
        return render_foil(c)
    dfc_note = ""
    if c.dfc_count > 0:
        dfc_note = (
            f" | double-faced cards: {c.dfc_count} (separate duplex PDF)"
            if c.duplex_dfc
            else f" | double-faced cards: {c.dfc_count} (both faces as separate cards)"
        )
    step2 = "Fronts only — print single-sided."
    if c.dfc_count > 0 and c.duplex_dfc:
        step2 += (
            f"\n   - **{c.name}-duplex.pdf** holds the {c.dfc_count} double-sided card image(s): print that file "
            "with manual duplex\n     (**long-edge flip**), then laminate and cut it like any other sheet."
        )
    elif c.dfc_count > 0:
        step2 += (
            f"\n   - {c.dfc_count} double-faced card(s): both faces are in the main PDF as separate single-sided"
            "\n     cards (--split-faces). Rerun without it for one physical double-sided card."
        )
    lam, paper = PRESETS["laminate"], PRESETS["paper"]
    return f"""# {c.name} — print & cut checklist

Generated: {c.today} | 3x3 on A4 | registration: 3-mark | cards: {c.cards}{dfc_note}

## Print (your usual ET-8550 profile)
{PRINT}
2. {step2}
3. Let ink dry before laminating.

## Laminate
- 80 µm pouches; run one grade hotter than the pouch rating if lamination looks cloudy.
- Feed the sealed edge first. Re-laminate cut cards once more at the end to seal edges.

## Cut (Cameo 5 Alpha)
With the Cameo on and in Bluetooth range of the Mac (`--usb` for the cable), `cd` into this folder
and run `mtg-proxy cut`. It reads run.json here.
{MAT}
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
