"""The checkouts compared with GitHub before a build or a cut (freshness.py), against a real clone of
a local repository standing in for GitHub; and the marks a sheet printed compared with the layout."""

import subprocess

import pytest

from mtgproxy import cutting, freshness, layout


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def commit(path, name):
    (path / name).write_text(name)
    git(path, "add", name)
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)


@pytest.fixture
def hub_and_clone(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    git(hub, "init", "-q", "-b", "main")
    commit(hub, "a")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(hub), str(clone)], check=True)
    return hub, freshness.Repo("mtg-proxy", clone, "main")


def test_a_checkout_level_with_github_runs(hub_and_clone):
    _, repo = hub_and_clone
    s = freshness.state(repo)
    assert (s.behind, s.ahead, s.problem) == (0, 0, None) and s.head


def test_a_checkout_behind_github_stops_the_run_and_says_what_to_pull(hub_and_clone, monkeypatch):
    hub, repo = hub_and_clone
    commit(hub, "b")
    commit(hub, "c")
    monkeypatch.delenv(freshness.SKIP_ENV)
    assert freshness.state(repo).behind == 2
    with pytest.raises(
        freshness.StaleError,
        match=rf"mtg-proxy here \(\w+\) is 2 commit\(s\) behind GitHub's main: git -C {repo.path} pull",
    ):
        freshness.ensure_fresh(lambda m: None, repos=(repo,))
    said = []
    freshness.ensure_fresh(said.append, allow_stale=True, repos=(repo,))
    assert said[0].startswith("WARNING: running a stale checkout")


def test_commits_not_on_github_only_say_so(hub_and_clone, monkeypatch):
    _, repo = hub_and_clone
    commit(repo.path, "local")
    monkeypatch.delenv(freshness.SKIP_ENV)
    said = []
    freshness.ensure_fresh(said.append, repos=(repo,))
    assert "has 1 commit(s) GitHub's main doesn't" in said[0]


def test_github_out_of_reach_says_so_and_runs(hub_and_clone, monkeypatch):
    hub, repo = hub_and_clone
    git(repo.path, "remote", "set-url", "origin", str(hub.parent / "nowhere"))
    monkeypatch.delenv(freshness.SKIP_ENV)
    said = []
    freshness.ensure_fresh(said.append, repos=(repo,))
    assert "couldn't fetch from GitHub" in said[0]


def test_a_folder_that_isnt_a_checkout_is_reported_not_refused(tmp_path, monkeypatch):
    monkeypatch.delenv(freshness.SKIP_ENV)
    said = []
    freshness.ensure_fresh(said.append, repos=(freshness.Repo("x", tmp_path, "main"),))
    assert "x: not a git checkout" in said[0]


def test_a_sheet_printed_with_other_marks_than_the_layout_here_is_refused():
    geom = layout.build_cut_svg(None)
    here = layout.printed_marks()
    assert cutting.marks_mismatch(here, geom) is None
    # 2026-11-01: the PC's stale checkout printed the first 3x3 layout's marks
    first = {"inset": 3.5, "inset_y": None, "length": 10.5}
    why = cutting.marks_mismatch(first, geom)
    assert why.startswith("this sheet was printed with its marks 3.5 mm from the sides, 3.5 from the top")
    assert cutting.marks_mismatch({}, geom) is None  # run.json from before marks were recorded
