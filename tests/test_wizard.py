"""The wizard a bare `mtg-proxy` starts: its chain (make, print, cut), its prompts and its cancels.

Questions are scripted through monkeypatched questionary; nothing is printed, cut or fetched."""

import inspect
import os

import pytest
import typer
from typer.testing import CliRunner

from mtgproxy import build, cli, cutmenu, printing, wizard
from mtgproxy.sidecar import RunInfo

CANCEL = object()  # Ctrl-C at a question


class Asked(list):
    def messages(self):
        return [m for _, m, _ in self]


def script(monkeypatch, *answers) -> Asked:
    """Answer the wizard's questions in order; records (kind, message, titles). CANCEL is Ctrl-C."""
    asked, replies = Asked(), iter(answers)

    def question(kind):
        def make(message, choices=None, **kw):
            asked.append((kind, message, [c.title for c in choices or []]))

            class Q:
                def ask(self):
                    a = next(replies)
                    return None if a is CANCEL else a

                def unsafe_ask(self):
                    a = next(replies)
                    if a is CANCEL:
                        raise KeyboardInterrupt
                    return a

            return Q()

        return make

    for kind in ("select", "checkbox", "text", "confirm"):
        monkeypatch.setattr(wizard.questionary, kind, question(kind))
    monkeypatch.setattr(cli, "_at_terminal", lambda: True)
    return asked


def make_run(parent, name="mydeck", duplex=False):
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / f"{name}.pdf").write_bytes(b"%PDF")
    if duplex:
        (folder / f"{name}-duplex.pdf").write_bytes(b"%PDF")
    info = RunInfo(
        name,
        cards=9,
        fronts_only=not duplex,
        generated="2026-10-01",
        proof_targets=True,
        pdf=f"{name}.pdf",
        duplex_pdf=f"{name}-duplex.pdf" if duplex else None,
    )
    return info.write(folder)


@pytest.fixture
def printed(monkeypatch):
    sent = []
    monkeypatch.setattr(printing, "print_pdf", lambda pdf, kind="glossy", **kw: sent.append((pdf.name, kind)))
    return sent


@pytest.fixture
def cuts(monkeypatch):
    jobs = []

    def flow(**kw):
        jobs.append(kw)
        raise typer.Exit(0)

    monkeypatch.setattr(cli, "cut_flow", flow)
    return jobs


@pytest.fixture
def built(monkeypatch, proxies_root):
    """build.run_build stands in: writes a run folder, records the options it was given."""
    seen = []

    def run_build(opts):
        seen.append(opts)
        name = build.TEST_SHEET_NAME if opts.test_mode else "newdeck"
        sidecar = make_run(proxies_root, name, duplex=not opts.fronts_only)
        pdf = sidecar.parent / f"{name}.pdf"
        dup = sidecar.parent / f"{name}-duplex.pdf" if not opts.fronts_only else None
        return build.BuildResult(name, sidecar.parent, pdf, dup, 2 if dup else 0, 9)

    monkeypatch.setattr(build, "run_build", run_build)
    return seen


def start():
    wizard.run()


# --- chain -------------------------------------------------------------------------------------
def test_make_flows_into_print_and_cut_with_the_run_carried_along(
    monkeypatch, built, printed, cuts, proxies_root
):
    asked = script(
        monkeypatch,
        "make", ["basics", "plain"], "https://moxfield.com/decks/abc",  # make
        True,  # print now?
        True,  # confirm the queue
        True,  # cut now?
        "default",  # the cut menu (cut_flow is stubbed, so the job ends there)
        "quit",
    )  # fmt: skip
    start()
    opts = built[0]
    assert (opts.deck, opts.include_basics, opts.fancy_art, opts.fronts_only, opts.test_mode) == (
        "https://moxfield.com/decks/abc", True, False, True, False,
    )  # fmt: skip
    assert printed == [("newdeck.pdf", "glossy")]
    assert len(cuts) == 1
    assert cuts[0]["run"] == proxies_root / "newdeck" / "run.json" and cuts[0]["preset"] == "laminate"
    assert "Print it now?" in asked.messages() and "Cut it now?" in asked.messages()
    # the cut menu's first question offers this run, not another
    cut_q = next(t for k, m, t in asked if m == "Cut:")
    assert cut_q[0].startswith("Cut newdeck: 9 cards")


def test_skipping_print_still_offers_the_cut(monkeypatch, built, printed, cuts):
    script(monkeypatch, "make", [], "decks/x.txt", False, True, "default", "quit")
    start()
    assert printed == [] and len(cuts) == 1


def test_skipping_both_goes_back_to_the_menu(monkeypatch, built, printed, cuts):
    asked = script(monkeypatch, "make", [], "decks/x.txt", False, False, "quit")
    start()
    assert printed == [] and cuts == []
    assert asked.messages().count("mtg-proxy:") == 2


def test_declining_the_queue_sends_nothing_but_offers_the_cut(monkeypatch, built, printed, cuts):
    asked = script(monkeypatch, "make", [], "decks/x.txt", True, False, False, "quit")
    start()
    assert printed == [] and cuts == [] and "Cut it now?" in asked.messages()


def test_a_failed_print_is_not_retried_and_does_not_offer_the_cut(monkeypatch, built, cuts, capsys):
    calls = []

    def boom(pdf, kind="glossy", **kw):
        calls.append(pdf)
        raise printing.PrintError("cannot reach the PC")

    monkeypatch.setattr(printing, "print_pdf", boom)
    asked = script(monkeypatch, "make", [], "decks/x.txt", True, True, "quit")
    start()
    assert len(calls) == 1 and "Cut it now?" not in asked.messages()
    assert "cannot reach the PC" in capsys.readouterr().err


def test_a_deck_path_is_expanded_and_a_url_kept(monkeypatch, built, printed):
    script(monkeypatch, "make", [], "~/decks/x.txt", False, False, "quit")
    start()
    assert built[0].deck.endswith("/decks/x.txt") and not built[0].deck.startswith("~")


def test_the_test_sheet_asks_for_no_deck_and_prints_on_plain(monkeypatch, built, printed, cuts):
    asked = script(monkeypatch, "make", ["test"], True, True, False, "quit")
    start()
    assert built[0].test_mode and built[0].deck is None
    assert "text" not in [k for k, _, _ in asked]
    assert printed == [("test-sheet.pdf", "plain")]


def test_a_build_that_fails_says_so_and_returns_to_the_menu(monkeypatch, capsys):
    def boom(opts):
        raise build.BuildError("no decklist given")

    monkeypatch.setattr(build, "run_build", boom)
    asked = script(monkeypatch, "make", [], "decks/x.txt", "quit")
    start()
    assert "no decklist given" in capsys.readouterr().err
    assert asked.messages().count("mtg-proxy:") == 2


# --- print -------------------------------------------------------------------------------------
def test_print_picks_a_run_newest_first_and_sends_it_once_on_glossy(monkeypatch, proxies_root, printed, cuts):
    old = make_run(proxies_root, "old")
    new = make_run(proxies_root, "new")
    os.utime(old, (1, 1))
    asked = script(monkeypatch, "print", str(new), True, False, "quit")
    start()
    assert asked[1][2] == ["new: 9 cards", "old: 9 cards"]
    assert printed == [("new.pdf", "glossy")] and cuts == []
    assert "Print new.pdf on the glossy queue ('Glossy photo paper proxies')?" in asked.messages()


def test_the_duplex_choice_appears_only_when_the_run_has_one(
    monkeypatch, proxies_root, printed, cuts, capsys
):
    plain_run = make_run(proxies_root, "plainrun")
    asked = script(monkeypatch, "print", str(plain_run), True, False, "quit")
    start()
    assert "Which PDF?" not in asked.messages()

    two = make_run(proxies_root, "twosided", duplex=True)
    asked = script(
        monkeypatch, "print", str(two), str(two.parent / "twosided-duplex.pdf"), True, False, "quit"
    )
    start()
    assert asked[2][1] == "Which PDF?" and len(asked[2][2]) == 2
    assert printed[-1] == ("twosided-duplex.pdf", "glossy")
    assert "manual duplex, long-edge flip" in capsys.readouterr().out


def test_print_with_no_runs_says_so(monkeypatch, proxies_root, printed, capsys):
    script(monkeypatch, "print", "quit")
    start()
    assert printed == [] and "no runs in" in capsys.readouterr().out


# --- cut ---------------------------------------------------------------------------------------
def test_cut_from_the_menu_runs_the_chosen_job(monkeypatch, proxies_root, cuts):
    sidecar = make_run(proxies_root, "mydeck")
    script(monkeypatch, "cut", "choose", str(sidecar), "paper", "dry-run", "quit")
    start()
    assert len(cuts) == 1
    k = cuts[0]
    assert (k["run"], k["plain"], k["preset"], k["dry_run"], k["proof"], k["probe"]) == (
        sidecar, False, "paper", True, False, False,
    )  # fmt: skip


def test_quitting_the_cut_menu_cuts_nothing(monkeypatch, proxies_root, cuts):
    make_run(proxies_root, "mydeck")
    script(monkeypatch, "cut", "quit", "quit")
    start()
    assert cuts == []


def test_cut_flow_defaults_are_cuts():
    """The wizard calls cut_flow on its defaults, which must be the cut command's."""
    defaults = {n: p.default for n, p in inspect.signature(cli.cut_flow).parameters.items()}
    cmd = typer.main.get_command(cli.app).commands["cut"]
    for param in cmd.params:
        if param.name in ("scan", "allow_stale"):
            continue
        assert defaults[param.name] == param.default, param.name


# --- redo, backlog, notes ---------------------------------------------------------------------
def test_redo_collects_refs_until_an_empty_line(monkeypatch, proxies_root):
    sidecar = make_run(proxies_root, "mydeck")
    seen = []
    monkeypatch.setattr(cli, "queue_redo", lambda refs, run: seen.append((refs, run)))
    script(monkeypatch, "redo", str(sidecar), "p3", "Atraxa, Praetors' Voice", "", "quit")
    start()
    assert seen == [(["p3", "Atraxa, Praetors' Voice"], sidecar)]


def test_redo_with_nothing_typed_queues_nothing(monkeypatch, proxies_root):
    sidecar = make_run(proxies_root, "mydeck")
    monkeypatch.setattr(cli, "queue_redo", lambda *a: pytest.fail("queued"))
    script(monkeypatch, "redo", str(sidecar), "", "quit")
    start()


def test_the_backlog_sheet_asks_its_options_and_flows_into_print(monkeypatch, proxies_root, printed, cuts):
    seen = []

    def build_backlog(out, **kw):
        seen.append(kw)
        sidecar = make_run(proxies_root, "backlog-2026-11-10")
        pdf = sidecar.parent / "backlog-2026-11-10.pdf"
        return build.BuildResult("backlog-2026-11-10", sidecar.parent, pdf, None, 0, 9)

    monkeypatch.setattr(cli, "_show_backlog", lambda out: None)
    monkeypatch.setattr(cli, "build_backlog", build_backlog)
    script(monkeypatch, "backlog", ["full_only"], True, True, False, "quit")
    start()
    assert seen == [{"backs": False, "full_only": True}]
    assert printed == [("backlog-2026-11-10.pdf", "glossy")]


def test_an_empty_backlog_returns_to_the_menu(monkeypatch, proxies_root, capsys):
    asked = script(monkeypatch, "backlog", "quit")
    start()
    assert "backlog empty" in capsys.readouterr().out
    assert asked.messages().count("mtg-proxy:") == 2


def test_notes_shows_the_picked_runs(monkeypatch, proxies_root):
    sidecar = make_run(proxies_root, "mydeck")
    seen = []
    monkeypatch.setattr(cli, "_notes", lambda name, parent: seen.append((name, parent)))
    script(monkeypatch, "notes", str(sidecar), "quit")
    start()
    assert seen == [("mydeck", proxies_root)]


# --- cancelling --------------------------------------------------------------------------------
CANCEL_POINTS = {
    "top menu": [CANCEL],
    "make options": ["make", CANCEL],
    "make deck": ["make", [], CANCEL],
    "print now": ["make", [], "d.txt", CANCEL],
    "print run": ["print", CANCEL],
    "print confirm": ["print", "{new}", CANCEL],
    "cut now": ["make", [], "d.txt", False, CANCEL],
    "cut menu": ["cut", CANCEL],
    "redo run": ["redo", CANCEL],
    "redo refs": ["redo", "{new}", CANCEL],
    "backlog options": ["backlog", CANCEL],
    "notes run": ["notes", CANCEL],
}


@pytest.mark.parametrize("where", CANCEL_POINTS)
def test_cancelling_leaves_cleanly_and_does_nothing_more(
    where, monkeypatch, built, printed, cuts, proxies_root
):
    new = make_run(proxies_root, "existing")
    answers = [str(new) if a == "{new}" else a for a in CANCEL_POINTS[where]]
    monkeypatch.setattr(cli, "_show_backlog", lambda out: None)
    monkeypatch.setattr(cli, "build_backlog", lambda *a, **k: pytest.fail("built"))
    monkeypatch.setattr(cli, "queue_redo", lambda *a: pytest.fail("queued"))
    monkeypatch.setattr(cli, "_notes", lambda *a: pytest.fail("shown"))
    script(monkeypatch, *answers)  # no "quit" left: running past the cancel would raise StopIteration
    start()
    assert printed == [] and cuts == []


# --- entry point -------------------------------------------------------------------------------
def test_bare_at_a_terminal_starts_the_wizard(monkeypatch):
    started = []
    monkeypatch.setattr(cli, "_at_terminal", lambda: True)
    monkeypatch.setattr(wizard, "run", lambda: started.append(True))
    result = CliRunner().invoke(cli.app, [])
    assert result.exit_code == 0 and started == [True]


def test_bare_off_a_terminal_prints_help(monkeypatch):
    monkeypatch.setattr(wizard, "run", lambda: pytest.fail("wizard off a terminal"))
    result = CliRunner().invoke(cli.app, [])
    assert result.exit_code == 2 and "Usage:" in result.output and "make" in result.output


def test_a_subcommand_does_not_start_the_wizard(monkeypatch):
    monkeypatch.setattr(cli, "_at_terminal", lambda: True)
    monkeypatch.setattr(wizard, "run", lambda: pytest.fail("wizard with a subcommand"))
    assert CliRunner().invoke(cli.app, ["doctor"]).exit_code in (0, 1)


def test_the_wizard_checks_freshness_once_at_the_start(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_fresh", lambda allow: calls.append(allow))
    script(monkeypatch, "quit")
    start()
    assert calls == [False]


def test_the_build_summary_points_at_mtg_proxy(proxies_root, capsys):
    r = build.BuildResult("mydeck", proxies_root / "mydeck", None, None, 0, 9)
    build.print_summary(r, build.BuildOptions())
    out = capsys.readouterr().out
    assert "mtg-proxy notes mydeck" in out and "cut-proxies" not in out and "make-proxies" not in out


def test_cutmenu_defaults_to_a_run_it_is_given_even_outside_the_folder(monkeypatch, tmp_path):
    sidecar = make_run(tmp_path / "elsewhere", "far")
    asked = script(monkeypatch, "quit")
    assert cutmenu.ask(tmp_path / "nothing", "Bluetooth", sidecar) is None
    assert asked[0][2][0].startswith("Cut far: 9 cards")
