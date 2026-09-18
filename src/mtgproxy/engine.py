"""In-process access to the vendored silhouette-card-maker engine.

The engine is a flat collection of scripts (not a package) that assumes it runs
from its own directory: ``game/*`` and ``data/offset_data.json`` are relative
paths. Everything here puts the engine root on sys.path and runs engine calls
under ``chdir(SCM)``, so the rest of mtg-proxy can stay cwd-agnostic.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from .paths import GAME, SCM

FRONT = GAME / "front"
BACK = GAME / "back"
DOUBLE_SIDED = GAME / "double_sided"
OUTPUT = GAME / "output"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


class EngineMissing(Exception):
    pass


def ensure_engine() -> None:
    if not (SCM / "create_pdf.py").is_file():
        raise EngineMissing(
            f"silhouette-card-maker engine missing at {SCM} — run ./setup.sh "
            "(clones github.com/j0nas/silhouette-card-maker, branch local-patches)"
        )
    if str(SCM) not in sys.path:
        sys.path.insert(0, str(SCM))


@contextlib.contextmanager
def in_engine() -> Iterator[Path]:
    """cwd = engine root for the duration (the engine's relative paths need it)."""
    ensure_engine()
    prev = Path.cwd()
    os.chdir(SCM)
    try:
        yield SCM
    finally:
        os.chdir(prev)


def module(name: str) -> ModuleType:
    ensure_engine()
    return importlib.import_module(name)


def scryfall_module() -> ModuleType:
    return module("plugins.mtg.scryfall")


def ensure_game_dirs() -> None:
    for d in (FRONT, BACK, DOUBLE_SIDED, OUTPUT):
        d.mkdir(parents=True, exist_ok=True)


def images_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def clean_images() -> None:
    """Empty game/front and game/double_sided (the engine's clean_up.py, in-process)."""
    ensure_game_dirs()
    with in_engine():
        module("clean_up").delete_files()


def set_back(back: Path) -> None:
    ensure_game_dirs()
    for f in images_in(BACK):
        f.unlink()
    shutil.copyfile(back, BACK / f"back{back.suffix.lower()}")


def _run_click(cmd, name: str, args: list[str]) -> None:
    """Run an engine click command in-process; usage errors and non-zero exits become RuntimeError."""
    import click

    try:
        cmd.main(args=args, prog_name=name, standalone_mode=False)
    except click.ClickException as e:  # bad/unknown option, bad value, ...
        raise RuntimeError(f"{name}: {e.format_message()}") from e
    except click.Abort as e:
        raise RuntimeError(f"{name}: aborted") from e
    except SystemExit as e:  # --help and friends
        if e.code not in (0, None):
            raise RuntimeError(f"{name} exited with {e.code}") from e


def _click_main(mod: ModuleType, args: list[str]) -> None:
    _run_click(mod.cli, mod.__name__, args)


def fetch_cards(deck_path: Path, fmt: str, args: list[str]) -> None:
    """plugins/mtg/fetch.py <deck> <format> [args] — writes game/front + game/double_sided."""
    ensure_game_dirs()
    with in_engine():
        _click_main(module("plugins.mtg.fetch"), [str(deck_path), fmt, *args])


def create_pdf(args: list[str]) -> None:
    with in_engine():
        _click_main(module("create_pdf"), args)


def offset_pdf(args: list[str]) -> None:
    """offset_pdf.py passthrough (duplex calibration); runs against the engine's data/."""
    with in_engine():
        _run_click(module("offset_pdf").offset_pdf, "offset_pdf", args)


def layouts() -> dict:
    import json

    return json.loads((SCM / "assets" / "layouts.json").read_text())


def run_engine_script(script: str, args: list[str]) -> None:
    """Run one of the engine's scripts as a subprocess (same interpreter, engine cwd)."""
    with in_engine() as scm:
        try:
            subprocess.run([sys.executable, script, *args], cwd=scm, check=True, stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"{script} {' '.join(args)} failed (exit {e.returncode})") from e


def size_to_mm(s: str) -> float:
    return module("size_convert").size_to_mm(s)
