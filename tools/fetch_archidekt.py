#!/usr/bin/env python3
"""Fetch an Archidekt deck (or one of its boards) as an MTGA-format decklist.

Uses Archidekt's public JSON API (https://archidekt.com/api/decks/<id>/ — the
same endpoint the site itself loads; unofficial but stable). Archidekt has no
boards as such: every card sits in one or more *categories*, each flagged
includedInDeck or not. A card belongs to the deck when its primary (first)
category is includedInDeck; the stock non-deck categories are "Sideboard" and
"Maybeboard". This maps them onto the same --board names as fetch_moxfield.py:

  main         every card whose primary category counts towards the deck
               (Commander, Companion, custom categories, ...)
  side         primary category "Sideboard"
  considering  primary category "Maybeboard"

Every emitted line carries the exact printing (set + collector number) chosen
on Archidekt; fetch.py honors those over its --prefer_* art flags.

Usage:
  fetch_archidekt.py <url-or-deck-id> [--board main|side|considering] [--to DIR]

Prints the decklist to stdout. With --to DIR it instead writes
DIR/<deck>-<board>.txt (plain DIR/<deck>.txt for the main board) and prints
the file path — the mode make-proxies.sh uses when handed an archidekt.com URL.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://archidekt.com/api/decks/{}/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

BOARDS = ("main", "side", "considering")
ALIASES = {"mainboard": "main", "sideboard": "side", "maybe": "considering",
           "maybeboard": "considering"}
# Archidekt's built-in non-deck categories, by lower-cased name.
CATEGORY_BOARD = {"sideboard": "side", "maybeboard": "considering"}


def die(msg: str) -> None:
    sys.exit(f"fetch_archidekt: {msg}")


def board_of(entry: dict, in_deck: dict) -> str:
    """Which --board an Archidekt card entry belongs to (primary-category rule)."""
    cats = entry.get("categories") or []
    if not cats:
        return "main"
    primary = cats[0]
    if in_deck.get(primary, True):
        return "main"
    return CATEGORY_BOARD.get(primary.lower(), "other")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("deck", help="archidekt.com/decks/... URL or bare numeric deck id")
    p.add_argument("--board", default="main", help="main | side | considering")
    p.add_argument("--to", metavar="DIR", help="write DIR/<deck>[-<board>].txt and print its path")
    args = p.parse_args()

    m = re.search(r"archidekt\.com/(?:api/)?decks/(\d+)", args.deck)
    deck_id = m.group(1) if m else args.deck
    if not re.fullmatch(r"\d+", deck_id):
        die(f"can't find a numeric deck id in {args.deck!r}")

    board = ALIASES.get(args.board.lower(), args.board.lower())
    if board not in BOARDS:
        die(f"unknown board {args.board!r} (use: {', '.join(BOARDS)})")

    req = urllib.request.Request(API.format(deck_id),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            deck = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            die(f"deck {deck_id!r} not found — private decks are not visible to the API")
        die(f"Archidekt API returned HTTP {e.code}")

    in_deck = {c.get("name", ""): bool(c.get("includedInDeck", True))
               for c in deck.get("categories", [])}

    entries, seen_boards = [], set()
    for v in deck.get("cards", []):
        b = board_of(v, in_deck)
        seen_boards.add(b)
        if b != board:
            continue
        c = v.get("card") or {}
        name = (c.get("oracleCard") or {}).get("name") or c.get("displayName") or ""
        if not name:
            continue
        qty = v.get("quantity", 1)
        set_code = (c.get("edition") or {}).get("editioncode") or ""
        cn = c.get("collectorNumber") or ""
        entries.append(f"{qty} {name} ({set_code.upper()}) {cn}" if set_code and cn
                       else f"{qty} {name}")
    if not entries:
        die(f"board {board!r} of {deck.get('name')!r} is empty "
            f"(non-empty boards: {', '.join(sorted(seen_boards - {'other'})) or 'none'})")
    entries.sort(key=str.lower)
    text = "\n".join(entries) + "\n"

    print(f"{deck.get('name')!r} [{board}]: {len(entries)} entries", file=sys.stderr)
    if args.to:
        slug = re.sub(r"[^a-z0-9]+", "-", deck.get("name", "").lower()).strip("-") or deck_id
        dest = Path(args.to) / (f"{slug}.txt" if board == "main" else f"{slug}-{board}.txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(dest)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
