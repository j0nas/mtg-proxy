"""Per-card edge trim: replace a card image's outermost ring with the pixels just inside.

Why: the engine builds print bleed by smearing the outermost pixel row of each
image outward. Every Scryfall scan carries a thin rim there; on black-bordered
cards it's black on black, but on borderless / extended-art printings the rim
is a dark line against bright art and the bleed becomes a visible band. The
engine's own --extend_edges does this for every card; this does it for the
cards you name (``--trim "Temple Garden"``), keeping image size and geometry.

Trimmed files are recorded in game/.trimmed.json so a --skip-fetch rerun
doesn't trim them a second time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from PIL import Image

from . import engine

DEFAULT_MM = 0.3
CARD_WIDTH_MM = 63.0
STATE_FILE = engine.GAME / ".trimmed.json"
CARD_FILE = re.compile(r"^\d+(.+?)\d+(?:-back)?\.\w+$")


class TrimError(Exception):
    pass


@dataclass(frozen=True)
class TrimSpec:
    name: str  # "all" or a card name
    mm: float = DEFAULT_MM

    @property
    def key(self) -> str:
        return clean_name(self.name)


def clean_name(name: str) -> str:
    """The engine's file-name form of a card name: alphanumerics only, case-folded here."""
    return re.sub(r"[^A-Za-z0-9]", "", name).lower()


def parse_spec(text: str) -> TrimSpec:
    """'Temple Garden' | 'Temple Garden:0.5' | 'all' | 'all:0.2'."""
    name, sep, mm = text.rpartition(":")
    if sep and re.fullmatch(r"\d+(\.\d+)?", mm.strip()):
        value = float(mm)
    else:
        name, value = text, DEFAULT_MM
    name = name.strip()
    if not name:
        raise TrimError(f"empty card name in --trim {text!r}")
    if not 0 < value <= 3:
        raise TrimError(f"--trim width must be between 0 and 3 mm, got {value} ({text!r})")
    return TrimSpec(name, value)


def trim_image(im: Image.Image, px: int) -> Image.Image:
    """Same-size image whose outer ``px`` ring is the ring just inside it, extended outward."""
    if px <= 0:
        return im
    w, h = im.size
    inner = im.crop((px, px, w - px, h - px))
    out = Image.new(im.mode, (w, h))
    out.paste(inner, (px, px))
    for i in range(px):
        out.paste(inner.crop((0, 0, inner.width, 1)), (px, i))  # top rows ← inner row 0
        out.paste(inner.crop((0, inner.height - 1, inner.width, inner.height)), (px, h - 1 - i))
    for i in range(px):
        out.paste(out.crop((px, 0, px + 1, h)), (i, 0))  # left cols ← col px (already has top/bottom)
        out.paste(out.crop((w - 1 - px, 0, w - px, h)), (w - 1 - i, 0))
    return out


def _load_state() -> dict[str, float]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def reset_state() -> None:
    STATE_FILE.unlink(missing_ok=True)


def apply_trims(specs: list[TrimSpec]) -> tuple[list[str], list[str]]:
    """Trim matching images in game/front and game/double_sided. Returns (trimmed files, unmatched names)."""
    if not specs:
        return [], []
    by_key = {s.key: s for s in specs}
    trim_all = by_key.pop("all", None)
    state = _load_state()
    done: list[str] = []
    matched: set[str] = set()
    for d in (engine.FRONT, engine.DOUBLE_SIDED):
        for f in engine.images_in(d):
            m = CARD_FILE.match(f.name)
            if not m or "_token" in f.name:
                continue
            key = m.group(1).lower()
            spec = by_key.get(key) or trim_all
            if spec is None:
                continue
            if key in by_key:
                matched.add(key)
            rel = str(f.relative_to(engine.GAME))
            if state.get(rel) == spec.mm:
                continue  # already trimmed by this much (--skip-fetch rerun)
            with Image.open(f) as im:
                px = max(1, round(spec.mm * im.width / CARD_WIDTH_MM))
                trim_image(im, px).save(f)
            state[rel] = spec.mm
            done.append(rel)
    STATE_FILE.write_text(json.dumps(state, indent=1))
    unmatched = [s.name for s in specs if s.key != "all" and s.key not in matched]
    return done, unmatched
