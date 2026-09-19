"""BACKLOG.txt: cards still owed, across decks, until they fill a sheet.

Lives next to the deck folders (the directory ``make`` runs in). Fed by
``make --defer-partial`` (the last, partial page of a run) and ``mtg-proxy redo``
(miscuts, bad laminations); drained by ``mtg-proxy backlog build``, which turns
it into an ordinary run folder.

Plain MTGA lines, one per physical card, with the provenance in a trailing comment:

    1 Sol Ring (CMM) 464  # deck=selesnyan ref=p3.5 date=2026-09-19 trim=0.3 dfc

Edit it by hand if you like; a bare ``1 Card Name`` line resolves to current art.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from .manifest import SlotCard

FILE_NAME = "BACKLOG.txt"
LINE = re.compile(r"^(?P<qty>\d+)x?\s+(?P<name>.+?)\s+\((?P<set>\w+)\)\s+(?P<cn>\S+)$")
BARE = re.compile(r"^(?P<qty>\d+)x?\s+(?P<name>.+?)$")


class BacklogError(Exception):
    pass


@dataclass
class Entry:
    name: str
    set: str = ""
    cn: str = ""
    qty: int = 1
    deck: str = ""
    ref: str = ""
    date: str = ""
    trim: float | None = None
    dfc: bool = False
    token: bool = False

    @property
    def card(self) -> str:
        return f"{self.name} ({self.set.upper()}) {self.cn}" if self.set and self.cn else self.name

    @property
    def meta(self) -> str:
        parts = []
        if self.deck:
            parts.append(f"deck={self.deck}")
        if self.ref:
            parts.append(f"ref={self.ref}")
        if self.date:
            parts.append(f"date={self.date}")
        if self.trim is not None:
            parts.append(f"trim={self.trim:g}")
        if self.dfc:
            parts.append("dfc")
        if self.token:
            parts.append("token")
        return " ".join(parts)

    def format(self) -> str:
        line = f"{self.qty} {self.card}"
        return f"{line}  # {self.meta}" if self.meta else line

    @classmethod
    def parse(cls, text: str) -> Entry | None:
        body, _, comment = text.partition("#")
        body = body.strip()
        if not body or body.startswith("//"):
            return None
        m = LINE.match(body) or BARE.match(body)
        if not m:
            raise BacklogError(f"unreadable backlog line: {text.strip()!r}")
        e = cls(
            m["name"].strip(), m.groupdict().get("set") or "", m.groupdict().get("cn") or "", int(m["qty"])
        )
        e.set = e.set.lower()
        for tok in comment.split():
            key, _, val = tok.partition("=")
            if key == "deck":
                e.deck = val
            elif key == "ref":
                e.ref = val
            elif key == "date":
                e.date = val
            elif key == "trim":
                with contextlib.suppress(ValueError):
                    e.trim = float(val)
            elif key == "dfc":
                e.dfc = True
            elif key == "token":
                e.token = True
        return e

    @classmethod
    def from_slot(cls, c: SlotCard, deck: str, ref: str, trim: float | None) -> Entry:
        return cls(c.name, c.set, c.cn, 1, deck, ref, date.today().isoformat(), trim, c.dfc, c.token)

    def units(self) -> list[Entry]:
        return [replace(self, qty=1) for _ in range(self.qty)]


@dataclass
class Backlog:
    path: Path
    entries: list[Entry] = field(default_factory=list)

    @classmethod
    def at(cls, parent: Path) -> Backlog:
        return cls.load(parent / FILE_NAME)

    @classmethod
    def load(cls, path: Path) -> Backlog:
        b = cls(path)
        if path.is_file():
            for line in path.read_text().splitlines():
                if (e := Entry.parse(line)) is not None:
                    b.entries.append(e)
        return b

    def save(self) -> None:
        if not self.entries:
            self.path.unlink(missing_ok=True)
            return
        header = "# mtg-proxy backlog — cards still to print. `mtg-proxy backlog` to see, `backlog build` to print.\n"
        self.path.write_text(header + "".join(e.format() + "\n" for e in self.entries))

    def add(self, new: list[Entry]) -> None:
        self.entries.extend(new)

    @property
    def cards(self) -> list[Entry]:
        return [u for e in self.entries for u in e.units()]

    def split(self) -> tuple[list[Entry], list[Entry]]:
        """(single-sided, double-faced) physical cards."""
        cards = self.cards
        return [c for c in cards if not c.dfc], [c for c in cards if c.dfc]

    def take(self, per_page: int, full_only: bool) -> tuple[list[Entry], list[Entry]]:
        """(cards to build now, cards that stay queued). ``full_only`` keeps each
        sheet's partial last page in the queue."""
        build: list[Entry] = []
        keep: list[Entry] = []
        for group in self.split():
            n = len(group) // per_page * per_page if full_only else len(group)
            build += group[:n]
            keep += group[n:]
        return build, keep

    def drop(self, ref: str) -> list[Entry]:
        """Remove by 1-based line number (as listed) or by unique card-name match."""
        if ref.isdigit():
            i = int(ref) - 1
            if not 0 <= i < len(self.entries):
                raise BacklogError(f"no backlog entry #{ref} (have {len(self.entries)})")
            return [self.entries.pop(i)]
        low = ref.lower()
        hits = [e for e in self.entries if low in e.name.lower()]
        names = sorted({e.name for e in hits})
        if not hits:
            raise BacklogError(f"nothing in the backlog matching {ref!r}")
        if len(names) > 1:
            raise BacklogError(f"{ref!r} is ambiguous: " + ", ".join(names))
        self.entries = [e for e in self.entries if e not in hits]
        return hits

    def summary(self, per_page: int) -> str:
        single, dfc = self.split()
        lines = [f"{len(self.cards)} card(s) in {self.path}"]
        for label, group in (("fronts", single), ("duplex", dfc)):
            if not group and label == "duplex":
                continue
            full, rest = divmod(len(group), per_page)
            lines.append(
                f"  {label}: {len(group)} → {full} full sheet(s) of {per_page}"
                + (f" + {rest} waiting" if rest else "")
            )
        return "\n".join(lines)

    def listing(self) -> str:
        w = max((len(e.card) for e in self.entries), default=0)
        rows = []
        for i, e in enumerate(self.entries, 1):
            src = " ".join(x for x in (e.deck, e.ref, e.date) if x)
            flags = " ".join(
                x for x in ("dfc" if e.dfc else "", f"trim {e.trim:g}" if e.trim is not None else "") if x
            )
            rows.append(f"  {i:3d}  {e.qty} {e.card:<{w}}  {src}" + (f"  [{flags}]" if flags else ""))
        return "\n".join(rows)


def decklist_text(cards: list[Entry]) -> str:
    """The clean MTGA decklist the engine builds from (no comments, one line per card)."""
    return "".join(f"1 {c.card}\n" for c in cards)
