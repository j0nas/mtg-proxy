"""run.json — what a build produced, so ``mtg-proxy cut`` needs no retyped flags.

Written next to the PDF by every build. ``cut`` looks for it in the current
directory (or the directory / file given), and takes paper, card size and layout
from it — the one mismatch that ruins a sheet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .paths import SIDECAR_NAME, out_root


@dataclass
class RunInfo:
    name: str
    paper: str
    card_size: str
    cards: int
    fronts_only: bool
    generated: str
    # Always 3 now; run.json files from before 3-mark only may say 4, and cut refuses those sheets.
    registration: str = "3"
    layout: str = "4x2"  # 4x2 | 3x3 (layout.py); run.json files from before layouts are 4x2
    # The sheets carry the layout's proof targets (layout.proof_targets_mm); sheets from before them don't.
    proof_targets: bool = False
    pdf: str | None = None
    duplex_pdf: str | None = None
    dfc_count: int = 0
    trims: dict[str, float] = field(default_factory=dict)
    decklist: str | None = None
    source: str | None = None
    deferred: int = 0  # cards pushed to BACKLOG.txt by --defer-partial
    options: dict = field(default_factory=dict)  # build options needed to re-derive the sheets
    sheets: dict = field(default_factory=dict)  # per-slot manifest, see manifest.py
    version: str = __version__

    def write(self, out_dir: Path) -> Path:
        p = out_dir / SIDECAR_NAME
        p.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return p

    @classmethod
    def read(cls, path: Path) -> RunInfo:
        raw = json.loads(path.read_text())
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def runs_parent(cwd: Path | None = None) -> Path:
    """The folder that holds the deck folders and BACKLOG.txt.

    The current folder when deck folders sit right below it, its parent when it is a deck
    folder itself (runs built with -o, or before the proxies folder), else paths.out_root().
    """
    here = cwd or Path.cwd()
    if (here / SIDECAR_NAME).is_file():
        return here.parent
    if any(here.glob(f"*/{SIDECAR_NAME}")):
        return here
    return out_root()


def newest_run(parent: Path) -> Path | None:
    """The newest run.json one folder below ``parent``."""
    found = [p for p in parent.glob(f"*/{SIDECAR_NAME}") if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def find_sidecar(where: Path | None) -> Path | None:
    """Resolve ``where`` to an existing run.json: None = cwd; a dir; the file itself; or a
    run's folder name in the proxies folder (``--run quintorius``)."""
    base = where or Path.cwd()
    if where is not None and not base.exists() and len(where.parts) == 1:
        base = out_root() / where
    if base.is_file():
        return base
    candidate = base / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def find_run(where: Path | None) -> Path | None:
    """find_sidecar, falling back to the newest run when no --run is given outside a deck folder."""
    found = find_sidecar(where)
    if found is None and where is None:
        found = newest_run(runs_parent())
    return found
