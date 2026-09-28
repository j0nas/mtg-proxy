"""Send a sheet's card outlines to the Cameo 5 Alpha directly, bypassing Silhouette Studio.

Why: Studio + Alpha firmware 1.05 mis-detect the machine and pick the wrong
registration-mark scan (3-mark vs 4-mark) on its own. Here the scan command is
chosen explicitly (4 → TB124 four L-marks, 3 → TB123 square + 2 L-marks), the
mark geometry comes from the same layouts.json that placed the marks in the
PDF, and force/speed/depth/passes are plain flags. The driver is
inkscape-silhouette's pure-Python sendto_silhouette.py in its own venv.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field, replace
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
    connection: str = "ble"  # ble | usb
    ble_name: str = "CAMEO 5 ALPHA"
    svg: Path | None = None
    dry_run: bool = False
    preview: bool = False
    extra: list[str] = field(default_factory=list)
    # Mark geometry to announce before the scan (mm). None = the driver's hard-coded 20 × 0.5,
    # Silhouette Studio's defaults; the card-maker's A4 marks are 9.4 × 1 (see regmark_launch.py).
    reg_length: float | None = None
    reg_thickness: float | None = None
    # With registration the driver clips every cut to the rectangle between the marks (software
    # only — the Cameo 5 line gets no hardware limit there). Widen it right/down by this many mm.
    cut_beyond: float = 0.0
    # Mark corners' distance from the paper edges (mm). None = the layout's (10 for the card-maker's
    # A4). A smaller inset gives a design more room: nothing may sit above/left of the top-left mark.
    reg_inset: float | None = None
    out_dir: Path = field(default_factory=lambda: ROOT / "output" / "cut")
    label: str | None = None  # deck name, for log/transcript file names
    # Our own driver (mtgproxy.cameo) unless this is set; the vendored inkscape-silhouette stays
    # available as a fallback until ours has cut real sheets.
    legacy: bool = False
    probe: bool = False  # scan and query the machine, cut nothing (see cameo.driver.probe)
    scan_starts: list[tuple[float, float]] | None = None  # (top, left) mm; None = the driver's defaults


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
    if o.connection == "ble" and not o.dry_run:
        # A dry run stays off the air: with a BLE name the driver would find and connect to the
        # machine even then, and the simulated scan needs no device at all.
        args += ["--connection_type", "ble", "--bluetooth_name", o.ble_name]
    if o.dry_run:
        # No device to answer the firmware query in a dry run: pin the model so the
        # transcript shows the Alpha's real command set (TB124 for 4-mark).
        args += ["--force_hardware", "Silhouette_Cameo5_Alpha"]
    return args + list(o.extra)


LAUNCHER = Path(__file__).with_name("regmark_launch.py")


def with_inset(geom: layout.Geometry, inset: float) -> layout.Geometry:
    """The same page with its marks moved to ``inset`` mm from every paper edge."""
    return replace(
        geom,
        reg_inset_mm=inset,
        reg_x_mm=round(geom.page_w_mm - 2 * inset, 3),
        reg_y_mm=round(geom.page_h_mm - 2 * inset, 3),
    )


def command(o: CutOptions, driver_args: list[str], svg: Path) -> list[str]:
    """The full argv: the driver directly, or through the launcher for mark-geometry overrides and
    for dry runs (where it simulates the scan so the whole cut is exercised)."""
    if o.reg_length is None and o.reg_thickness is None and not o.cut_beyond and not o.dry_run:
        return [str(DRV_PY), str(DRV / "sendto_silhouette.py"), *driver_args, str(svg)]
    opts: list[str] = []
    for flag, value in (
        ("--length", o.reg_length),
        ("--thickness", o.reg_thickness),
        ("--beyond", o.cut_beyond),
    ):
        if value:
            opts += [flag, str(value)]
    return [str(DRV_PY), str(LAUNCHER), str(DRV), *opts, "--", *driver_args, str(svg)]


def clip_report(log: str) -> tuple[int, str] | None:
    """(points clipped, cut bbox) from the driver log's final bounding-box line."""
    m = re.search(r"Final bounding box and point counts: (\{.*\})", log)
    if not m:
        return None
    count = re.search(r"'clip': \{[^}]*'count': (\d+)", m.group(1))
    bbox = re.search(r"'llx': ([-\d.]+), 'urx': ([-\d.]+), 'lly': ([-\d.]+), 'ury': ([-\d.]+)\}$", m.group(1))
    where = (
        f"x {float(bbox.group(1)):g}..{float(bbox.group(2)):g}, y {float(bbox.group(4)):g}..{float(bbox.group(3)):g} mm"
        if bbox
        else "?"
    )
    return (int(count.group(1)) if count else 0), where


def mark_commands_in(transcript: str) -> tuple[str | None, str | None]:
    """The TB51 (length) and TB53 (thickness) values the transcript announced."""
    length = re.search(r"TB51,(\d+)", transcript)
    thickness = re.search(r"TB53,(\d+)", transcript)
    return (length.group(1) if length else None, thickness.group(1) if thickness else None)


def reached_cut(log: str) -> bool:
    """Did the driver get as far as sending the cut? It logs its final bounding box after the mark
    scan, right before the cut paths go out. Failing to connect or finding no media loaded happen
    earlier: the driver reports them, swallows them and still exits 0."""
    return "Final bounding box and point counts:" in log


DRIVER_ERRORS = re.compile(
    r"^(No Graphtec Silhouette devices found\.|Could not open Bluetooth.*|Could not query cutter status.*"
    r"|No media is loaded.*|Cannot determine whether media is loaded.*)$",
    re.M,
)


def driver_error(log: str) -> str | None:
    """The driver's own reason for stopping before the cut, if it logged a known one."""
    m = DRIVER_ERRORS.search(log)
    return m.group(1) if m else None


def scan_command_in(transcript: str) -> str | None:
    m = re.search(r"TB12[34],[0-9,]*", transcript)
    return m.group(0) if m else None


def ensure_driver() -> None:
    if not DRV_PY.is_file():
        raise CutError(DRV_SETUP_HINT)


def ble_scan() -> int:
    from .cameo.transport import BleTransport

    found = BleTransport.discover()
    print(f"Found {len(found)} Bluetooth LE device(s):")
    for address, name in found:
        print(f"    {address}   {name or '(unnamed)'}")
    return 0


def run_cut(o: CutOptions) -> int:
    if o.registration not in SCAN_COMMAND:
        raise CutError("--registration must be 3 or 4")
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
    if o.reg_inset is not None:
        geom = with_inset(geom, o.reg_inset)

    # 2. Machine cut bias. Studio's (the .studio3 path and the legacy driver) was measured with
    # Studio; our driver's with deckbox's calibration sheets (pnpm calib, 2026-09-28).
    cfg_x, cfg_y = studio3.read_cut_offset(section=None if o.legacy else "driver")
    x_off = cfg_x if o.x_off is None else o.x_off
    y_off = cfg_y if o.y_off is None else o.y_off

    if not o.legacy:
        return run_ours(o, svg, name, geom, (x_off, y_off))
    ensure_driver()
    argv = command(o, driver_argv(o, geom, x_off, y_off, name), svg)
    what = f"{geom.cards} cards" if o.svg is None else svg.name
    print(
        f"cut-proxies: {name} — {what}, {o.registration}-mark registration "
        f"(marks inset {geom.reg_inset_mm}mm, {geom.reg_x_mm}x{geom.reg_y_mm}mm apart)"
    )
    print(
        f"cut-proxies: force {o.force} · speed {o.speed} · depth {o.depth} · passes {o.passes} · "
        f"offset x={x_off:g}mm y={y_off:g}mm · via {o.connection}"
    )
    if o.reg_length is not None or o.reg_thickness is not None:
        print(
            f"cut-proxies: announcing marks {20.0 if o.reg_length is None else o.reg_length:g} mm long, "
            f"{0.5 if o.reg_thickness is None else o.reg_thickness:g} mm thick (driver default 20 x 0.5)"
        )
    if o.dry_run:
        print(f"cut-proxies: DRY RUN — nothing is sent; transcript in {o.out_dir / f'{name}.cmds'}")
        # The dry run necessarily dies at the registration scan (no machine answers), so
        # its traceback is noise; keep it in a file and judge the transcript instead.
        with open(o.out_dir / f"{name}.stderr", "w") as err:
            subprocess.run(argv, stderr=err, check=False, env={**os.environ, "MTGPROXY_SIMULATE_SCAN": "1"})
        want = SCAN_COMMAND[o.registration]
        try:
            cmd = scan_command_in((o.out_dir / f"{name}.cmds").read_text(errors="replace"))
        except OSError:
            cmd = None
        if cmd and cmd.startswith(want):
            length, thickness = mark_commands_in((o.out_dir / f"{name}.cmds").read_text(errors="replace"))
            print(
                f"cut-proxies: dry run OK — regmark scan command {cmd} ({o.registration}-mark), "
                f"marks announced TB51,{length} TB53,{thickness}, transcript {o.out_dir / f'{name}.cmds'}"
            )
            try:
                report = clip_report((o.out_dir / f"{name}.log").read_text(errors="replace"))
            except OSError:
                report = None
            if report is None:
                raise CutError(
                    "dry run: the simulated scan did not reach the cut — no bounding box in the log"
                )
            clipped, where = report
            print(f"cut-proxies: cut spans {where} from the top-left mark; {clipped} point(s) clipped")
            if clipped:
                raise CutError(
                    f"{clipped} point(s) fall outside the cut area (the mark frame"
                    + (f" widened {o.cut_beyond:g} mm right/down" if o.cut_beyond else "")
                    + ") and would NOT be cut. Keep the design inside it, or raise --cut-beyond."
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
    try:
        log = (o.out_dir / f"{name}.log").read_text(errors="replace")
    except OSError:
        log = ""
    if not reached_cut(log):
        reason = driver_error(log) or "the driver stopped before the cut"
        if "media" in reason:
            hint = "Load the mat (the Cameo's load button), then rerun."
        elif o.connection == "ble":
            hint = (
                "Is the Cameo on, in range, and not connected to Studio or a phone? "
                "`cut-proxies --scan` lists what's advertising."
            )
        else:
            hint = "Is the cable plugged in and the Cameo on? Drop --usb to cut over Bluetooth."
        raise CutError(
            f"NOTHING WAS CUT over {o.connection}: {reason}\ncut-proxies: {hint}\n"
            f"cut-proxies: log: {o.out_dir / f'{name}.log'}"
        )
    print(
        f"cut-proxies: done — {what} cut. Don't eject yet: lift a corner and rerun with more --passes if needed."
    )
    return 0


def run_ours(o: CutOptions, svg: Path, name: str, geom: layout.Geometry, bias: tuple[float, float]) -> int:
    """cut-proxies on mtgproxy.cameo: our geometry, our protocol, our registration."""
    from .cameo import driver, protocol, session
    from .cameo.transport import TransportError

    frame = session.Frame(
        page_w=geom.page_w_mm,
        page_h=geom.page_h_mm,
        inset=geom.reg_inset_mm,
        width=geom.reg_x_mm,
        height=geom.reg_y_mm,
        length=9.4 if o.reg_length is None else o.reg_length,
        thickness=1.0 if o.reg_thickness is None else o.reg_thickness,
        marks=int(o.registration),
    )
    try:
        blade = protocol.Blade(o.force, o.speed, o.depth)
        job = driver.plan(svg, frame, blade, o.passes, bias)
    except (ValueError, session.CutError) as e:
        raise CutError(str(e)) from e
    starts = o.scan_starts or driver.default_starts(frame)
    what = f"{geom.cards} cards" if o.svg is None else svg.name
    via = "nothing (dry run)" if o.dry_run else o.connection
    print(
        f"cut-proxies: {name} — {what}, {o.registration}-mark registration "
        f"(marks inset {frame.inset:g} mm, {frame.width:g} x {frame.height:g} mm apart, "
        f"{frame.length:g} x {frame.thickness:g} mm)"
    )
    print(
        f"cut-proxies: force {o.force} · speed {o.speed} · depth {o.depth} · passes {o.passes} · "
        f"offset x={bias[0]:g}mm y={bias[1]:g}mm · via {via}"
    )
    if o.cut_beyond:
        print("cut-proxies: (--cut-beyond is for --legacy-driver only; ours checks the paper edge instead)")
    log_path = o.out_dir / f"{name}.session.jsonl"
    log = session.SessionLog(log_path, echo=lambda m: print(f"cut-proxies: {m}"))
    try:
        t = driver.open_transport(o.connection, o.ble_name, log, o.dry_run)
        if o.probe:
            report = driver.probe(frame, t, log, starts)
            print(probe_table(report["replies"]))
            print(f"cut-proxies: probe done; log {log_path}")
            return 0
        result = driver.cut(job, t, log, starts)
    except (session.CutError, TransportError) as e:
        raise CutError(f"{e}\ncut-proxies: session log: {log_path}") from e
    finally:
        log.close()
    x0, y0, x1, y1 = result.extent
    print(f"cut-proxies: cut spans x {x0:.2f}..{x1:.2f}, y {y0:.2f}..{y1:.2f} mm from the top-left mark")
    if o.dry_run:
        transcript = o.out_dir / f"{name}.cmds"
        transcript.write_bytes(bytes(t.sent))
        print(f"cut-proxies: dry run OK — transcript {transcript}, session log {log_path}")
        return 0
    print(
        f"cut-proxies: done — {what} cut. Don't eject yet: lift a corner and rerun with more --passes if needed."
    )
    return 0


def probe_table(replies: dict[str, list[str | None]]) -> str:
    rows = [f"{'query':6} {'before scan':24} {'after scan':24}"]
    for q, (before, after) in replies.items():
        mark = "  <- changed" if before != after else ""
        rows.append(f"{q:6} {before!s:24} {after!s:24}{mark}")
    return "\n".join(rows)
