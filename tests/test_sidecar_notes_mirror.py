from __future__ import annotations

import json
import os
import time

from mtgproxy import notes
from mtgproxy.mirror import mirror, to_windows_notation
from mtgproxy.paths import NOTES_NAME, SIDECAR_NAME
from mtgproxy.sidecar import RunInfo, find_sidecar


def _info(**kw) -> RunInfo:
    base = dict(
        name="deck",
        paper="a4",
        card_size="standard",
        registration="4",
        cards=8,
        fronts_only=True,
        generated="2026-09-17",
    )
    base.update(kw)
    return RunInfo(**base)


def test_sidecar_roundtrip_and_forward_compat(tmp_path):
    p = _info(trims={"Temple Garden": 0.3}).write(tmp_path)
    assert p.name == SIDECAR_NAME
    raw = json.loads(p.read_text())
    raw["some_future_field"] = 1
    raw["template"] = "a4+y1mm.studio3"  # older runs: Studio template fields, now ignored
    p.write_text(json.dumps(raw))
    back = RunInfo.read(p)
    assert back.registration == "4" and back.trims == {"Temple Garden": 0.3}


def test_find_sidecar(tmp_path, monkeypatch):
    assert find_sidecar(tmp_path) is None
    p = _info().write(tmp_path)
    assert find_sidecar(tmp_path) == p
    assert find_sidecar(p) == p
    monkeypatch.chdir(tmp_path)
    assert find_sidecar(None) == p


def test_notes_render_covers_the_run():
    ctx = notes.NotesContext(
        name="orah", paper="letter", card_size="standard", registration="3", cards=12, fronts_only=True,
        duplex_dfc=True, dfc_count=2, today="2026-09-17",
    )  # fmt: skip
    text = notes.render(ctx)
    assert "# orah — print & cut checklist" in text
    assert (
        "registration: 3-mark | cards: 12 | sides: fronts only | double-faced cards: 2 (separate duplex PDF)"
        in text
    )
    assert "**orah-duplex.pdf** holds the 2 double-sided card image(s)" in text
    assert "cut-proxies -r 3 -p letter" in text
    assert "**Force 20 · Speed 25 · Depth 4 · Passes 3**" in text
    assert "`--preset paper` (Force 10 · Speed 5 · Depth 1 · Passes 1)" in text
    assert "Studio" not in text and "studio3" not in text
    duplex = notes.render(notes.NotesContext("d", "a4", "standard", "4", 8, False, True, 0))
    assert "manual duplex, **long-edge flip**" in duplex
    assert "`cut-proxies -r 4`" in duplex


def test_latest_notes(tmp_path):
    assert notes.latest_notes(tmp_path) is None
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / NOTES_NAME).write_text("old")
    old = tmp_path / "old" / NOTES_NAME
    os.utime(old, (time.time() - 100, time.time() - 100))
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / NOTES_NAME).write_text("new")
    assert notes.latest_notes(tmp_path) == tmp_path / "new" / NOTES_NAME


def test_mirror_is_flat_and_keeps_other_decks(tmp_path):
    out = tmp_path / "deck"
    out.mkdir()
    for n in ("deck.pdf", "deck-duplex.pdf", NOTES_NAME, SIDECAR_NAME, "deck.txt"):
        (out / n).write_text(n)
    win = tmp_path / "win"
    win.mkdir()
    (win / "other-deck.pdf").write_text("keep")
    r = mirror(out, "deck", win)
    assert r.failed == []
    assert sorted(p.name for p in win.iterdir()) == [
        "deck-duplex.pdf",
        "deck.pdf",
        "deck.txt",
        "other-deck.pdf",
    ]
    assert to_windows_notation(tmp_path / "x") == str(tmp_path / "x").replace("/", "\\")
    assert to_windows_notation(type(tmp_path)("/mnt/c/Users/me/Desktop")) == "C:\\Users\\me\\Desktop"


def test_mirror_reports_a_locked_stale_file_instead_of_crashing(tmp_path, monkeypatch):
    from pathlib import Path

    from mtgproxy import mirror

    out, win = tmp_path / "run", tmp_path / "win"
    out.mkdir(), win.mkdir()
    (out / "deck.pdf").write_bytes(b"new")
    (out / "deck.txt").write_bytes(b"t")
    (win / "deck.pdf").write_bytes(b"old, open in a viewer")
    real_unlink = Path.unlink

    def locked_unlink(self, missing_ok=False):
        if self.name == "deck.pdf":
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    r = mirror.mirror(out, "deck", win)
    assert r.failed == ["deck.pdf"]
    assert (win / "deck.pdf").read_bytes() == b"old, open in a viewer"  # never half-overwritten
    assert (win / "deck.txt").is_file()
