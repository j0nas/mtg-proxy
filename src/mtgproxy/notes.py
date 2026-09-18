"""CUT-NOTES.md: the print & cut checklist generated for one exact run."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .paths import NOTES_NAME


@dataclass
class NotesContext:
    name: str
    paper: str
    card_size: str
    registration: str
    cards: int
    fronts_only: bool
    duplex_dfc: bool
    dfc_count: int
    template_name: str | None
    template_baked: bool
    cut_offset_y_mm: float
    today: str = ""

    def __post_init__(self) -> None:
        self.today = self.today or date.today().isoformat()


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
            "with manual duplex\n     (**long-edge flip**), then laminate and cut it like any other sheet — "
            "same template,\n     same machine profile, same cut settings."
        )
    elif c.dfc_count > 0 and c.fronts_only:
        step2 += (
            f"\n   - {c.dfc_count} double-faced card(s): both faces are in the main PDF as separate single-sided"
            "\n     cards (--split-faces). Rerun without it for one physical double-sided card."
        )
    paper_flag = "" if c.paper == "a4" else f" -p {c.paper}"
    if c.registration == "4" and c.template_baked:
        machine = (
            "Machine & media: the template opens with **Cameo 5 Alpha** selected and media set\n"
            "   to **A4** (both baked into the template base) — verify, change nothing else. If media\n"
            '   ever reads "Custom", switch it to A4: Custom media warps the cuts.'
        )
    else:
        profile = (
            "Cameo 5 Alpha"
            if c.registration == "4"
            else "Cameo 5 (yes, plain 5 — 3-mark PDFs need the old profile even on Alpha hardware)"
        )
        machine = (
            "Set the machine profile MANUALLY to match this PDF's marks — auto-detect picks wrong on some\n"
            f"   firmware/Studio combos: this PDF is **{c.registration}-mark**, so select **{profile}**."
        )
    if c.cut_offset_y_mm:
        machine += (
            f"\n   - Cut paths in this template are pre-shifted **{c.cut_offset_y_mm:g}mm down** (the machine cuts high\n"
            "     relative to the scanned marks). Do NOT nudge shapes in Studio — tune the number in\n"
            "     data/cut_offset.json instead and rerun."
        )
    postit = (
        "4-mark pattern: cover the cards nearest BOTH bottom corners."
        if c.registration == "4"
        else "3-mark pattern: cover the card nearest the bottom-left L mark."
    )
    return f"""# {c.name} — print & cut checklist

Generated: {c.today} | paper: {c.paper} | card: {c.card_size} | registration: {c.registration}-mark | cards: {c.cards} | sides: {sides}{dfc_note}

## Print (your usual ET-8550 profile)
1. **Actual size / 100%** scale, borderless **OFF** — any scaling shifts the registration
   marks off their expected positions and registration fails.
2. {step2}
3. Let ink dry before laminating.

## Laminate
- 80 µm pouches; run one grade hotter than the pouch rating if lamination looks cloudy.
- Feed the sealed edge first. Re-laminate cut cards once more at the end to seal edges.

## Cut (Cameo 5 Alpha)
**Preferred — no Studio:** with the Cameo on USB or Bluetooth from the Mac, `cd` into this
folder and run `cut-proxies` (it reads run.json here: {c.registration}-mark scan, {c.paper} paper;
see README §5). Explicit form: `cut-proxies -r {c.registration}{paper_flag}`.
Studio fallback:
1. Open `{c.template_name or "<template>"}` in Silhouette Studio (Studio **v5.0.402+** needed for the Alpha;
   the limited "Starter" edition of Studio v5 is incompatible — use the full edition).
2. {machine}
3. Sheet on mat: top-left of the mat grid, aligned to the *paper* edge, not the laminate edge.
4. Post-it trick (light-colored, remove after registration scan, before cutting):
   {postit}
5. Starting cut settings (AutoBlade, 135 gsm photo paper + 80 µm matte laminate):
   **Force 25 · Speed 25 · Depth 5 · Passes 3** (cut-proxies defaults)
   Tune passes first, then force. Rippled/torn edges = force too high or blade dull.
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
