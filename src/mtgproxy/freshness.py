"""Is this machine running what GitHub has? Checked before every build and every cut.

Two checkouts decide what a sheet looks like and how it is cut: mtg-proxy itself, and the card-maker
engine cloned inside it (``silhouette-card-maker``, where the layouts and their mark positions
live), which ``setup.sh`` clones once and never updates. Each is compared with its GitHub branch:
behind it, the run stops and says what to pull; offline, it says it couldn't check and goes on;
ahead of it, or with uncommitted changes, it only says so.

On 2026-11-01 eight laminated proxy sheets came off the PC's printer with the first 3x3 layout's
marks (3.5 mm from every edge), from a checkout weeks behind GitHub, while the Mac cut them as
today's layout (9 mm from the top and bottom). Nothing compared the two, and an evening went to
misreads that looked like the machine's fault.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import ROOT, SCM

SKIP_ENV = "MTG_PROXY_NO_FRESHNESS"  # set (to anything) to skip the GitHub comparison: the test suite
TIMEOUT_S = 15.0  # per git command; a fetch over a slow link


@dataclass(frozen=True)
class Repo:
    name: str
    path: Path
    branch: str  # the GitHub branch it must not be behind


REPOS = (Repo("mtg-proxy", ROOT, "main"), Repo("the card-maker engine", SCM, "local-patches"))


@dataclass(frozen=True)
class State:
    repo: Repo
    head: str | None  # short hash of what runs here; None: not a git checkout
    behind: int | None = None  # commits on GitHub's branch missing here; None: couldn't tell
    ahead: int = 0  # commits here GitHub's branch doesn't have
    dirty: bool = False  # uncommitted changes to tracked files
    problem: str | None = None  # why GitHub couldn't be compared with

    def label(self) -> str:
        return f"{self.repo.name} {self.head or '?'}{'+changes' if self.dirty else ''}"


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )


def head(repo: Repo) -> tuple[str | None, bool]:
    """(short hash, uncommitted changes) of what this checkout runs; (None, False) when it isn't one."""
    if not (repo.path / ".git").exists():
        return None, False
    try:
        h = _git(repo.path, "rev-parse", "--short", "HEAD")
        d = _git(repo.path, "status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    return (h.stdout.strip() or None), bool(d.stdout.strip())


def state(repo: Repo) -> State:
    """This checkout against GitHub's branch: fetches it, then counts the commits either side."""
    short, dirty = head(repo)
    if short is None:
        return State(repo, None, problem="not a git checkout")
    try:
        fetch = _git(repo.path, "fetch", "--quiet", "origin", repo.branch)
        if fetch.returncode != 0:
            why = (
                fetch.stderr.strip().splitlines()[-1] if fetch.stderr.strip() else f"exit {fetch.returncode}"
            )
            return State(repo, short, dirty=dirty, problem=f"couldn't fetch from GitHub ({why})")
        counts = _git(repo.path, "rev-list", "--left-right", "--count", f"HEAD...origin/{repo.branch}")
    except (OSError, subprocess.TimeoutExpired) as e:
        return State(repo, short, dirty=dirty, problem=f"couldn't reach GitHub ({e})")
    try:
        ahead, behind = (int(v) for v in counts.stdout.split())
    except ValueError:
        return State(repo, short, dirty=dirty, problem=f"couldn't compare with origin/{repo.branch}")
    return State(repo, short, behind, ahead, dirty)


def versions() -> dict[str, str]:
    """What runs here, by checkout, without asking GitHub: recorded in run.json and the cut's log."""
    out = {}
    for repo in REPOS:
        short, dirty = head(repo)
        out[repo.name] = f"{short}{'+changes' if dirty else ''}" if short else "unknown"
    return out


class StaleError(Exception):
    pass


def ensure_fresh(echo, allow_stale: bool = False, repos: tuple[Repo, ...] = REPOS) -> list[State]:
    """Every checkout in ``repos`` compared with GitHub, one line said for the lot. Raises StaleError
    naming each one that is behind and the pull that fixes it, unless ``allow_stale``."""
    if os.environ.get(SKIP_ENV):
        return []
    states = [state(r) for r in repos]
    stale = [s for s in states if s.behind]
    notes = []
    for s in states:
        if s.problem:
            notes.append(f"{s.repo.name}: {s.problem}, so not compared with GitHub")
        elif s.ahead:
            notes.append(f"{s.repo.name} has {s.ahead} commit(s) GitHub's {s.repo.branch} doesn't")
    if not stale:
        fine = all(s.problem is None for s in states)
        echo(
            f"version: {', '.join(s.label() for s in states)}"
            + (" (up to date with GitHub)" if fine else "")
            + "".join(f"; {n}" for n in notes)
        )
        return states
    lines = [
        f"{s.repo.name} here ({s.head}) is {s.behind} commit(s) behind GitHub's {s.repo.branch}: "
        f"git -C {s.repo.path} pull"
        for s in stale
    ]
    if allow_stale:
        echo("WARNING: running a stale checkout (--allow-stale): " + "; ".join(lines))
        return states
    raise StaleError(
        "this machine's code is behind GitHub, and a stale checkout prints and cuts the wrong layout:\n  "
        + "\n  ".join(lines)
        + "\nthen rerun (--allow-stale runs it anyway)"
    )
