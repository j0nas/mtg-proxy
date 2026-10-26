"""Print a sheet through the Windows PC's Epson driver.

Nothing is printed from the Mac. A PDF is turned upright (the driver ignores a
landscape setting and clips the page), copied to the PC with scp, and handed to
SumatraPDF over SSH with scaling off, aimed at one of the PC's print queues.
Each queue carries its own paper preset as its defaults (docs/printer-presets/).
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

HOST = "pomelomadness.local"
USER = "jonas"
KEY = Path("~/.ssh/pomelomadness_ed25519").expanduser()
REMOTE_DIR = "C:/Users/jonas/Desktop/projects/mtg-proxy/print"
SUMATRA = r"C:\Users\jonas\AppData\Local\SumatraPDF\SumatraPDF.exe"

QUEUES = {
    "plain": "ET-8550 Series(Network)",
    "glossy": "Glossy photo paper proxies",
    "premium": "Premium glossy paper",
}
# paper name -> (short side, long side) in mm
PAPERS = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "letter": (215.9, 279.4)}
TOLERANCE_MM = 2.0
PT_MM = 25.4 / 72

Runner = Callable[[list[str], str | None], "subprocess.CompletedProcess[str]"]


class PrintError(Exception):
    """A step failed; the message is the one line shown to the user."""


@dataclass
class Result:
    queue: str
    remote_pdf: str
    command: str
    sent: bool


def run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)


def _page_size_mm(page) -> tuple[float, float]:
    w, h = float(page.mediabox.width) * PT_MM, float(page.mediabox.height) * PT_MM
    return (h, w) if (page.get("/Rotate") or 0) % 180 else (w, h)


def paper_name(width: float, height: float) -> str:
    short, long_ = sorted((width, height))
    for name, (s, lg) in PAPERS.items():
        if abs(short - s) <= TOLERANCE_MM and abs(long_ - lg) <= TOLERANCE_MM:
            return name
    raise PrintError(f"page size {short:.0f}x{long_:.0f} mm is not A4, A3 or letter")


def make_upright(pdf: Path, out: Path) -> tuple[str, int, int]:
    """Write ``out`` with landscape pages turned 90 degrees; return (paper, pages, rotated)."""
    reader = PdfReader(pdf)
    if not reader.pages:
        raise PrintError(f"{pdf.name} has no pages")
    writer = PdfWriter()
    sizes, rotated = [], 0
    for page in reader.pages:
        w, h = _page_size_mm(page)
        sizes.append(paper_name(w, h))
        if w > h:
            page.rotate(90)
            rotated += 1
        writer.add_page(page)
    if len(set(sizes)) > 1:
        raise PrintError(f"pages mix sizes ({', '.join(sorted(set(sizes)))})")
    writer.write(out)
    return sizes[0], len(sizes), rotated


def _quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def print_command(queue: str, remote_pdf: str, paper: str) -> str:
    win = remote_pdf.replace("/", "\\")
    return (
        f"& {_quote(SUMATRA)} -print-to {_quote(queue)} "
        f"-print-settings {_quote(f'noscale,portrait,paper={paper}')} "
        f"-silent -exit-when-done {_quote(win)} | Out-Null"
    )


def _ssh_base() -> list[str]:
    return ["ssh", "-i", str(KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"{USER}@{HOST}"]


def powershell(script: str, runner: Runner = run) -> subprocess.CompletedProcess[str]:
    """Run a PowerShell script on the PC (stderr is CLIXML noise and is ignored)."""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    return runner([*_ssh_base(), f"powershell -NoProfile -EncodedCommand {b64}"], None)


def check_queue(queue: str, runner: Runner = run) -> None:
    script = f"if (Get-Printer -Name {_quote(queue)} -ErrorAction SilentlyContinue) {{ 'present' }} else {{ 'missing' }}"
    r = powershell(script, runner)
    out = r.stdout.strip()
    if out == "present":
        return
    if out == "missing":
        raise PrintError(f"print queue '{queue}' does not exist on {HOST}")
    raise PrintError(f"cannot reach {HOST} over ssh (is the PC on, on this LAN?)")


def copy_to_pc(local: Path, name: str, runner: Runner = run) -> str:
    remote = f"{REMOTE_DIR}/{name}"
    r = powershell(f"New-Item -ItemType Directory -Force -Path {_quote(REMOTE_DIR)} | Out-Null", runner)
    if r.returncode != 0:
        raise PrintError(f"could not create {REMOTE_DIR} on the PC")
    r = runner(["scp", "-q", "-i", str(KEY), str(local), f"{USER}@{HOST}:{remote}"], None)
    if r.returncode != 0:
        raise PrintError("scp to the PC failed")
    size = powershell(f"(Get-Item -LiteralPath {_quote(remote)}).Length", runner).stdout.strip()
    if size != str(local.stat().st_size):
        raise PrintError(
            f"copy check failed: {size or 'no file'} bytes on the PC, {local.stat().st_size} here"
        )
    return remote


def print_pdf(
    pdf: Path,
    kind: str = "glossy",
    *,
    dry_run: bool = False,
    runner: Runner = run,
    say: Callable[[str], None] = print,
) -> Result:
    """Copy ``pdf`` to the PC and print it on the ``kind`` queue; raises PrintError."""
    if kind not in QUEUES:
        raise PrintError(f"unknown paper '{kind}' (plain, glossy, premium)")
    if not pdf.is_file():
        raise PrintError(f"{pdf} not found")
    queue = QUEUES[kind]
    with tempfile.TemporaryDirectory() as tmp:
        name = f"{pdf.stem}-portrait.pdf"
        upright = Path(tmp) / name
        paper, pages, rotated = make_upright(pdf, upright)
        check_queue(queue, runner)  # before anything is copied
        say(f"queue '{queue}' ok")
        say(f"{name}: {paper}, {pages} page(s), {rotated} turned upright")
        remote = copy_to_pc(upright, name, runner)
        say(f"copied to {remote} ({upright.stat().st_size} bytes)")
    command = print_command(queue, remote, paper)
    if dry_run:
        say(f"dry run, would run on the PC: {command}")
        return Result(queue, remote, command, False)
    # Sent once and never retried: glossy jobs spool slowly and a resend prints twice.
    script = (
        command
        + f"\nGet-PrintJob -PrinterName {_quote(queue)} | ForEach-Object "
        + "{ '{0} {1} {2}' -f $_.Id, $_.DocumentName, $_.JobStatus }"
    )
    r = powershell(script, runner)
    say(f"sent to '{queue}'")
    jobs = r.stdout.strip()
    say(
        f"queue jobs: {jobs.replace(chr(10), ' | ')}"
        if jobs
        else "queue shows no job yet (it may already have spooled)"
    )
    return Result(queue, remote, command, True)
