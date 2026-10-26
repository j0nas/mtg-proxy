"""Keep the fancy-art ranking to printings that show the card's own name.

The engine ranks a card's printings newest first and takes the first that passes its
showcase / extra-art preferences. Scryfall's list for a card also holds printings with
another name on them: Universes Beyond versions ("Rathalos, King of the Skies" for
Drakuseth, Maw of Flames; "Grimm Fate" for Beast Within) and Japanese-only Mystical
Archive cards. Picked for their frame, they print a card nobody at the table can read by
name, so by default they are left out of the ranking.

Installed by wrapping ``fetch_printings`` in the engine's scryfall module, which only the
ranking calls: a deck line with a set and collector number never reaches it, so pinning a
printing always wins. ``--alt-names`` lets every printing through. Prize and oversized
printings (the Legacy Championship duals) show the real name and stay in. When every
printing of a card would be dropped, the list passes through unchanged.
"""

from __future__ import annotations

from types import ModuleType


def shows_own_name(card: dict, foreign_ok: bool = False) -> bool:
    """True when the printing's face reads as the card it is.

    ``foreign_ok`` (set by ``--prefer_lang``) accepts non-English printings, whose
    ``printed_name`` is the translated name; a Universes Beyond name is never accepted.
    """
    faces = card.get("card_faces") or []
    if card.get("flavor_name") or any(f.get("flavor_name") for f in faces):
        return False
    if card.get("lang", "en") != "en":
        return foreign_ok
    printed = card.get("printed_name")
    return not printed or printed.casefold() == card.get("name", "").casefold()


def keep_own_names(printings: list[dict], foreign_ok: bool = False) -> list[dict]:
    kept = [c for c in printings if shows_own_name(c, foreign_ok)]
    return kept or printings  # a card printed only under another name still prints


def prefers_lang(fetch_args: list[str]) -> bool:
    """Whether the run passes the engine ``--prefer_lang`` (after ``--``)."""
    return any(a == "--prefer_lang" or a.startswith("--prefer_lang=") for a in fetch_args)


def install(scryfall: ModuleType, *, alt_names: bool = False, foreign_ok: bool = False) -> None:
    """Filter what ``scryfall.fetch_printings`` returns.

    Wraps once per process; a later install only changes the settings, so builds in one
    process don't stack wrappers or keep an earlier run's flags.
    """
    current = getattr(scryfall, "fetch_printings", None)
    if current is None:  # an engine (or test double) without the ranking has nothing to filter
        return
    if getattr(current, "_mtgproxy_wrapper", False):
        current._settings = (alt_names, foreign_ok)  # type: ignore[attr-defined]
        return
    real = current

    def fetch_printings(*args, **kwargs):
        printings = real(*args, **kwargs)
        allow_all, foreign = fetch_printings._settings  # type: ignore[attr-defined]
        return printings if allow_all else keep_own_names(printings, foreign)

    fetch_printings._mtgproxy_wrapper = True  # type: ignore[attr-defined]
    fetch_printings._settings = (alt_names, foreign_ok)  # type: ignore[attr-defined]
    scryfall.fetch_printings = fetch_printings
