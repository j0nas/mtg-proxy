from types import SimpleNamespace

from typer.testing import CliRunner

from mtgproxy import printings
from mtgproxy.build import BuildOptions
from mtgproxy.cli import app

# Trimmed Scryfall printings, newest first, as the engine's fetch_printings returns them.
RATHALOS = {
    "name": "Drakuseth, Maw of Flames",
    "set": "sld",
    "lang": "en",
    "flavor_name": "Rathalos, King of the Skies",
}
DRAKUSETH = {"name": "Drakuseth, Maw of Flames", "set": "fdc", "lang": "en"}
RIFT_JA = {"name": "Cyclonic Rift", "set": "soa", "lang": "ja", "printed_name": "サイクロンの裂け目"}
RIFT_EN = {"name": "Cyclonic Rift", "set": "soa", "lang": "en"}
BIRDS_ZETA = {"name": "Birds of Paradise", "set": "slz", "lang": "en", "printed_name": "BIRDS OF PARADISE"}
VOLCANIC_PRIZE = {"name": "Volcanic Island", "set": "olgc", "lang": "en", "oversized": True}
UB_FACE = {
    "name": "Front // Back",
    "lang": "en",
    "card_faces": [{"name": "Front"}, {"name": "Back", "flavor_name": "Something Else"}],
}


def test_alternate_names_and_foreign_printings_are_dropped():
    assert printings.shows_own_name(DRAKUSETH)
    assert not printings.shows_own_name(RATHALOS)
    assert not printings.shows_own_name(UB_FACE)  # a flavor name on either face counts
    assert not printings.shows_own_name(RIFT_JA)
    assert printings.shows_own_name(RIFT_EN)


def test_same_name_printings_stay_including_prize_cards_and_case_changes():
    assert printings.shows_own_name(VOLCANIC_PRIZE)  # Legacy Championship duals are wanted
    assert printings.shows_own_name(BIRDS_ZETA)


def test_prefer_lang_admits_translations_but_never_universes_beyond_names():
    assert printings.shows_own_name(RIFT_JA, foreign_ok=True)
    assert not printings.shows_own_name(RATHALOS, foreign_ok=True)


def test_keep_own_names_keeps_order_and_falls_back_when_nothing_is_left():
    assert printings.keep_own_names([RATHALOS, DRAKUSETH]) == [DRAKUSETH]
    assert printings.keep_own_names([RIFT_JA, RIFT_EN]) == [RIFT_EN]
    assert printings.keep_own_names([RATHALOS]) == [RATHALOS]  # only printed under another name


def test_prefers_lang_reads_engine_args():
    assert printings.prefers_lang(["--prefer_lang", "ja"])
    assert printings.prefers_lang(["--prefer_lang=ja"])
    assert not printings.prefers_lang(["--prefer_set", "sld"])
    assert not printings.prefers_lang([])


def test_install_filters_by_default_and_reinstall_only_changes_settings():
    calls = []

    def real(uri, prefer_ub, name):
        calls.append(name)
        return [RATHALOS, DRAKUSETH]

    mod = SimpleNamespace(fetch_printings=real)
    printings.install(mod)
    assert mod.fetch_printings("uri", None, "Drakuseth") == [DRAKUSETH]

    printings.install(mod, alt_names=True)  # no second wrapper: one real call per lookup
    assert mod.fetch_printings("uri", None, "Drakuseth") == [RATHALOS, DRAKUSETH]
    printings.install(mod)
    assert mod.fetch_printings("uri", None, "Drakuseth") == [DRAKUSETH]
    assert calls == ["Drakuseth"] * 3


def test_alt_names_is_recorded_for_rebuilds_and_reaches_the_cli(decklist, tmp_path):
    assert BuildOptions().recorded()["alt_names"] is False
    assert BuildOptions(alt_names=True).recorded()["alt_names"] is True
    runner = CliRunner()
    r = runner.invoke(app, ["make", str(decklist), "-o", str(tmp_path), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "alternate names allowed" not in r.output
    r = runner.invoke(app, ["make", str(decklist), "-o", str(tmp_path), "--dry-run", "--alt-names"])
    assert r.exit_code == 0, r.output
    assert "alternate names allowed" in r.output
