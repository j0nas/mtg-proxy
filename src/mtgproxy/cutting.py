"""Cut a printed sheet on the Cameo 5 Alpha with our own driver (``mtgproxy.cameo``).

The mark scan is chosen explicitly (4 → TB124 four L-marks, 3 → TB123 square + 2 L-marks), the
mark frame comes from the same layouts.json that placed the marks in the PDF, and the blade
settings come from a preset (laminate or plain paper), each overridable by a plain flag.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import layout
from .paths import CUT_OFFSET_FILE, ROOT


class CutError(Exception):
    pass


@dataclass(frozen=True)
class Preset:
    force: int
    speed: int
    depth: int
    passes: int


PRESETS = {
    # Jonas's proven proxy preset: 130 gsm glossy in 80 µm pouches, and the laminated deck box.
    "laminate": Preset(force=20, speed=25, depth=4, passes=3),
    # Plain copier paper, cut cleanly through (calibration sheets and deck-box outlines, 2026-09-28).
    "paper": Preset(force=10, speed=5, depth=1, passes=1),
}


@dataclass
class CutOptions:
    paper: str = "a4"
    card_size: str = "standard"
    registration: str = "4"
    force: int = PRESETS["laminate"].force
    speed: int = PRESETS["laminate"].speed
    depth: int = PRESETS["laminate"].depth
    passes: int = PRESETS["laminate"].passes
    x_off: float | None = None  # None = data/cut_offset.json
    y_off: float | None = None
    connection: str = "ble"  # ble | usb
    ble_name: str = "CAMEO 5 ALPHA"
    svg: Path | None = None
    dry_run: bool = False
    # The printed marks (mm): the card-maker's A4 marks and deckbox's are 9.4 × 1.
    reg_length: float = 9.4
    reg_thickness: float = 1.0
    # Mark corners' distance from the paper edges (mm). None = the layout's (10 for the card-maker's
    # A4). Nothing may be cut above/left of the top-left mark.
    reg_inset: float | None = None
    out_dir: Path = field(default_factory=lambda: ROOT / "output" / "cut")
    label: str | None = None  # deck name, for log/transcript file names
    probe: bool = False  # scan and query the machine, cut nothing (see cameo.driver.probe)
    proof: bool = False  # proof cuts over the marks after the scan; the job waits for y / r / q
    scan_starts: list[tuple[float, float]] | None = None  # (top, left) mm; None = the driver's defaults


def read_cut_offset(path: Path = CUT_OFFSET_FILE) -> tuple[float, float]:
    """(x_mm, y_mm) from data/cut_offset.json; (0, 0) when absent. Positive shifts cuts right/down."""
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        return 0.0, 0.0
    return float(cfg.get("x_mm", 0)), float(cfg.get("y_mm", 0))


def with_inset(geom: layout.Geometry, inset: float) -> layout.Geometry:
    """The same page with its marks moved to ``inset`` mm from every paper edge."""
    return replace(
        geom,
        reg_inset_mm=inset,
        reg_x_mm=round(geom.page_w_mm - 2 * inset, 3),
        reg_y_mm=round(geom.page_h_mm - 2 * inset, 3),
    )


def ble_scan() -> int:
    from .cameo.transport import BleTransport

    found = BleTransport.discover()
    print(f"Found {len(found)} Bluetooth LE device(s):")
    for address, name in found:
        print(f"    {address}   {name or '(unnamed)'}")
    return 0


def run_cut(o: CutOptions) -> int:
    from .cameo import driver, protocol, session
    from .cameo.transport import TransportError

    if o.registration not in ("3", "4"):
        raise CutError("--registration must be 3 or 4")
    if o.proof and o.registration != "4":
        raise CutError("--proof needs the four L-marks (-r 4): the proof cuts trace them")
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

    # 2. The machine's fixed cut offset, measured with deckbox's calibration sheets (pnpm calib).
    cfg_x, cfg_y = read_cut_offset()
    bias = (cfg_x if o.x_off is None else o.x_off, cfg_y if o.y_off is None else o.y_off)

    frame = session.Frame(
        page_w=geom.page_w_mm,
        page_h=geom.page_h_mm,
        inset=geom.reg_inset_mm,
        width=geom.reg_x_mm,
        height=geom.reg_y_mm,
        length=o.reg_length,
        thickness=o.reg_thickness,
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
    log_path = o.out_dir / f"{name}.session.jsonl"
    log = session.SessionLog(log_path, echo=lambda m: print(f"cut-proxies: {m}"))
    try:
        t = driver.open_transport(o.connection, o.ble_name, log, o.dry_run)
        if o.probe:
            report = driver.probe(frame, t, log, starts)
            print(probe_table(report["replies"]))
            print(f"cut-proxies: probe done; log {log_path}")
            return 0
        confirm = None
        if o.proof:
            confirm = dry_confirm if o.dry_run else ask_operator
        result = driver.cut(job, t, log, starts, confirm)
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


PROOF_HELP = """\
cut-proxies: proof cuts are down, on the marks: each mark's L scored along its centre lines,
with a gap across each leg where the sensor reads it. Lift the lid and look at all four
corners. Registered right, every cut runs down the middle of the black and ends where the leg
ends. A misread corner's cuts sit beside its lines: how far beside is the misread.
    y  every cut on its mark: cut the job
    r  a cut is off its mark: scan the marks again (the new cuts go on the same marks)
    q  stop: nothing more is cut, the head goes home"""


def ask_operator(attempt: int, rescan: bool) -> str:
    """Asked on the terminal between the proof cuts and the cut (cameo.driver.prove)."""
    if attempt == 0:
        print(PROOF_HELP)
    else:
        print(
            f"cut-proxies: round {attempt + 1}'s cuts went on the same marks: a cut beside a mark that "
            "wasn't there before is this round's."
        )
    choices = "y/r/q" if rescan else "y/q"
    while True:
        a = input(f"cut-proxies: every cut on its mark? [{choices}] ").strip().lower()[:1]
        if a in ("y", "q") or (rescan and a == "r"):
            return a


def dry_confirm(attempt: int, rescan: bool) -> str:
    print("cut-proxies: dry run: proof cuts answered y")
    return "y"


def probe_table(replies: dict[str, list[str | None]]) -> str:
    rows = [f"{'query':6} {'before scan':24} {'after scan':24}"]
    for q, (before, after) in replies.items():
        mark = "  <- changed" if before != after else ""
        rows.append(f"{q:6} {before!s:24} {after!s:24}{mark}")
    return "\n".join(rows)
