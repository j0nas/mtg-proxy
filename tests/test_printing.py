from __future__ import annotations

from pathlib import Path

from mtgproxy.printing import DEFAULT_INSTANCE, build_job, find_instance, instance_defined


def test_instance_defined(tmp_path):
    lp = tmp_path / "lpoptions"
    assert not instance_defined(DEFAULT_INSTANCE, lp)
    lp.write_text(f"Default EPSON\nDest {DEFAULT_INSTANCE} media=A4\n")
    assert instance_defined(DEFAULT_INSTANCE, lp)
    assert not instance_defined("EPSON/other", lp)


def test_real_sheet_uses_instance_when_defined(tmp_path, monkeypatch):
    monkeypatch.delenv("MTG_PROXY_LP_OPTS", raising=False)
    monkeypatch.delenv("MTG_PROXY_LP_INSTANCE", raising=False)
    lp = tmp_path / "lpoptions"
    lp.write_text(f"Dest {DEFAULT_INSTANCE} x=y\n")
    job = build_job(Path("/x/deck.pdf"), "a4", test_sheet=False, lpoptions=lp, exists=lambda q: True)
    assert job.argv == [
        "lp", "-d", DEFAULT_INSTANCE,
        "-o", "media=A4", "-o", "print-scaling=none", "-o", "fit-to-page=false", "/x/deck.pdf",
    ]  # fmt: skip
    assert "4x2-glossy" in job.description


def test_real_sheet_falls_back_to_explicit_glossy_options(tmp_path, monkeypatch):
    monkeypatch.setenv("MTG_PROXY_LP_OPTS", "-o foo=bar")
    job = build_job(
        Path("d.pdf"), "letter", test_sheet=False, lpoptions=tmp_path / "none", exists=lambda q: True
    )
    assert "-d" not in job.argv
    assert "MediaType=photographic-glossy" in job.argv and "media=Letter" in job.argv
    assert job.argv[-3:] == ["-o", "foo=bar", "d.pdf"]
    named = build_job(
        Path("d.pdf"),
        "a4",
        test_sheet=False,
        printer="Other",
        lpoptions=tmp_path / "none",
        exists=lambda q: True,
    )
    assert named.argv[1:3] == ["-d", "Other"] and "InputSlot=rear" in named.argv


def test_test_sheet_uses_plain_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("MTG_PROXY_LP_OPTS", raising=False)
    job = build_job(Path("t.pdf"), "a4", test_sheet=True, lpoptions=tmp_path / "none")
    assert "MediaType=photographic-glossy" not in job.argv and "-d" not in job.argv
    assert "plain paper" in job.description


def test_instance_follows_a_renamed_queue_and_ignores_dangling_ones(tmp_path):
    lp = tmp_path / "lpoptions"
    lp.write_text(
        f"Default X\nDest {DEFAULT_INSTANCE} media=A4\nDest EPSON_ET_8550_Series_2/4x2-glossy EPIJ_Medi=145\n"
    )
    live = {"EPSON_ET_8550_Series_2"}
    assert (
        find_instance(DEFAULT_INSTANCE, lp, exists=lambda q: q in live) == "EPSON_ET_8550_Series_2/4x2-glossy"
    )
    assert (
        find_instance(DEFAULT_INSTANCE, lp, exists=lambda q: True) == DEFAULT_INSTANCE
    )  # preferred wins when alive
    assert find_instance(DEFAULT_INSTANCE, lp, exists=lambda q: False) is None
    job = build_job(Path("d.pdf"), "a4", test_sheet=False, lpoptions=lp, exists=lambda q: False)
    assert (
        "-d" not in job.argv and "explicit glossy options" in job.description
    )  # dangling instance never used
