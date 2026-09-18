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
    p = _info(template="a4+y1mm.studio3", cut_offset_mm={"x": 0.0, "y": 1.0}).write(tmp_path)
    assert p.name == SIDECAR_NAME
    raw = json.loads(p.read_text())
    raw["some_future_field"] = 1
    p.write_text(json.dumps(raw))
    back = RunInfo.read(p)
    assert back.registration == "4" and back.cut_offset_mm == {"x": 0.0, "y": 1.0}


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
        duplex_dfc=True, dfc_count=2, template_name="letter-standard-v3+y1mm.studio3", template_baked=False,
        cut_offset_y_mm=1.0, today="2026-09-17",
    )  # fmt: skip
    text = notes.render(ctx)
    assert "# orah — print & cut checklist" in text
    assert (
        "registration: 3-mark | cards: 12 | sides: fronts only | double-faced cards: 2 (separate duplex PDF)"
        in text
    )
    assert "**orah-duplex.pdf** holds the 2 double-sided card image(s)" in text
    assert "cut-proxies -r 3 -p letter" in text
    assert "Cameo 5 (yes, plain 5" in text
    assert "pre-shifted **1mm down**" in text
    assert "3-mark pattern: cover the card nearest the bottom-left L mark." in text
    baked = notes.render(
        notes.NotesContext("d", "a4", "standard", "4", 8, False, True, 0, "t.studio3", True, 0.0)
    )
    assert "opens with **Cameo 5 Alpha** selected" in baked and "pre-shifted" not in baked
    assert "manual duplex, **long-edge flip**" in baked


def test_latest_notes(tmp_path):
    assert notes.latest_notes(tmp_path) is None
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / NOTES_NAME).write_text("old")
    old = tmp_path / "old" / NOTES_NAME
    os.utime(old, (time.time() - 100, time.time() - 100))
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / NOTES_NAME).write_text("new")
    assert notes.latest_notes(tmp_path) == tmp_path / "new" / NOTES_NAME


def test_mirror_is_flat_and_wipes_stale_templates(tmp_path):
    out = tmp_path / "deck"
    out.mkdir()
    for n in ("deck.pdf", "deck-duplex.pdf", "a4+y1mm.studio3", NOTES_NAME, SIDECAR_NAME, "deck.txt"):
        (out / n).write_text(n)
    win = tmp_path / "win"
    win.mkdir()
    (win / "stale-old.studio3").write_text("stale")
    (win / "other-deck.pdf").write_text("keep")
    r = mirror(out, "deck", win)
    assert r.failed == []
    assert sorted(p.name for p in win.iterdir()) == [
        "a4+y1mm.studio3",
        "deck-duplex.pdf",
        "deck.pdf",
        "deck.txt",
        "other-deck.pdf",
    ]
    assert to_windows_notation(tmp_path / "x") == str(tmp_path / "x").replace("/", "\\")
    assert to_windows_notation(type(tmp_path)("/mnt/c/Users/me/Desktop")) == "C:\\Users\\me\\Desktop"
