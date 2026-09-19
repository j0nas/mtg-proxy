"""run.json — what a build produced, so ``mtg-proxy cut`` needs no retyped flags.

Written next to the PDF by every build. ``cut`` looks for it in the current
directory (or the directory / file given), and takes paper, card size and
registration pattern from it — the one mismatch that ruins a sheet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .paths import SIDECAR_NAME


@dataclass
class RunInfo:
    name: str
    paper: str
    card_size: str
    registration: str
    cards: int
    fronts_only: bool
    generated: str
    pdf: str | None = None
    duplex_pdf: str | None = None
    dfc_count: int = 0
    template: str | None = None
    cut_offset_mm: dict[str, float] = field(default_factory=dict)
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


def find_sidecar(where: Path | None) -> Path | None:
    """Resolve ``where`` (None = cwd; a dir; or the file itself) to an existing run.json."""
    base = where or Path.cwd()
    if base.is_file():
        return base
    candidate = base / SIDECAR_NAME
    return candidate if candidate.is_file() else None
