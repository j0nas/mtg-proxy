"""Which card went where: the per-slot manifest written into run.json.

The engine names images ``<index><CleanName><copy>.png`` and lays them out in
natural sort order, N per page, so page and slot of every card follow from the
file list alone. What the file list does not carry is the *printing* (set and
collector number) — the deck line may have had none, or the engine may have
picked a showcase frame. ``Recorder`` hooks the engine's ``fetch_card_art`` to
capture that as the images are saved, and persists it in ``game/.manifest.json``
so a ``--skip-fetch`` run still knows.

``mtg-proxy redo`` resolves references (``last``, ``p3.5``, a card name) against
these sheets; older run.json files without them are rebuilt by
:func:`rebuild` from the decklist, through the image cache.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType

from natsort import natsorted

from . import engine
from .trim import clean_name

STATE_FILE = engine.GAME / ".manifest.json"
IMAGE_FILE = re.compile(r"^(\d+)(.+?)(\d+)(-back)?\.\w+$")
MAIN, DUPLEX = "main", "duplex"


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Printing:
    name: str
    set: str = ""
    cn: str = ""
    token: bool = False


@dataclass
class SlotCard:
    file: str
    name: str
    set: str = ""
    cn: str = ""
    token: bool = False
    dfc: bool = False

    @property
    def line(self) -> str:
        return f"{self.name} ({self.set.upper()}) {self.cn}" if self.set and self.cn else self.name


@dataclass
class Sheet:
    key: str  # MAIN | DUPLEX
    pdf: str | None
    per_page: int
    cards: list[SlotCard] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return -(-len(self.cards) // self.per_page) if self.cards else 0

    def page(self, n: int) -> list[SlotCard]:
        if not 1 <= n <= self.pages:
            raise ManifestError(f"{self.key} sheet has {self.pages} page(s), no page {n}")
        return self.cards[(n - 1) * self.per_page : n * self.per_page]

    def slot(self, page: int, slot: int) -> SlotCard:
        cards = self.page(page)
        if not 1 <= slot <= len(cards):
            raise ManifestError(f"{self.key} page {page} has {len(cards)} card(s), no slot {slot}")
        return cards[slot - 1]

    def position(self, i: int) -> tuple[int, int]:
        return i // self.per_page + 1, i % self.per_page + 1

    def as_dict(self) -> dict:
        return {"pdf": self.pdf, "per_page": self.per_page, "cards": [asdict(c) for c in self.cards]}

    @classmethod
    def from_dict(cls, key: str, raw: dict) -> Sheet:
        return cls(key, raw.get("pdf"), int(raw["per_page"]), [SlotCard(**c) for c in raw.get("cards", [])])


def sheets_from_dict(raw: dict | None) -> dict[str, Sheet]:
    return {k: Sheet.from_dict(k, v) for k, v in (raw or {}).items()}


def cards_per_page(paper: str, card_size: str) -> int:
    layout = engine.layouts()["layouts"][paper][card_size]["default"]
    return int(layout["num_rows"]) * int(layout["num_cols"])


# --- recording printings while the engine fetches --------------------------
class Recorder:
    """Wraps ``scryfall.fetch_card_art`` and remembers (index, clean name) → printing."""

    def __init__(self) -> None:
        self.seen: dict[str, Printing] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(index: int | str, clean: str) -> str:
        return f"{index}:{clean}"

    def record(self, index: int, clean: str, name: str, set_code: str, cn: str) -> None:
        with self._lock:
            self.seen[self.key(index, clean)] = Printing(
                name, str(set_code).lower(), str(cn), clean.endswith("_token")
            )

    def install(self, scryfall: ModuleType) -> None:
        current = scryfall.fetch_card_art
        if getattr(current, "_mtgproxy_recorder", False):
            current._recorder = self  # type: ignore[attr-defined]
            return
        real = current
        sig = inspect.signature(real)

        def recording(*args, **kwargs):
            rec: Recorder = recording._recorder  # type: ignore[attr-defined]
            try:
                b = sig.bind_partial(*args, **kwargs).arguments
                card_json = b.get("card_json") or {}
                name = b.get("card_name") or card_json.get("name") or b["clean_card_name"]
                rec.record(b["index"], b["clean_card_name"], name, b["card_set"], b["card_collector_number"])
            except (KeyError, TypeError):
                pass
            return real(*args, **kwargs)

        recording._mtgproxy_recorder = True  # type: ignore[attr-defined]
        recording._recorder = self  # type: ignore[attr-defined]
        scryfall.fetch_card_art = recording

    def save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({k: asdict(v) for k, v in self.seen.items()}, indent=1))

    @classmethod
    def load(cls) -> Recorder:
        r = cls()
        try:
            for k, v in json.loads(STATE_FILE.read_text()).items():
                r.seen[k] = Printing(**v)
        except (OSError, ValueError, TypeError):
            pass
        return r

    def lookup(self, file: Path) -> SlotCard:
        m = IMAGE_FILE.match(file.name)
        if not m:
            return SlotCard(file.name, file.stem)
        index, clean = m.group(1), m.group(2)
        p = self.seen.get(self.key(index, clean))
        if p is None:
            # Tokens are re-indexed after fetching (build.move_tokens_last); match by name.
            p = next((v for k, v in sorted(self.seen.items()) if k.split(":", 1)[1] == clean), None)
        if p is None:
            # Nothing recorded (state lost): the clean name is all we have.
            return SlotCard(file.name, clean.removesuffix("_token"), token=clean.endswith("_token"))
        return SlotCard(file.name, p.name, p.set, p.cn, p.token)


# --- sheets --------------------------------------------------------------------
def partition(fronts_only: bool, duplex_dfc: bool) -> tuple[list[Path], list[Path]]:
    """(main files, duplex files) in the order the engine lays them out.

    Fronts-only with duplex DFCs: double-faced cards go to their own sheet. Every
    other mode is one sheet — the engine puts single-sided cards first, then
    double-sided ones, each group in natural order.
    """
    fronts = engine.images_in(engine.FRONT)
    is_dfc = {f: (engine.DOUBLE_SIDED / f.name).is_file() for f in fronts}
    if fronts_only and duplex_dfc:
        return _nat([f for f in fronts if not is_dfc[f]]), _nat([f for f in fronts if is_dfc[f]])
    if fronts_only:
        # --split-faces: each back face is its own plain card, named <front>-back next to its
        # front (a virtual path; see view_target).
        backs = [f.with_name(f"{f.stem}-back{f.suffix}") for f in fronts if is_dfc[f]]
        return _nat(fronts + backs), []
    return _nat([f for f in fronts if not is_dfc[f]]) + _nat([f for f in fronts if is_dfc[f]]), []


def _nat(files: list[Path]) -> list[Path]:
    return natsorted(files, key=lambda p: p.name)


def view_target(f: Path) -> Path:
    """The real image behind a partition entry (split-face backs live in game/double_sided)."""
    if f.stem.endswith("-back") and not f.exists():
        return engine.DOUBLE_SIDED / f.name.replace("-back", "", 1)
    return f


def make_sheet(key: str, files: list[Path], pdf: str | None, per_page: int, rec: Recorder) -> Sheet:
    cards = []
    for f in files:
        c = rec.lookup(f)
        c.dfc = (engine.DOUBLE_SIDED / f.name).is_file()
        cards.append(c)
    return Sheet(key, pdf, per_page, cards)


# --- references ------------------------------------------------------------------
REF = re.compile(r"^(?P<sheet>[pd])(?P<page>\d+)(?:[.:](?P<slot>\d+))?$", re.IGNORECASE)


def resolve(ref: str, sheets: dict[str, Sheet]) -> list[SlotCard]:
    """``last`` | ``dlast`` | ``p3`` | ``p3.5`` | ``d1`` | ``d1.2`` | a card name (unique match)."""
    r = ref.strip()
    low = r.lower()
    if low in ("last", "dlast"):
        sheet = _sheet(sheets, MAIN if low == "last" else DUPLEX, r)
        if not sheet.pages:
            raise ManifestError(f"{sheet.key} sheet is empty — nothing for {r!r}")
        return list(sheet.page(sheet.pages))
    if m := REF.match(r):
        sheet = _sheet(sheets, MAIN if m["sheet"].lower() == "p" else DUPLEX, r)
        if m["slot"] is None:
            return list(sheet.page(int(m["page"])))
        return [sheet.slot(int(m["page"]), int(m["slot"]))]
    return [_by_name(r, sheets)]


def _sheet(sheets: dict[str, Sheet], key: str, ref: str) -> Sheet:
    if key not in sheets:
        raise ManifestError(f"this run has no {key} sheet ({ref!r})")
    return sheets[key]


def _by_name(ref: str, sheets: dict[str, Sheet]) -> SlotCard:
    want = clean_name(ref)
    if not want:
        raise ManifestError(f"empty reference {ref!r}")
    cards = [c for s in sheets.values() for c in s.cards]
    for match in (
        lambda c: clean_name(c.name) == want,
        lambda c: clean_name(c.name).startswith(want),
        lambda c: want in clean_name(c.name),
    ):
        hits = [c for c in cards if match(c)]
        names = sorted({c.name for c in hits})
        if len(names) == 1:
            return hits[0]
        if len(names) > 1:
            raise ManifestError(f"{ref!r} is ambiguous: " + ", ".join(names))
    raise ManifestError(f"no card matching {ref!r} in this run")


def locate(card: SlotCard, sheets: dict[str, Sheet]) -> str:
    """'p3.5' / 'd1.2' for a card in these sheets."""
    for s in sheets.values():
        for i, c in enumerate(s.cards):
            if c is card:
                page, slot = s.position(i)
                return f"{'p' if s.key == MAIN else 'd'}{page}.{slot}"
    return "?"


# --- older runs --------------------------------------------------------------------
def rebuild(run_dir: Path, info) -> dict[str, Sheet]:
    """Re-derive the sheets of a run made before manifests existed: re-run the
    fetch stage from the run's decklist (image cache makes it seconds) and read
    the resulting file order. Nothing is printed or overwritten but run.json."""
    from . import build  # late import: build imports this module

    if not info.decklist:
        raise ManifestError("run.json names no decklist — nothing to rebuild the manifest from")
    deck = Path(info.decklist)
    if not deck.is_absolute():
        deck = run_dir / deck
    if not deck.is_file():
        raise ManifestError(f"decklist not found: {deck}")
    o = info.options or {}
    opts = build.BuildOptions(
        deck=str(deck),
        fmt=o.get("fmt", "mtga"),
        paper=info.paper,
        card_size=info.card_size,
        fronts_only=info.fronts_only,
        duplex_dfc=o.get("duplex_dfc", True),
        fancy_art=o.get("fancy_art", True),
        include_basics=o.get("include_basics", False),
        token_copies=o.get("token_copies", 0),
        tokens_only=o.get("tokens_only", False),
        fetch_args=list(o.get("fetch_args", [])),
    )
    rec = build.stage_and_index(opts)
    main, duplex = partition(opts.fronts_only, opts.duplex_dfc)
    total = len(main) + len(duplex)
    expected = info.cards + info.deferred
    if total != expected:
        raise ManifestError(
            f"rebuilt {total} card(s) but the run had {expected} — the decklist or the build "
            "options differ from the original run; pass the -t N / --basics it was built with"
        )
    per_page = cards_per_page(info.paper, info.card_size)
    sheets = {MAIN: make_sheet(MAIN, main, info.pdf, per_page, rec)}
    if duplex:
        sheets[DUPLEX] = make_sheet(DUPLEX, duplex, info.duplex_pdf, per_page, rec)
    return sheets
