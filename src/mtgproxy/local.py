"""Your own card images (``make --images DIR``): a deck line whose card has an image in DIR
prints from that file, the rest of the deck is fetched from Scryfall as usual.

An image belongs to a deck line by the slug of the card's front-face name, the rule the files
are named by: ``Fell the Profane // Fell Mire`` → ``fell-the-profane.jpg``, ``Norn's Annex`` →
``norn-s-annex.jpg``. Case, punctuation, set code and collector number don't matter; .jpg,
.jpeg and .png are read.

The build splits the decklist (:func:`split_deck`). The engine fetches a copy holding only the
other lines, exactly as written, and :func:`stage` copies the images into game/front under the
engine's own file names, ``<index><CleanName><copy>.<ext>``. Every card's index is its line
number in the original decklist (:func:`place_indices` renumbers the engine's), so the sheet
keeps the deck's order, and trimming, the manifest and the PDF treat a local card like any
other. A local image is always one face, even for a double-faced name: it never gets a back in
game/double_sided, so it never goes to the duplex PDF, and nothing about it is looked up.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
# Decklist formats the engine reads line by line, offline. The others carry their own image
# URLs (scryfall_json, cubecobra_csv, mpcfill_xml) or are fetched whole (url).
FORMATS = ("mtga", "moxfield", "archidekt", "mtgo", "deckstats", "simple")
BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
# MTGA section markers, never cards (the engine's parse_mtga skips the same).
SECTION_HEADERS = {"about", "commander", "companion", "deck", "sideboard", "maybeboard", "tokens"}
# Lenient on purpose: it only has to find the card's name, in any of FORMATS.
CARD_LINE = re.compile(
    r"^(?:(?P<qty>\d+)x?\s+)?"  # 1 / 1x / nothing (simple)
    r"(?:\[[^\]]*\]\s+)?"  # deckstats [SET#cn]
    r"(?P<name>.+?)"
    r"(?:\s+\(\w+\)\s+\S+.*|\s+[\[*^#].*)?$"  # (SET) cn ...; archidekt [Category], *F*, ^tag^; #!
)

ReadLines = Callable[[list[str], str], list[tuple[str, str, str, int] | None]]


class LocalImageError(Exception):
    """A user-facing problem with --images (missing folder, two images for one card)."""


def slug(name: str) -> str:
    """The file stem a card's image goes by: front face, alphanumerics, dashes, lower case."""
    return re.sub(r"[^A-Za-z0-9]+", "-", name.split(" // ")[0]).strip("-").lower()


def clean(name: str) -> str:
    """The engine's file-name form of a card name (plugins/mtg/common.py remove_nonalphanumeric)."""
    return re.sub(r"[^\w]", "", name)


def is_basic(name: str) -> bool:
    """Basic land by name, snow-covered included: the engine's --skip_basics rule without asking Scryfall."""
    return slug(name).removeprefix("snow-covered-") in BASICS


def read_line(line: str) -> tuple[str, int] | None:
    """(card name, quantity) of a decklist line, or None for blanks, comments and headers."""
    s = line.strip()
    if not s or s.startswith(("#", "//")) or s.rstrip(":").lower() in SECTION_HEADERS:
        return None
    if re.match(r"^Name\s+(?!\()", s):  # MTGA "About" section: the deck's name
        return None
    m = CARD_LINE.match(s)
    if not m:
        return None
    return m["name"].strip(), int(m["qty"] or 1)


@dataclass
class LocalImages:
    dir: Path
    by_slug: dict[str, Path]

    @classmethod
    def from_dir(cls, directory: Path) -> LocalImages:
        if not directory.is_dir():
            raise LocalImageError(f"--images dir not found: {directory}")
        by_slug: dict[str, Path] = {}
        for f in sorted(directory.iterdir()):
            # Dotfiles: macOS writes ._name.jpg resource forks beside the real images.
            if not f.is_file() or f.name.startswith(".") or f.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            key = slug(f.stem)
            if not key:
                continue
            if key in by_slug:
                raise LocalImageError(f"two images for {key!r} in {directory}: {by_slug[key].name}, {f.name}")
            by_slug[key] = f
        if not by_slug:
            raise LocalImageError(f"no .jpg or .png card images in {directory}")
        return cls(directory, by_slug)

    def match(self, name: str) -> Path | None:
        return self.by_slug.get(slug(name))


@dataclass
class Line:
    pos: int  # line number in the decklist, which is also the card's file index
    text: str  # as written
    name: str
    qty: int
    image: Path | None = None


@dataclass
class Split:
    """One decklist, split into cards printed from local images and lines the engine fetches."""

    images: LocalImages
    skip_basics: bool
    local: list[Line] = field(default_factory=list)  # staged from their image
    fetched: list[Line] = field(default_factory=list)  # handed to the engine as written
    basics: list[Line] = field(default_factory=list)  # local basic lands left out (no --basics)
    unread: list[Line] = field(default_factory=list)  # no image, and the format's parser skips them
    unused: list[Path] = field(default_factory=list)  # images no deck line asks for

    @property
    def local_cards(self) -> int:
        return sum(c.qty for c in self.local)

    @property
    def fetched_cards(self) -> int:
        return sum(c.qty for c in self.fetched if not self._basic(c))

    @property
    def basics_left_out(self) -> int:
        return sum(c.qty for c in self.basics) + sum(c.qty for c in self.fetched if self._basic(c))

    def _basic(self, c: Line) -> bool:
        return self.skip_basics and is_basic(c.name)

    @property
    def positions(self) -> list[int]:
        """The deck line of the engine's k-th card (k = 1..): see :func:`place_indices`."""
        return [c.pos for c in self.fetched]

    @property
    def fetch_text(self) -> str:
        return "".join(c.text + "\n" for c in self.fetched)

    def summary(self) -> str:
        text = f"local images: {self.local_cards} card(s) from {self.images.dir}, {self.fetched_cards} from Scryfall"
        if self.basics_left_out:
            text += f"; {self.basics_left_out} basic land(s) left out (--basics prints them)"
        if self.unused:
            text += f"; {len(self.unused)} image(s) match no deck line"
        return text

    def unread_note(self, fmt: str) -> str | None:
        if not self.unread:
            return None
        shown = ", ".join(repr(c.text.strip()) for c in self.unread[:5])
        if len(self.unread) > 5:
            shown += ", …"
        hint = ""
        if fmt.lower() in ("moxfield", "archidekt"):  # both need (SET) number on every line
            hint = " (-f mtga also reads lines without a printing)"
        return (
            f"NOTE: {len(self.unread)} line(s) have no image in {self.images.dir} and the {fmt} format "
            f"can't read them, so they print nothing{hint}: {shown}"
        )


def split_deck(
    text: str, fmt: str, images: LocalImages, *, skip_basics: bool, read_lines: ReadLines
) -> Split:
    """Which lines of ``text`` print from ``images`` and which the engine fetches.

    ``read_lines`` is the engine's reading of each line (engine.read_deck_lines): a line without
    an image is fetched only if the engine reads it, and then exactly as written. A line with
    an image is local whatever the format makes of it, so a moxfield list's bare ``1 Counterspell``
    lines, which the engine's moxfield parser skips, still find their image.
    """
    lines = text.splitlines()
    split = Split(images, skip_basics)
    used: set[Path] = set()
    for pos, (line, engine_view) in enumerate(zip(lines, read_lines(lines, fmt), strict=True), 1):
        mine = read_line(line)
        image = images.match(mine[0]) if mine else None
        if mine and image:
            used.add(image)
            card = Line(pos, line, mine[0], mine[1], image)
            (split.basics if skip_basics and is_basic(card.name) else split.local).append(card)
        elif engine_view is not None:
            split.fetched.append(Line(pos, line, mine[0] if mine else engine_view[0], engine_view[3]))
        elif mine:
            split.unread.append(Line(pos, line, *mine))
    split.unused = [p for p in images.by_slug.values() if p not in used]
    return split


def stage(cards: list[Line], front: Path) -> list[tuple[Line, Path]]:
    """Copy each card's image into ``front`` once per copy, named the way the engine names a fetch.
    Copies, never links: ``--trim`` rewrites staged files in place."""
    staged = []
    for c in cards:
        assert c.image is not None
        for n in range(1, c.qty + 1):
            dst = front / f"{c.pos}{clean(c.name)}{n}{c.image.suffix.lower()}"
            shutil.copyfile(c.image, dst)
            staged.append((c, dst))
    return staged


def place_indices(scryfall: ModuleType, positions: list[int]) -> None:
    """Number the engine's k-th fetched card ``positions[k - 1]`` instead of k.

    The engine numbers cards by their place in the decklist it reads, which here is the split's
    copy without the local lines; this puts each one back on its original line, so fetched and
    local cards interleave in deck order. Wraps ``scryfall.fetch_card`` once per process; a later
    call re-points it (an empty list numbers as the engine does).
    """
    current = scryfall.fetch_card
    if getattr(current, "_mtgproxy_positions", None) is not None:
        current._mtgproxy_positions = list(positions)  # type: ignore[attr-defined]
        return
    if not positions:
        return
    real = current

    def placed(index, *args, **kwargs):
        pos: list[int] = placed._mtgproxy_positions  # type: ignore[attr-defined]
        return real(pos[index - 1] if 0 < index <= len(pos) else index, *args, **kwargs)

    placed._mtgproxy_positions = list(positions)  # type: ignore[attr-defined]
    scryfall.fetch_card = placed


def gather(cards: list[tuple[str, Path]], dest: Path) -> int:
    """``backlog build``: copy the queued local cards' images into ``dest`` under their card's
    slug, so the run reads them with --images like any deck (and a later rebuild finds them).
    Checks every file before copying any; returns how many images were copied."""
    chosen: dict[str, Path] = {}
    for name, src in cards:
        if not src.is_file():
            raise LocalImageError(
                f"local image missing: {src} ({name}) — restore it or `backlog drop` the card"
            )
        key = slug(name)
        if chosen.setdefault(key, src) != src:
            raise LocalImageError(
                f"two different local images for {name!r}: {chosen[key]}, {src} — `backlog drop` one of them"
            )
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.iterdir():
        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES:
            f.unlink()  # this folder is ours: an earlier build of the same run name
    for key, src in chosen.items():
        shutil.copyfile(src, dest / f"{key}{src.suffix.lower()}")
    return len(chosen)
