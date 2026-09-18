"""Print a sheet through CUPS at exact size.

Scaling is what breaks registration, so print-scaling=none is forced and the
media is set to match the PDF. Real sheets go to the "4x2 Glossy" preset from
the Windows driver, kept on the Mac as the CUPS printer instance
<queue>/4x2-glossy — EPSON_ET_8550_Series_2 on the Mac (~/.cups/lpoptions, chezmoi-managed; see
docs/printer-presets/README.md); explicit options are used when that instance
isn't there. Test sheets use printer defaults on plain paper.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INSTANCE = "EPSON_ET_8550_Series_2/4x2-glossy"  # the Epson-driver queue on the Mac (2026-09)
INSTANCE_SUFFIX = "4x2-glossy"  # any queue carrying a /4x2-glossy instance qualifies
MEDIA = {"a4": "A4", "letter": "Letter", "a3": "A3"}
GLOSSY_OPTS = [
    "-o",
    "MediaType=photographic-glossy",
    "-o",
    "InputSlot=rear",
    "-o",
    "cupsPrintQuality=High",
    "-o",
    "ColorModel=RGB",
]


@dataclass
class PrintJob:
    argv: list[str]
    description: str


def queue_exists(queue: str) -> bool:
    """True when CUPS knows the queue (the instance's base name, before the slash)."""
    if not lp_available():
        return False
    return subprocess.run(["lpstat", "-p", queue], capture_output=True, check=False).returncode == 0


def defined_instances(lpoptions: Path | None = None) -> list[str]:
    lpoptions = lpoptions or Path.home() / ".cups" / "lpoptions"
    try:
        lines = lpoptions.read_text().splitlines()
    except OSError:
        return []
    return [line.split()[1] for line in lines if line.startswith("Dest ") and len(line.split()) > 1]


def find_instance(preferred: str, lpoptions: Path | None = None, exists=queue_exists) -> str | None:
    """The preferred instance if usable, else any ``<queue>/4x2-glossy`` whose queue exists.

    The queue name changes when the printer is re-added on the Mac (AirPrint → Epson
    driver became EPSON_ET_8550_Series_2), so the instance is looked up by its suffix
    and verified against CUPS rather than trusted from ~/.cups/lpoptions alone.
    """
    candidates = [
        i for i in defined_instances(lpoptions) if i == preferred or i.endswith("/" + INSTANCE_SUFFIX)
    ]
    candidates.sort(key=lambda i: i != preferred)
    for inst in candidates:
        if exists(inst.split("/", 1)[0]):
            return inst
    return None


def instance_defined(instance: str, lpoptions: Path | None = None) -> bool:
    return instance in defined_instances(lpoptions)


def build_job(
    pdf: Path,
    paper: str,
    *,
    test_sheet: bool,
    printer: str | None = None,
    instance: str | None = None,
    extra_opts: str | None = None,
    lpoptions: Path | None = None,
    exists=queue_exists,
) -> PrintJob:
    media = MEDIA.get(paper, paper)
    args = ["-o", f"media={media}", "-o", "print-scaling=none", "-o", "fit-to-page=false"]
    dest: list[str] = []
    if test_sheet:
        if printer:
            dest = ["-d", printer]
        desc = f"{printer or 'default printer'}, plain paper / driver defaults"
    else:
        preferred = instance or os.environ.get("MTG_PROXY_LP_INSTANCE") or DEFAULT_INSTANCE
        found = None if printer else find_instance(preferred, lpoptions, exists=exists)
        if found:
            dest = ["-d", found]
            desc = f"{found} (rear feeder, glossy photo, High)"
        else:
            if printer:
                dest = ["-d", printer]
            args += GLOSSY_OPTS
            desc = f"{printer or 'default printer'}, explicit glossy options"
    extra = shlex.split(extra_opts if extra_opts is not None else os.environ.get("MTG_PROXY_LP_OPTS", ""))
    return PrintJob(["lp", *dest, *args, *extra, str(pdf)], f"{desc} (media={media}, 100%, no scaling)")


def lp_available() -> bool:
    return shutil.which("lp") is not None


def send(job: PrintJob) -> bool:
    """Run lp; False (not an exception) when it fails, so the caller can carry on."""
    return subprocess.run(job.argv, check=False).returncode == 0
