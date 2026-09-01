#!/usr/bin/env python3
"""Fetch a Moxfield deck (or one of its boards) as an MTGA-format decklist.

Moxfield has no export or copy path for the "Considering" board (internally:
maybeboard), so this pulls it straight from their JSON API
(https://api2.moxfield.com/v3/decks/all/<public-id> — unofficial but the same
endpoint the site itself uses; it wants a browser-ish User-Agent).

Every emitted line carries the exact printing (set + collector number) chosen
on Moxfield; fetch.py honors those over its --prefer_* art flags.

Usage:
  fetch_moxfield.py <url-or-public-id> [--board main|side|considering] [--to DIR]

Prints the decklist to stdout. With --to DIR it instead writes
DIR/<deck>-<board>.txt (plain DIR/<deck>.txt for the main board) and prints
the file path — the mode make-proxies.sh uses when handed a moxfield.com URL.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api2.moxfield.com/v3/decks/all/{}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# UI name -> Moxfield board keys. "main" folds in the commander/companion
# boards so a Commander deck comes out whole.
BOARDS = {
    "main": ["commanders", "companions", "mainboard"],
    "side": ["sideboard"],
    "considering": ["maybeboard"],
}
ALIASES = {"mainboard": "main", "sideboard": "side", "maybe": "considering",
           "maybeboard": "considering"}


def die(msg: str) -> None:
    sys.exit(f"fetch_moxfield: {msg}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("deck", help="moxfield.com/decks/... URL or bare public deck id")
    p.add_argument("--board", default="main", help="main | side | considering")
    p.add_argument("--to", metavar="DIR", help="write DIR/<deck>[-<board>].txt and print its path")
    args = p.parse_args()

    m = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", args.deck)
    public_id = m.group(1) if m else args.deck
    if not re.fullmatch(r"[A-Za-z0-9_-]+", public_id):
        die(f"can't find a deck id in {args.deck!r}")

    board = ALIASES.get(args.board.lower(), args.board.lower())
    if board not in BOARDS:
        die(f"unknown board {args.board!r} (use: {', '.join(BOARDS)})")

    # urllib, not requests: Moxfield's WAF 403s the requests fingerprint but
    # accepts a plain urllib client with a browser User-Agent (2026-08).
    req = urllib.request.Request(API.format(public_id),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            deck = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            die(f"deck {public_id!r} not found — private decks are not visible to the API")
        die(f"Moxfield API returned HTTP {e.code}")

    entries = []
    for key in BOARDS[board]:
        for v in deck.get("boards", {}).get(key, {}).get("cards", {}).values():
            c = v.get("card", {})
            qty, name = v.get("quantity", 1), c.get("name", "")
            set_code, cn = c.get("set", ""), c.get("cn", "")
            if not name:
                continue
            entries.append(f"{qty} {name} ({set_code.upper()}) {cn}" if set_code and cn
                           else f"{qty} {name}")
    if not entries:
        nonempty = [k for k, b in deck.get("boards", {}).items() if b.get("count")]
        die(f"board {board!r} of {deck.get('name')!r} is empty (non-empty boards: {', '.join(nonempty)})")
    entries.sort(key=str.lower)
    text = "\n".join(entries) + "\n"

    print(f"{deck.get('name')!r} [{board}]: {len(entries)} entries", file=sys.stderr)
    if args.to:
        slug = re.sub(r"[^a-z0-9]+", "-", deck.get("name", "").lower()).strip("-") or public_id
        dest = Path(args.to) / (f"{slug}.txt" if board == "main" else f"{slug}-{board}.txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(dest)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
