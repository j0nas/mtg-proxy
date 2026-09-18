"""Mirror a run's output to the Windows side under WSL.

Real copies — symlinks don't cross the WSL/Windows boundary. Files land FLAT in
<Windows profile>\\Desktop\\projects\\mtg-proxy (profile read from cmd.exe's
%USERPROFILE%, so no user name is baked in; MTG_PROXY_WIN_OUT overrides it
outright). CUT-NOTES.md is not mirrored — view it with ``mtg-proxy notes``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .paths import NOTES_NAME, SIDECAR_NAME


@dataclass
class MirrorResult:
    windows_path: str
    failed: list[str] = field(default_factory=list)


def windows_out_dir() -> Path | None:
    env = os.environ.get("MTG_PROXY_WIN_OUT")
    if env:
        return Path(env)
    if not Path("/mnt/c/Users").is_dir() or not shutil.which("cmd.exe") or not shutil.which("wslpath"):
        return None
    try:
        home = (
            subprocess.run(
                ["cmd.exe", "/c", "echo %USERPROFILE%"],
                cwd="/mnt/c",
                capture_output=True,
                text=True,
                check=False,
            )
            .stdout.strip()
            .replace("\r", "")
        )
        if not home:
            return None
        unix = subprocess.run(
            ["wslpath", "-u", home], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(unix) / "Desktop" / "projects" / "mtg-proxy"


def to_windows_notation(p: Path) -> str:
    s = str(p)
    if s.startswith("/mnt/c"):
        s = "C:" + s[len("/mnt/c") :]
    return s.replace("/", "\\")


def mirror(out: Path, name: str, win_out: Path) -> MirrorResult:
    """Copy this run's files into ``win_out`` flat, wiping our previous artifacts first."""
    win_out.mkdir(parents=True, exist_ok=True)
    # Templates are regenerated fresh every run, and stale ones are dangerous to cut with.
    for stale in [win_out / f"{name}.pdf", win_out / f"{name}-duplex.pdf", win_out / f"{name}-{NOTES_NAME}"]:
        stale.unlink(missing_ok=True)
    for stale in win_out.glob("*.studio3"):
        stale.unlink(missing_ok=True)
    failed = []
    for f in sorted(out.iterdir()):
        if f.name in (NOTES_NAME, SIDECAR_NAME) or not f.is_file():
            continue
        try:
            shutil.copyfile(f, win_out / f.name)
        except OSError:
            failed.append(f.name)
    return MirrorResult(to_windows_notation(win_out), failed)
