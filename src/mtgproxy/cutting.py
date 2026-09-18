"""Send a sheet's card outlines to the Cameo 5 Alpha directly, bypassing Silhouette Studio.

Why: Studio + Alpha firmware 1.05 mis-detect the machine and pick the wrong
registration-mark scan (3-mark vs 4-mark) on its own. Here the scan command is
chosen explicitly (4 → TB124 four L-marks, 3 → TB123 square + 2 L-marks), the
mark geometry comes from the same layouts.json that placed the marks in the
PDF, and force/speed/depth/passes are plain flags. The driver is
inkscape-silhouette's pure-Python sendto_silhouette.py in its own venv.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import layout, studio3
from .paths import DRV, DRV_PY, ROOT

SCAN_COMMAND = {"3": "TB123", "4": "TB124"}
DRV_SETUP_HINT = (
    f"inkscape-silhouette venv missing at {DRV_PY}. Run ./setup.sh, or by hand:\n"
    f"  git clone https://github.com/fablabnbg/inkscape-silhouette {DRV}\n"
    f"  cd {DRV} && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python "
    "-r requirements.txt libusb1 bleak && uv pip install --python .venv/bin/python --no-deps inkex\n"
    "  (libusb itself: brew formula 'libusb', managed in the dotfiles)"
)


class CutError(Exception):
    pass


@dataclass
class CutOptions:
    paper: str = "a4"
    card_size: str = "standard"
    registration: str = "4"
    force: int = 25
    speed: int = 25
    depth: int = 5
    passes: int = 3
    x_off: float | None = None
    y_off: float | None = None
    connection: str = "usb"  # usb | ble
    ble_name: str = "CAMEO 5 ALPHA"
    svg: Path | None = None
    dry_run: bool = False
    preview: bool = False
    extra: list[str] = field(default_factory=list)
    out_dir: Path = field(default_factory=lambda: ROOT / "output" / "cut")
    label: str | None = None  # deck name, for log/transcript file names


def driver_argv(o: CutOptions, geom: layout.Geometry, x_off: float, y_off: float, name: str) -> list[str]:
    quad = "True" if o.registration == "4" else "False"
    args = [
        "--preview", "True" if o.preview else "False",
        "--dry_run", "True" if o.dry_run else "False",
        "--tool", "autoblade",
        "--pressure", str(o.force), "--speed", str(o.speed), "--depth", str(o.depth), "--multipass", str(o.passes),
        "--cuttingmat", "cameo_12x12",
        "--regmark", "True", "--regsearch", "True", "--quadregmarks", quad,
        "--reg-x", str(geom.reg_x_mm), "--reg-y", str(geom.reg_y_mm),
        "--rego-x", str(geom.reg_inset_mm), "--rego-y", str(geom.reg_inset_mm),
        "--x-off", str(x_off), "--y-off", str(y_off),
        "--endposition", "below",
        "--logfile", str(o.out_dir / f"{name}.log"),
        "--cmdfile", str(o.out_dir / f"{name}.cmds"),
    ]  # fmt: skip
    if o.connection == "ble":
        args += ["--connection_type", "ble", "--bluetooth_name", o.ble_name]
    if o.dry_run:
        # No device to answer the firmware query in a dry run: pin the model so the
        # transcript shows the Alpha's real command set (TB124 for 4-mark).
        args += ["--force_hardware", "Silhouette_Cameo5_Alpha"]
    return args + list(o.extra)


def scan_command_in(transcript: str) -> str | None:
    m = re.search(r"TB12[34],[0-9,]*", transcript)
    return m.group(0) if m else None


def ensure_driver() -> None:
    if not DRV_PY.is_file():
        raise CutError(DRV_SETUP_HINT)


def ble_scan() -> int:
    ensure_driver()
    argv = [
        str(DRV_PY), str(DRV / "sendto_silhouette.py"),
        "--connection_type=ble", "--bluetooth_scan=True", "--preview", "False",
        str(DRV / "examples" / "testcut_square_triangle.svg"),
    ]  # fmt: skip
    return subprocess.call(argv)


def run_cut(o: CutOptions) -> int:
    if o.registration not in SCAN_COMMAND:
        raise CutError("--registration must be 3 or 4")
    ensure_driver()
    o.out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Cut geometry from the card-maker's own layout engine (same source as the PDF).
    if o.svg is None:
        name = o.label or f"{o.paper}-{o.card_size}"
        svg = o.out_dir / f"{name}.svg"
        geom = layout.build_cut_svg(o.paper, o.card_size, svg)
    else:
        if not o.svg.is_file():
            raise CutError(f"SVG not found: {o.svg}")
        svg, name = o.svg, o.svg.stem
        geom = layout.build_cut_svg(o.paper, o.card_size, None)

    # 2. Machine cut bias (measured with Studio; same file the .studio3 path uses).
    cfg_x, cfg_y = studio3.read_cut_offset()
    x_off = cfg_x if o.x_off is None else o.x_off
    y_off = cfg_y if o.y_off is None else o.y_off

    argv = [
        str(DRV_PY),
        str(DRV / "sendto_silhouette.py"),
        *driver_argv(o, geom, x_off, y_off, name),
        str(svg),
    ]
    print(
        f"cut-proxies: {name} — {geom.cards} cards, {o.registration}-mark registration "
        f"(marks inset {geom.reg_inset_mm}mm, {geom.reg_x_mm}x{geom.reg_y_mm}mm apart)"
    )
    print(
        f"cut-proxies: force {o.force} · speed {o.speed} · depth {o.depth} · passes {o.passes} · "
        f"offset x={x_off:g}mm y={y_off:g}mm · via {o.connection}"
    )
    if o.dry_run:
        print(f"cut-proxies: DRY RUN — nothing is sent; transcript in {o.out_dir / f'{name}.cmds'}")
        # The dry run necessarily dies at the registration scan (no machine answers), so
        # its traceback is noise; keep it in a file and judge the transcript instead.
        with open(o.out_dir / f"{name}.stderr", "w") as err:
            subprocess.run(argv, stderr=err, check=False)
        want = SCAN_COMMAND[o.registration]
        try:
            cmd = scan_command_in((o.out_dir / f"{name}.cmds").read_text(errors="replace"))
        except OSError:
            cmd = None
        if cmd and cmd.startswith(want):
            print(
                f"cut-proxies: dry run OK — regmark scan command {cmd} ({o.registration}-mark), transcript {o.out_dir / f'{name}.cmds'}"
            )
            return 0
        raise CutError(
            f"dry run did not produce the expected {want} scan (got '{cmd or 'nothing'}') — see {o.out_dir / f'{name}.stderr'}"
        )

    rc = subprocess.run(argv, check=False).returncode
    if rc != 0:
        msg = f"FAILED (exit {rc}) — log: {o.out_dir / f'{name}.log'}"
        try:
            if "registration marks" in (o.out_dir / f"{name}.log").read_text(errors="replace"):
                msg += (
                    f"\ncut-proxies: the machine could not find the marks. Check: sheet printed at 100% "
                    f"(marks {geom.reg_inset_mm}mm from the paper edge),\ncut-proxies: -r {o.registration} matches "
                    "the printed pattern, sheet top-left on the mat, lid closed, no glare."
                )
        except OSError:
            pass
        raise CutError(msg)
    print(
        f"cut-proxies: done — {geom.cards} cards cut. Don't eject yet: lift a corner and rerun with more --passes if needed."
    )
    return 0
