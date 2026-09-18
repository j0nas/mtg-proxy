"""Fetch a deck from Moxfield or Archidekt as an MTGA-format decklist.

Both sites expose the JSON their own front-ends load (unofficial but stable).
Every emitted line carries the exact printing chosen on the site
(set + collector number) — the Scryfall fetch honors those over its
--prefer_* art flags. Private decks are not reachable.

Boards are unified across sources:

  main         the deck proper (Moxfield: commanders + companions + mainboard;
               Archidekt: every card whose primary category is "included in deck")
  side         Moxfield sideboard / Archidekt "Sideboard" category
  considering  Moxfield maybeboard ("Considering", which the site can't export) /
               Archidekt "Maybeboard" category

The JSON→lines conversion is pure (``moxfield_entries`` / ``archidekt_entries``)
so it can be tested against recorded fixtures without network.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
BOARDS = ("main", "side", "considering")
BOARD_ALIASES = {
    "mainboard": "main",
    "sideboard": "side",
    "maybe": "considering",
    "maybeboard": "considering",
}


class DeckError(Exception):
    """A user-facing deck fetch problem (bad URL, private deck, empty board)."""


def normalize_board(board: str) -> str:
    b = BOARD_ALIASES.get(board.lower(), board.lower())
    if b not in BOARDS:
        raise DeckError(f"unknown board {board!r} (use: {', '.join(BOARDS)})")
    return b


def slugify(name: str, fallback: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or fallback


def format_line(qty: int, name: str, set_code: str, cn: str) -> str:
    return f"{qty} {name} ({set_code.upper()}) {cn}" if set_code and cn else f"{qty} {name}"


@dataclass
class Entries:
    """Lines for the requested board plus which boards were non-empty (for the error message)."""

    lines: list[str]
    nonempty_boards: list[str] = field(default_factory=list)


# --- Moxfield ---------------------------------------------------------------
MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all/{}"
# Moxfield board keys per unified board. "main" folds in the commander/companion
# boards so a Commander deck comes out whole.
MOXFIELD_BOARDS = {
    "main": ["commanders", "companions", "mainboard"],
    "side": ["sideboard"],
    "considering": ["maybeboard"],
}


def moxfield_entries(deck: dict, board: str) -> Entries:
    boards = deck.get("boards", {})
    lines = []
    for key in MOXFIELD_BOARDS[board]:
        for v in boards.get(key, {}).get("cards", {}).values():
            c = v.get("card", {})
            name = c.get("name", "")
            if not name:
                continue
            lines.append(format_line(v.get("quantity", 1), name, c.get("set", ""), c.get("cn", "")))
    nonempty = [b for b, keys in MOXFIELD_BOARDS.items() if any(boards.get(k, {}).get("count") for k in keys)]
    return Entries(sorted(lines, key=str.lower), nonempty)


# --- Archidekt --------------------------------------------------------------
ARCHIDEKT_API = "https://archidekt.com/api/decks/{}/"
# Archidekt's built-in non-deck categories, by lower-cased name.
ARCHIDEKT_CATEGORY_BOARD = {"sideboard": "side", "maybeboard": "considering"}


def archidekt_board_of(entry: dict, in_deck: dict[str, bool]) -> str:
    """Which unified board an Archidekt card sits on (primary-category rule)."""
    cats = entry.get("categories") or []
    if not cats:
        return "main"
    primary = cats[0]
    if in_deck.get(primary, True):
        return "main"
    return ARCHIDEKT_CATEGORY_BOARD.get(primary.lower(), "other")


def archidekt_entries(deck: dict, board: str) -> Entries:
    in_deck = {c.get("name", ""): bool(c.get("includedInDeck", True)) for c in deck.get("categories", [])}
    lines, seen = [], set()
    for v in deck.get("cards", []):
        b = archidekt_board_of(v, in_deck)
        seen.add(b)
        if b != board:
            continue
        c = v.get("card") or {}
        name = (c.get("oracleCard") or {}).get("name") or c.get("displayName") or ""
        if not name:
            continue
        set_code = (c.get("edition") or {}).get("editioncode") or ""
        lines.append(format_line(v.get("quantity", 1), name, set_code, c.get("collectorNumber") or ""))
    return Entries(sorted(lines, key=str.lower), sorted(seen - {"other"}))


# --- Sources ----------------------------------------------------------------
@dataclass(frozen=True)
class Source:
    name: str
    url_pattern: re.Pattern[str]
    api: str
    entries: Callable[[dict, str], Entries]
    id_pattern: re.Pattern[str]


SOURCES = (
    Source(
        "moxfield",
        re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)"),
        MOXFIELD_API,
        moxfield_entries,
        re.compile(r"[A-Za-z0-9_-]+"),
    ),
    Source(
        "archidekt",
        re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)"),
        ARCHIDEKT_API,
        archidekt_entries,
        re.compile(r"\d+"),
    ),
)


def match_source(ref: str) -> tuple[Source, str] | None:
    """(source, deck id) when ``ref`` is a deck URL of a known site, else None."""
    for src in SOURCES:
        m = src.url_pattern.search(ref)
        if m:
            return src, m.group(1)
    return None


def is_deck_url(ref: str) -> bool:
    return match_source(ref) is not None


@dataclass
class FetchedDeck:
    source: str
    name: str
    board: str
    lines: list[str]

    @property
    def slug(self) -> str:
        return slugify(self.name, "deck")

    @property
    def file_stem(self) -> str:
        return self.slug if self.board == "main" else f"{self.slug}-{self.board}"

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def fetch_json(url: str, timeout: int = 30) -> dict:
    # urllib, not requests: Moxfield's WAF 403s the requests fingerprint but
    # accepts a plain urllib client with a browser User-Agent (2026-08).
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # fixed https hosts
        return json.load(r)


def fetch_deck(ref: str, board: str = "main", source_name: str | None = None) -> FetchedDeck:
    """Fetch ``ref`` (deck URL, or bare id with ``source_name``) as a decklist."""
    board = normalize_board(board)
    matched = match_source(ref)
    if matched:
        src, deck_id = matched
    else:
        src = next((s for s in SOURCES if s.name == source_name), None)
        if src is None or not src.id_pattern.fullmatch(ref):
            raise DeckError(f"can't find a Moxfield or Archidekt deck id in {ref!r}")
        deck_id = ref
    try:
        deck = fetch_json(src.api.format(deck_id))
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            raise DeckError(f"{src.name} deck {deck_id!r} not found — private decks are not visible") from e
        raise DeckError(f"{src.name} API returned HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise DeckError(f"{src.name} unreachable: {e.reason}") from e
    name = deck.get("name") or deck_id
    entries = src.entries(deck, board)
    if not entries.lines:
        raise DeckError(
            f"board {board!r} of {name!r} is empty (non-empty boards: {', '.join(entries.nonempty_boards) or 'none'})"
        )
    return FetchedDeck(src.name, name, board, entries.lines)
