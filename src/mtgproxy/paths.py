"""Where things live. The repo root is derived from this file, never from cwd."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCM = ROOT / "silhouette-card-maker"  # vendored PDF engine (fork j0nas/silhouette-card-maker, local-patches)
DRV = ROOT / "inkscape-silhouette"  # vendored cutter driver (fablabnbg/inkscape-silhouette)
DRV_PY = DRV / ".venv" / "bin" / "python"
DATA = ROOT / "data"
TEMPLATES = ROOT / "templates"
ASSETS = ROOT / "assets"
DECKS = ROOT / "decks"
CUT_OFFSET_FILE = DATA / "cut_offset.json"
DUPLEX_OFFSET_FILE = DATA / "offset_data.json"
DEFAULT_BACK = ASSETS / "back.png"
GAME = SCM / "game"
SIDECAR_NAME = "run.json"
NOTES_NAME = "CUT-NOTES.md"


def cache_dir() -> Path:
    """Persistent Scryfall image cache: $MTG_PROXY_CACHE, else ~/.cache/mtg-proxy."""
    env = os.environ.get("MTG_PROXY_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "mtg-proxy"
