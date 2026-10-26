"""Cut a printed sheet on the Cameo 5 Alpha with our own driver (``mtgproxy.cameo``).

Every sheet has 3-mark registration (the square top-left, L marks top-right and bottom-left),
scanned with TB123. The mark frame comes from the same layouts.json that placed the marks in the
PDF, and the blade settings come from a preset (laminate or plain paper), each overridable by a
plain flag.
"""

from __future__ import annotations

import json
import time
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
    # Jonas's proven proxy preset: 130 gsm glossy in 80 µm pouches, and the laminated deck box. Depth 5
    # cut four laminated 3x3 sheets cleanly in a row, where depth 4 left spots attached (2026-10-25).
    "laminate": Preset(force=20, speed=25, depth=5, passes=3),
    # Plain copier paper, cut cleanly through (calibration sheets and deck-box outlines, 2026-09-28).
    "paper": Preset(force=10, speed=5, depth=1, passes=1),
}

# The blade for an SVG's scored lines (cameo.geometry.SCORE): one pass before the cut, meant to mark
# a laminated sheet's top film along each fold and leave the sheet whole. A guess, untested on the
# machine: Jonas's 220 gsm deck box in an 80 µm pouch cuts through at force 33, depth 6, 3 passes,
# and at force 20 even depth 9 stopped short in 3, so one pass at 15 should stay well inside it.
# The speed is the cut's unless --score-speed says otherwise.
SCORE_FORCE = 15
SCORE_DEPTH = 3


@dataclass
class CutOptions:
    paper: str = "a4"
    card_size: str = "standard"
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
    layout: str = (
        layout.DEFAULT
    )  # 3x3 | 4x2 (layout.py): which of the engine's layouts the sheet was printed in
    # The printed marks (mm). Leg length None = the layout's: 9.4 on the card-maker's 4x2 A4 (and
    # deckbox's), 6 on its 3x3.
    reg_length: float | None = None
    reg_thickness: float = 1.0
    # Mark corners' distance from the paper edges (mm). None = the layout's (10 for the card-maker's
    # 4x2 A4; 3.5 from the sides and 9 from the top and bottom on its 3x3).
    reg_inset: float | None = None
    out_dir: Path = field(default_factory=lambda: ROOT / "output" / "cut")
    label: str | None = None  # deck name, for log/transcript file names
    probe: bool = False  # scan and query the machine, cut nothing (see cameo.driver.probe)
    sense: bool = False  # scan, then read the marks back with the sensor; cut nothing (cameo.sensing)
    check: bool = True  # read the marks with the sensor before and after the scan; stop on a misread
    diagnose: bool = False  # read across every mark before any scan and say what they show; cut nothing
    proof: bool = False  # a proof cut around a printed target after the scan; the job waits for y / r / q
    # The sheet's proof targets' centres (page mm, y down) and side: None = the layout's, which an
    # --svg sheet doesn't have. The first proof_from are cut already: a run on this sheet that cuts
    # one moves it on, so a retry takes the next (cameo.proof).
    proof_targets: list[tuple[float, float]] | None = None
    proof_target_size: float | None = None
    proof_from: int = 0
    proof_blade: tuple[int, int, int] | None = None  # (force, speed, depth); None = cameo.proof.BLADE
    scan_starts: list[tuple[float, float]] | None = None  # (top, left) mm; None = the driver's defaults
    paper_at: tuple[float, float] = (0.0, 0.0)  # (right, down) mm: the paper's corner from the grid's
    # The SVG's scored lines (cameo.geometry.SCORE), cut first in one pass; False leaves them out.
    scores: bool = True
    score_force: int = SCORE_FORCE
    score_speed: int | None = None  # None = the cut's speed
    score_depth: int = SCORE_DEPTH
    pass_by_pass: bool = False  # every path once, then every path again, `passes` times


def scan_starts(o: CutOptions, frame) -> tuple[list[tuple[float, float]], str]:
    """The starts to try in order, and where the first came from: ``--scan-start`` as given (it is
    in the loaded mat's frame already), else the one start 2 mm or more into the square, moved
    with the paper (``driver.default_start``). TB123's area search covers the millimetre or two the
    marks move from one load to the next."""
    from .cameo import driver

    if o.scan_starts:
        return o.scan_starts, "--scan-start"
    top, left = driver.default_start(frame)
    return [(top, left)], f"{top - frame.on_mat(frame.top, 0)[0]:g} mm into the square"


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
        reg_inset_y_mm=None,
    )


def frame_of(
    geom: layout.Geometry,
    reg_length: float | None = None,
    reg_thickness: float = 1.0,
    paper_at: tuple[float, float] = (0.0, 0.0),
    targets: tuple = (),
):
    """The printed marks as the machine is told them (``session.Frame``), from a layout's geometry;
    ``reg_length`` None = the layout's leg length, ``paper_at`` where the paper sits on the mat,
    ``targets`` the proof targets the rounds can take (``sheet_targets``)."""
    from .cameo import session

    return session.Frame(
        page_w=geom.page_w_mm,
        page_h=geom.page_h_mm,
        inset=geom.reg_inset_mm,
        width=geom.reg_x_mm,
        height=geom.reg_y_mm,
        length=geom.reg_length_mm if reg_length is None else reg_length,
        thickness=reg_thickness,
        inset_y=geom.reg_inset_y_mm,
        paper_at=paper_at,
        targets=targets,
    )


def sheet_targets(o: CutOptions, geom: layout.Geometry) -> tuple:
    """The proof targets printed on the sheet, numbered in the order the rounds take them, less the
    first ``proof_from``: ``--proof-target``'s, else the layout's (layouts.json), which an --svg sheet
    doesn't have, since the engine didn't print it."""
    from .cameo import proof

    if o.proof_targets is not None:
        centres, size = o.proof_targets, o.proof_target_size or proof.SIZE
    elif o.svg is None:
        centres, size = geom.proof_targets, o.proof_target_size or geom.proof_target_mm or proof.SIZE
    else:
        centres, size = (), proof.SIZE
    return proof.targets(centres, size)[o.proof_from :]


def no_targets(o: CutOptions, geom: layout.Geometry) -> str:
    """Why --proof has no target to cut on this sheet, for the message."""
    printed = len(o.proof_targets if o.proof_targets is not None else () if o.svg else geom.proof_targets)
    if printed:
        return (
            f"all {printed} proof targets on this sheet are cut already (--proof-from {o.proof_from}): "
            "cut it without --proof; the sensor check still reads every mark"
        )
    if o.svg is not None:
        return (
            f"{o.svg.name} has no proof targets: give each one printed on the sheet as --proof-target X,Y "
            "(its centre, page mm from the top-left corner), or cut without --proof"
        )
    return f"the {o.layout} layout on {o.paper} prints no proof targets: cut without --proof"


def ble_scan() -> int:
    from .cameo.transport import BleTransport

    found = BleTransport.discover()
    print(f"Found {len(found)} Bluetooth LE device(s):")
    for address, name in found:
        print(f"    {address}   {name or '(unnamed)'}")
    return 0


def check(o: CutOptions) -> None:
    """Refuse options that can't describe a printed sheet: an unknown layout, or one with marks or
    paper it never prints."""
    try:
        layout.check(o.layout, o.paper, o.card_size)
    except layout.LayoutError as e:
        raise CutError(str(e)) from e


def run_cut(o: CutOptions) -> int:
    from .cameo import driver, protocol, session
    from .cameo.transport import TransportError

    check(o)
    o.out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Cut geometry from the card-maker's own layout engine (same source as the PDF).
    if o.svg is None:
        name = o.label or f"{o.paper}-{o.card_size}-{o.layout}"
        svg = o.out_dir / f"{name}.svg"
        geom = layout.build_cut_svg(o.paper, o.card_size, svg, o.layout)
    else:
        if not o.svg.is_file():
            raise CutError(f"SVG not found: {o.svg}")
        svg, name = o.svg, o.svg.stem
        geom = layout.build_cut_svg(o.paper, o.card_size, None, o.layout)
    if o.reg_inset is not None:
        geom = with_inset(geom, o.reg_inset)

    # 2. The machine's fixed cut offset, measured with deckbox's calibration sheets (pnpm calib).
    cfg_x, cfg_y = read_cut_offset()
    bias = (cfg_x if o.x_off is None else o.x_off, cfg_y if o.y_off is None else o.y_off)

    targets = sheet_targets(o, geom) if o.proof else ()
    if o.proof and not targets and not (o.probe or o.sense or o.diagnose):
        raise CutError(no_targets(o, geom))
    frame = frame_of(geom, o.reg_length, o.reg_thickness, o.paper_at, targets)
    try:
        blade = protocol.Blade(o.force, o.speed, o.depth)
        score = None
        if o.scores:
            speed = o.speed if o.score_speed is None else o.score_speed
            score = protocol.Blade(o.score_force, speed, o.score_depth)
        job = driver.plan(svg, frame, blade, o.passes, bias, score, o.pass_by_pass)
    except (ValueError, session.CutError) as e:
        raise CutError(str(e)) from e
    starts, origin = scan_starts(o, frame)
    what = f"{geom.cards} cards ({geom.layout})" if o.svg is None else svg.name
    via = "nothing (dry run)" if o.dry_run else o.connection
    print(
        f"cut-proxies: {name} — {what}, 3-mark registration "
        f"(marks inset {frame.inset:g} mm"
        + ("" if frame.inset_y is None else f", {frame.inset_y:g} mm top and bottom")
        + f", {frame.width:g} x {frame.height:g} mm apart, "
        f"{frame.length:g} x {frame.thickness:g} mm)"
    )
    print(
        f"cut-proxies: force {o.force} · speed {o.speed} · depth {o.depth} · passes {o.passes}"
        + (", pass by pass" if o.pass_by_pass else "")
        + f" · offset x={bias[0]:g}mm y={bias[1]:g}mm · via {via}"
    )
    if job.scores:
        b = job.score_blade
        print(
            f"cut-proxies: {len(job.scores)} scored line(s) first: force {b.force} · speed {b.speed} · "
            f"depth {b.depth} · one pass"
        )
    elif not o.scores:
        from .cameo import geometry

        uncut = len(geometry.load_kinds(svg)[1])
        if uncut:
            print(f"cut-proxies: {uncut} scored line(s) left uncut (--no-scores)")
    if frame.paper_at != (0, 0):
        print(f"cut-proxies: the paper's top-left corner {frame.placed()}")
    proof_blade = proof_blade_of(o)
    if o.proof and targets:
        b, first = proof_blade, targets[0]
        print(
            f"cut-proxies: proof: {len(targets)} target(s) left on the sheet, {first.size:g} mm, the first "
            f"at {first.x:g}, {first.y:g} mm; each cut one pass at force {b.force} · speed {b.speed} · "
            f"depth {b.depth}"
        )
    top, left = starts[0]
    more = f"; a miss tries {len(starts) - 1} more around it" if len(starts) > 1 else ""
    print(f"cut-proxies: scan starts at ({top:g}, {left:g}) mm, {origin}{more}")
    # One log per run: a batch of sheets used to overwrite it, losing the runs before a bad one.
    log_path = o.out_dir / f"{name}.{time.strftime('%Y%m%d-%H%M%S')}.session.jsonl"
    log = session.SessionLog(log_path, echo=lambda m: print(f"cut-proxies: {m}"))
    try:
        t = driver.open_transport(o.connection, o.ble_name, log, o.dry_run)
        if o.probe:
            report = driver.probe(frame, t, log, starts)
            print(probe_table(report["replies"]))
            print(f"cut-proxies: probe done; log {log_path}")
            return 0
        if o.diagnose:
            seen, flat = driver.diagnose(frame, t, log)
            print(diagnose_report(seen, frame, flat))
            print(f"cut-proxies: diagnose done; log {log_path}")
            return 0
        if o.sense:
            from .cameo import sensing

            report = driver.sense(frame, t, log, starts, sensing.profiles(frame))
            print(sense_table(report["readings"]))
            print(f"cut-proxies: sensing done; log {log_path}")
            return 0
        confirm = None
        if o.proof:
            confirm = dry_confirm if o.dry_run else ask_operator
        if o.check and o.dry_run:
            print("cut-proxies: dry run: the mark check is skipped (it needs the machine's sensor)")
        start_from = o.proof_from

        def cut_target(target) -> None:  # a retry on this sheet takes the next one
            o.proof_from = target.number

        result = driver.cut(
            job,
            t,
            log,
            starts,
            confirm,
            check=o.check and not o.dry_run,
            proof_blade=proof_blade,
            on_target=cut_target,
        )
    except (session.CutError, TransportError) as e:
        note = "" if o.proof_from == start_from else proof_note(o)
        raise CutError(f"{e}\ncut-proxies: session log: {log_path}{note}") from e
    finally:
        log.close()
    x0, y0, x1, y1 = result.extent
    print(f"cut-proxies: cut spans x {x0:.2f}..{x1:.2f}, y {y0:.2f}..{y1:.2f} mm from the top-left mark")
    if o.dry_run:
        transcript = o.out_dir / f"{name}.cmds"
        transcript.write_bytes(bytes(t.sent))
        print(f"cut-proxies: dry run OK — transcript {transcript}, session log {log_path}")
        return 0
    print(f"cut-proxies: done — {what} cut. Don't eject yet: lift a corner and check it's through.")
    if job.scores:
        print("cut-proxies: its folds are scored: another pass wants --no-scores, or they are scored again.")
    return 0


def proof_blade_of(o: CutOptions):
    """The proof cut's blade: ``--proof-blade``'s, else cameo.proof.BLADE."""
    from .cameo import proof, protocol

    return proof.BLADE if o.proof_blade is None else protocol.Blade(*o.proof_blade)


def proof_note(o: CutOptions) -> str:
    """How far this sheet's proof targets are cut, for a rerun on it."""
    n = o.proof_from
    which = "proof target 1 on this sheet is" if n == 1 else f"proof targets 1-{n} on this sheet are"
    return f"\ncut-proxies: {which} cut: a rerun on it takes --proof-from {n}"


def wait_for_next_sheet(o: CutOptions, interrupted) -> bool:
    """Connect and watch the mat: True once it has been unloaded and loaded again, False when
    ``interrupted`` (called between polls with the wait in seconds) returns True."""
    from .cameo import driver, session
    from .cameo.transport import TransportError

    log = session.SessionLog(None)
    try:
        t = driver.open_transport(o.connection, o.ble_name, log, o.dry_run)
        try:
            return driver.wait_for_reload(t, log, interrupted)
        finally:
            t.close()
    except TransportError as e:
        raise CutError(f"can't watch the mat ({e})") from e


PROOF_HELP = """\
cut-proxies: the proof cut is down: the blade has traced the edge of the small black square named
above, one of the proof targets printed with the marks. Lift the lid and look at it. Registered
right, the cut runs along the edge of the black all round. Registered off, white shows between
the black and the cut on one side, and the cut runs inside the black on the other: the width of
the white is how far off, its side which way.
    y  the cut on the square's edge all round: cut the job
    r  white between the black and the cut: scan the marks again (the next round cuts the next square)
    q  stop: nothing more is cut, the head goes home"""


def ask_operator(round_: int, rescan: bool) -> str:
    """Asked on the terminal between the proof cut and the cut (cameo.driver.prove)."""
    if round_ == 0:
        print(PROOF_HELP)
    else:
        print(f"cut-proxies: round {round_ + 1}'s cut went on the next square: look at that one.")
    choices = "y/r/q" if rescan else "y/q"
    while True:
        a = input(f"cut-proxies: the cut on the square's edge all round? [{choices}] ").strip().lower()[:1]
        if a in ("y", "q") or (rescan and a == "r"):
            return a


def dry_confirm(round_: int, rescan: bool) -> str:
    print("cut-proxies: dry run: the proof cut answered y")
    return "y"


def probe_table(replies: dict[str, list[str | None]]) -> str:
    rows = [f"{'query':6} {'before scan':24} {'after scan':24}"]
    for q, (before, after) in replies.items():
        mark = "  <- changed" if before != after else ""
        rows.append(f"{q:6} {before!s:24} {after!s:24}{mark}")
    return "\n".join(rows)


def sense_table(readings) -> str:
    """Each profile's readings in order: offset (mm from the line it crosses), level, a bar."""
    rows, last = [], None
    for pt, level in readings:
        if pt.profile != last:
            rows.append(f"{pt.profile}:")
            last = pt.profile
        bar = "#" * round((level or 0) / 255 * 40)
        rows.append(f"  {pt.offset:+6.2f} mm  {'?' if level is None else level:>4}  {bar}")
    return "\n".join(rows)


def diagnose_report(seen, frame, flat=None) -> str:
    """The verdict first (with ``flat``, the blank paper beside the marks too), then each row:
    where the mark is (+ right or down), its peak, the paper beside it, and the readings as bars."""
    from .cameo import sensing

    at = "" if frame.paper_at == (0, 0) else f", the paper's corner {frame.placed()}"
    rows = [f"cut-proxies: before any scan, in the loaded mat's frame (page mm{at}):"]
    rows += [f"  {line}" for line in sensing.verdict(seen, frame, flat)]
    for s in seen:
        where = "no clear mark" if s.shift is None else f"{s.shift:+.2f} mm"
        paper = "?" if s.paper_range is None else f"{s.paper_range[0]}-{s.paper_range[1]}"
        rows.append(f"{s.row.mark}, {s.row.what}: {where}, peak {s.peak}, paper {paper}")
        for pt, level in zip(s.row.points, s.levels, strict=True):
            bar = "#" * round((level or 0) / 255 * 40)
            rows.append(f"  {pt.offset - s.row.middle:+5.2f} mm  {'?' if level is None else level:>4}  {bar}")
    return "\n".join(rows)
