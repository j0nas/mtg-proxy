"""Entry points: plan a job from an SVG, open a transport, cut, or probe the registration.

``cut`` can stop between the scan and the cut for a proof cut around a printed target (``proof``),
which the operator checks by eye before anything else is cut. ``probe`` is an experiment, not a cut: it scans the marks and asks the machine every query that
might report where it found them, before and after the scan. Run it twice with the sheet moved a
known distance; a reply that moves with the sheet is a readback we can check against tolerances.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from . import geometry, proof, protocol, sensing
from .session import CutError, Frame, Job, Session, SessionLog, check_bounds, far_corner, low_corner
from .transport import BleTransport, RecordingTransport, Transport, UsbTransport

FIRMWARE = b"CAMEO 5 ALPHA V1.04    \x03"
DRY_ANSWERS = {
    b"\x1b\x05": b"0\x03",  # ready
    b"FG": FIRMWARE,
    b"TB123": b"    0\x03",  # marks found (a scan result is padded to five characters)
    b"TB60": b"    0\x03",  # the scan's light slot
    b"TB62": b"  120\x03",
    b"TB63": b"   40\x03",
}

# Queries that might report registration state; the probe logs each reply before and after a
# scan. On firmware V1.04 FQ5 changes with the scan (0 → -64 on a straight sheet) and [ / U report
# the cutting area's corners; the GP-GL O* outputs get no reply at all, so they are left out.
PROBE_QUERIES = ("FQ0", "FQ1", "FQ2", "FQ3", "FQ4", "FQ5", "FQ6", "FQ7", "FQ8", "FQ9", "[", "U", "TB71", "FA")


LOWEST = 1.0  # mm: no start above or left of this
BACKUP = 7.0  # mm the head backs up above the search row that caught the square, to measure it down
EDGE_CLEAR = 1.5  # mm inside the paper's top edge that measuring pass must start


def default_start(frame: Frame) -> tuple[float, float]:
    """Where the mark search starts, (top, left) mm from the origin: 2 mm below the square's top
    edge, or lower so the pass that measures the square starts EDGE_CLEAR inside the paper, and 6
    mm left of it, or as far left as LOWEST allows from the paper's edge, then moved with the paper
    (``Frame.on_mat``). (11, 1) on a 3x3 sheet (the square at 3.5-8.5 mm across, 9-14 mm down),
    (12, 4) on a 4x2 sheet (10-15 mm both ways), (8.5, 1) on the 5 mm deck box, (11.5, 4) with its
    pouch on the tack and the paper 3 mm in both ways.

    Once a search row catches the square, the head backs up BACKUP above that row and measures the
    square's middle down from there (cameo-re NOTES.md, "Locating it"). On 2026-10-26 the deck box
    started 2 mm into its square, 7 mm below the paper's top edge: that pass began on the edge,
    which a laminated sheet's pouch shows as dark as ink, and every scan, TB123 twice and TB122,
    found the square and failed to measure it (reply -1). Starting 3.5 mm into the square, the pass
    starts 1.5 mm inside the paper.

    The machine finds the square by an area search: rows 3 mm apart from the start to 30 mm below
    it, each sweeping 30 mm to its right, for a dark run 3.5-9 mm long (firmware V1.04, cameo-re
    NOTES.md). It then measures the square down and across. Starting 2 mm into the square, the
    first row runs through it. The sweep has to cross the square's left edge from white paper:
    on plain A4 with the square 10 mm in (2026-10-24), starts 2-6 mm in from the left found it
    and starts at 11-13 mm, on or past the square, never did (reply 1). The square must print
    exactly 5 mm (the engine fork's cd4166a): at 6 mm the machine found it and then failed to
    measure it (reply -1) from every start.

    On laminated 3x3 sheets (2026-10-25) a start 2 mm above the square, near the paper's edge,
    took the firmware's second try (28 s) and registered off the top of the sheet, while a start
    2 mm into it found the marks first time twice (13-14 s) and then registered four sheets cut in
    a row. A 4x2 sheet's start follows that: (8, 4), 2 mm above its square, registered once, on
    plain A4 (2026-10-24); (12, 4) has not been tried on the machine."""
    top = max(frame.top + 2, min(BACKUP + EDGE_CLEAR, frame.top + proof.SQUARE - 1.5))
    return frame.on_mat(top, max(LOWEST, frame.inset - 6))


def plan(
    svg: Path,
    frame: Frame,
    blade: protocol.Blade,
    passes: int,
    bias: tuple[float, float],
    score_blade: protocol.Blade | None = None,
    pass_by_pass: bool = False,
) -> Job:
    """The job an SVG describes. Its scores (``geometry.SCORE`` strokes) are scored once with
    ``score_blade`` before anything is cut, or left out when it is None (a sheet scored already).
    The cuts go ``passes`` times: each closed loop's laps in a row, or with ``pass_by_pass`` every
    path once, then every path again. A score blade at least as heavy as the cut's in both force
    and depth is refused: its one pass would go as deep as a pass of the cut."""
    cuts, scores = geometry.load_kinds(svg)
    if score_blade is None:
        scores = []
    if not cuts and not scores:
        raise CutError(f"{svg} has no paths to cut")
    if scores and score_blade and score_blade.force >= blade.force and score_blade.depth >= blade.depth:
        raise CutError(
            f"the score blade (force {score_blade.force}, depth {score_blade.depth}) is no lighter than the "
            f"cut's (force {blade.force}, depth {blade.depth}): the scored folds would cut like the outline. "
            "Give --score-force or --score-depth below the cut's"
        )
    start = (frame.inset, frame.top)
    ordered = geometry.order(cuts, start=start)
    folds = geometry.order(scores, start=start)
    lines = geometry.passes(ordered, 1 if pass_by_pass else passes)
    job = Job(
        lines,
        frame,
        blade,
        bias,
        shapes=[*folds, *ordered],
        scores=geometry.passes(folds, 1),
        score_blade=score_blade if scores else None,
        repeat=passes if pass_by_pass else 1,
    )
    check_bounds(job)
    return job


def open_transport(connection: str, ble_name: str, log: SessionLog, dry_run: bool) -> Transport:
    if dry_run:
        return RecordingTransport(log, DRY_ANSWERS)
    if connection == "usb":
        return UsbTransport(log)
    return BleTransport.connect(ble_name, log)


@dataclass
class Result:
    firmware: str
    start: tuple[float, float]
    extent: tuple[float, float, float, float]


# The operator's answer to a round of proof cuts: (round, rescan offered) → "y" cut, "r" rescan,
# anything else stops.
Confirm = Callable[[int, bool], str]


def cut(
    job: Job,
    t: Transport,
    log: SessionLog,
    starts: list[tuple[float, float]],
    confirm: Confirm | None = None,
    check: bool = False,
    proof_blade: protocol.Blade = proof.BLADE,
    on_target: Callable[[proof.Target], None] | None = None,
) -> Result:
    """The whole job. Once anything moved the machine, it always ends back at the origin.

    With ``check``, the sensor finds the square before the scan and every reading and the scan's
    start follow where it lies (``check_placement``), reads blank paper beside each mark (does the
    sheet lie on the mat there? ``check_flat``) and every mark after the scan (does the print agree
    with the frame the scan made?). A scan that finds nothing or disagrees with the print is tried
    again, then once more as TB122, which skips the top-right L (SCANS); after the first that finds
    nothing, the sensor measures the square again (``locate``) and the next scan starts from where it
    lies; when none agrees, the job stops before anything is cut (``sensing``). A sheet lifting off
    the mat stops the job as soon as the readings show it, with no rescan (``sensing.press_down``).
    With ``confirm``, each proof round traces the next of the frame's proof targets with
    ``proof_blade`` between the scan and the cut (``prove``), and nothing else is cut until the
    operator says so; ``on_target`` hears of each target as it is cut. A rescan returns to the
    origin and repeats a fresh run's start."""
    s = Session(t, log)
    try:
        extent = reach = check_bounds(job)
        targets: list[proof.Target] = []
        if confirm:  # the area must hold the targets' cuts too, which may reach past the job's own
            targets = proof_targets(job, log)
            reach = check_bounds(replace(job, lines=[*job.lines, *(tg.outline() for tg in targets)]))
        if check:  # and the paper points beside the marks
            points = [sensing.registered(job.frame, c) for c in sensing.checks(job.frame)]
            reach = (
                min(reach[0], *(x for _, x in points)),
                min(reach[1], *(y for y, _ in points)),
                *reach[2:],
            )
        low = low_corner(reach)
        proofs = min(proof.ROUNDS, len(targets))
        rounds = max(proofs, CHECK_ROUNDS if check else 1)
        done = 0  # proof rounds cut
        remeasure = measured = False  # after a scan that found nothing, once
        for attempt in range(rounds):
            if attempt:
                s.finish()
                s.wait_ready(60)
            firmware = s.preflight()
            log.say(f"connected: {firmware} via {t.name}, mat loaded")
            if check and attempt == 0:
                slot = check_light_slot(s, log)
            s.prepare()
            try:
                if remeasure:
                    remeasure = False
                    off = locate(s, job.frame, log, halvings=REMEASURE_HALVINGS)
                    if off is None:
                        log.note("the square's middle doesn't read ink: scanning from the same start")
                    elif max(abs(off[0]), abs(off[1])) < MEASURE_MOVE:
                        log.note("the square measures where the frame puts it")
                    else:
                        before = job.frame
                        job = replace(job, frame=moved(job.frame, off))
                        starts = follow(starts, before, job.frame)
                        log("measured", dx=off[0], dy=off[1], paper_at=job.frame.paper_at)
                        log.note(
                            f"square measured {_off(off[0], 'right', 'left')} and {_off(off[1], 'down', 'up')} "
                            f"of where the frame put it: the paper's corner is {job.frame.placed()}; scanning "
                            "from there"
                        )
                if check and attempt == 0:
                    placed = check_placement(s, job.frame, log)
                    starts = follow(starts, job.frame, placed)  # the square sits off: so does the sheet
                    job = replace(job, frame=placed)
                    flat = check_flat(s, job.frame, log)
            except CutError:
                s.send(protocol.select_light(slot))  # the next job's scan, checked or not, gets its light
                raise
            if check:  # the readings leave the light wherever their ramp stopped: back to the scan's level
                s.send(protocol.select_light(slot))
            marks = SCANS[min(attempt, len(SCANS) - 1)] if check else 3
            if marks == 2:
                log.say(
                    "two-mark scan (TB122): the square and the bottom-left L; the check reads the top-right L"
                )
            try:
                start = s.register(job.frame, starts, marks)
            except CutError as e:
                if check and attempt + 1 < rounds:
                    remeasure, measured = not measured, True
                    then = "measuring the square, then scanning again" if remeasure else "scanning again"
                    log.say(f"{e.args[0].split('. ')[0]}: {then}")
                    continue
                raise
            if low != (0.0, 0.0) and attempt == 0:
                log.say(
                    f"cut reaches {max(0.0, -reach[1]):.2f} mm above / {max(0.0, -reach[0]):.2f} mm left of the "
                    f"top-left mark: cutting area widened to {low[0]:g}, {low[1]:g} mm"
                )
            s.setup(job.blade, job.frame, low)
            if check:
                seen: dict[tuple[str, str], int | None] = {}
                failed = check_marks(s, job.frame, log, flat, seen=seen)
                s.send(protocol.select_light(slot))  # for the rescan, or the next sheet's scan
                if failed:
                    for line in failed:
                        log.say(f"check: {line}")
                    beside = {what: level for (mark, what), level in seen.items() if mark == "top-left"}
                    if sensing.lifts_by_square(beside, flat.reference):
                        where = ("to its left", "to its right", "above it", "below it")
                        *each, last = (
                            f"{beside.get(w)} {at}"
                            for w, at in zip(sensing.BESIDE_SQUARE, where, strict=True)
                        )
                        raise CutError(
                            sensing.press_down(
                                ["top-left"],
                                f"blank paper beside the square reads {', '.join(each)} and {last}, against "
                                f"{flat.reference} in the middle of the top margin",
                            )
                        )
                    if attempt + 1 < rounds:
                        log.say("the marks don't read where the scan put them: scanning again")
                        continue
                    raise CutError(
                        f"the marks don't read where the scan put them, after {rounds} scans: nothing was cut. "
                        f"Check that the sheet lies flat with its paper's corner {job.frame.placed()} and "
                        "its marks are clean, then rerun; cut-proxies --diagnose shows what the sensor sees "
                        "at each mark"
                    )
                log.say(
                    f"check: every mark reads where the scan put it ({len(sensing.checks(job.frame))} readings)"
                )
            if confirm is None:
                break
            done += 1
            rescan = attempt + 1 < rounds and done < proofs
            target = targets[done - 1]
            end = target.cuts()[-1].points[-1]  # where the proof cut leaves the blade
            verify = partial(held, s, job.frame, log, flat, slot, end) if check else None
            if prove(s, job, log, confirm, target, done, rescan, verify, low, proof_blade, on_target):
                break
        # No reading after the job's own cut: by then there is nothing left to guard. Until
        # 2026-10-28 every checked job read five marks' ink once it was cut (about 27 s); in the 13
        # cuts in output/cut it never failed, and a further pass rescans the marks before it cuts
        # (cli's "one more pass").
        between = None
        if check and job.scores:
            between = partial(kept_place, s, job.frame, log, flat, slot, job.scores[-1].points[-1])
        s.cut_job(job, low, between)
        return Result(firmware, start, extent)
    finally:
        wrap_up(s, t, log)


def proof_targets(job: Job, log: SessionLog) -> list[proof.Target]:
    """The frame's proof targets the rounds can take, in order: one whose cut would touch the job is
    left out, and said so. With none left the job stops before the machine moves."""
    sheet = job.frame.targets
    kept, touching = proof.clear_of(sheet, job.shapes or job.lines)
    of = max((tg.number for tg in sheet), default=0)
    for tg in touching:
        log.say(f"proof: {tg.name(of)} would touch the job, so it is left out")
    if not kept:
        why = "every one would touch the job" if sheet else "the sheet has none"
        raise CutError(f"no proof target to cut ({why}): nothing was cut. Rerun without --proof")
    return kept


def held(
    s: Session,
    frame: Frame,
    log: SessionLog,
    flat: sensing.Flatness,
    slot: int,
    near: tuple[float, float] | None = None,
) -> list[str]:
    """After the blade has cut: are the legs of the L nearest ``near`` (page mm, where the blade
    stopped) still ink where the frame puts them? What failed, one line each. Only ink: a cut groove
    beside a mark reads dark, so it can fake ink but never paper, and a frame that slipped reads
    paper on a leg's centre line.

    On 2026-10-26 a second pass on the Aminatou box (force 30, speed 10, depth 8, the depth changed
    from 6) cut 2.5 mm left of the first everywhere, after every reading before it had agreed with
    the marks: the carriage slipped once, before or at its first cut, and the whole outline moved
    with it. A slip moves the whole frame, so one vertical leg sees it in x and one horizontal leg in
    y, to half a millimetre; until 2026-10-28 this read the square and both Ls (five ink readings,
    about 28 s), and after the 5 proof cuts and 7 scorings in output/cut it never failed. The
    square's middle sees only a slip of 2.5 mm or more, and the far L the same slip again."""
    failed = check_marks(s, frame, log, flat, checks=sensing.legs(frame, near))
    s.send(protocol.select_light(slot))
    return failed


def kept_place(
    s: Session,
    frame: Frame,
    log: SessionLog,
    flat: sensing.Flatness,
    slot: int,
    near: tuple[float, float] | None = None,
) -> None:
    """Between a job's scores and its cuts: the marks' ink still where the frame puts it (``held``,
    the L nearest ``near``, where the scores ended), or nothing more is cut. The blade changes
    depth there (the score's to the cut's), and on 2026-10-26 the one run that changed depth
    mid-session cut 2.5 mm off."""
    lost = held(s, frame, log, flat, slot, near)
    if lost:
        raise CutError(
            f"after the scores the marks no longer read where the scan put them ({'; '.join(lost)}): the "
            "machine lost its place changing blades, so nothing else was cut"
        )
    log.say("check: the marks still read where the scan put them after the scores")


# The scans a checked job tries, in order, until one agrees with the print. TB122 reads only the square
# and the bottom-left L: on 2026-10-26 a double-sided sheet whose back showed through behind its
# top-right L misread on every TB123 and registered first time as TB122, the check confirming the L.
SCANS = (3, 3, 2)
CHECK_ROUNDS = len(SCANS)


def check_light_slot(s: Session, log: SessionLog) -> int:
    """The slot the scan takes its light from; refuses to read the marks when it is the one TB63
    overwrites. Sets the light to that slot's level, undoing a reading an earlier job left."""
    slot = protocol.number(s.query(protocol.cmd(protocol.LIGHT_SLOT)))
    log("light", selected=slot, slot=SENSE_SLOT)
    if slot is None or slot == SENSE_SLOT:
        raise CutError(
            f"the scan's light slot reads {slot!r}: reading the marks would overwrite it. Power-cycle the Cameo"
        )
    s.send(protocol.select_light(slot))
    return slot


def read_loaded(s: Session, frame: Frame, pt: sensing.Point | sensing.Check) -> tuple[int | None, str | None]:
    """A reading before the scan, at page point ``pt`` moved onto the loaded mat (``Frame.on_mat``).
    Every reading before the scan goes through here; after it, ``sensing.registered``."""
    return s.sense(*frame.on_mat(pt.y, pt.x), SENSE_SLOT)


def moved(frame: Frame, off: tuple[float, float]) -> Frame:
    """``frame`` with the paper ``off`` (right, down) mm further on the mat."""
    right, down = frame.paper_at
    return replace(frame, paper_at=(round(right + off[0], 2), round(down + off[1], 2)))


def follow(starts: list[tuple[float, float]], before: Frame, after: Frame) -> list[tuple[float, float]]:
    """The scan starts (top, left) moved as the paper moved from ``before`` to ``after``."""
    dx = after.paper_at[0] - before.paper_at[0]
    dy = after.paper_at[1] - before.paper_at[1]
    return starts if (dx, dy) == (0, 0) else [(top + dy, left + dx) for top, left in starts]


def check_placement(s: Session, frame: Frame, log: SessionLog) -> Frame:
    """Before the scan: where is the square? Its middle where the frame expects it, then blank paper
    right of it (``sensing.beside``), which the readings near the square are judged against
    (``sensing.thresholds``). Ink there, or dim ink DIM_INK above that paper, there or NUDGE either
    side, and the square's right and bottom edges say where it lies (``locate``); no ink, and the
    head hunts for it (``hunt``). Either way every later reading and the scan's start follow it
    there, in the frame this returns, and only the session log says how far it moved: a sheet lies a
    few millimetres from where it was laid every time, laid against the mat's edge, laminated and fed
    in, and that is no news to the operator. Found nowhere, a landscape sheet is read once more where
    the square lies when the sheet is loaded portrait, as it leaves the printer (``sensing.turned``):
    ink there says so. Either way nothing is scanned.

    Until 2026-10-28 a middle that read ink passed the frame unmoved, and so did a sheet up to 2.5
    mm off: the readings after it and the scan then assumed the frame. On 2026-10-28 two Betor runs
    stopped before the scan with 255 inside the bottom-left L, its own leg with the sheet 3 mm right;
    on 2026-10-26 and 27 three deck boxes 1.5-2.3 mm right of the frame had their scans start on the
    pouch's dark band past the paper's edge, and miss, 43 s each, until the square was measured.

    On 2026-10-26 a laminated deck box went on the mat as it came out of the printer, its square
    top-right. The millimetres in the stop are where the head read, on the mat."""
    c = sensing.placement(frame)
    level, reply = read_loaded(s, frame, c)
    log("placement", x=c.x, y=c.y, level=level, reply=reply)
    side = sensing.beside(frame)
    paper, reply = read_loaded(s, frame, side)
    log("placement paper", x=side.x, y=side.y, level=paper, reply=reply)
    local = paper if sensing.blank(paper) else None
    ink = level if c.reads(level, local) is True else None
    # Single readings inside the square swing (238-255 on the deck box's sheet): a dim one gets a
    # second try either side. A middle that reads like the paper beside it is off the square, and
    # a try at its edge would pass a sheet lying 2.5 mm off (2026-10-26): that one is looked for.
    dim = ink is None and level is not None and local is not None and level >= local + sensing.CONTRAST
    for k in (-1, 1) if dim else ():
        nl, reply = read_loaded(s, frame, c.nudged(k * sensing.NUDGE))
        log("placement", x=c.x + k * sensing.NUDGE, y=c.y, level=nl, reply=reply)
        if c.reads(nl, local) is True:
            ink = nl
            break
    if ink is not None:
        found = locate(s, frame, log, ink, paper)
        assert found is not None  # its middle read ink: it measures, or locate stops the job
        if max(abs(found[0]), abs(found[1])) < MEASURE_MOVE:
            found = (0.0, 0.0)
    else:
        # The middle missed the square, so it may be the paper, and the point right of it the square's
        # own dim ink (238-249 passes as blank) when the sheet lies 3.5 mm or more right: the paler.
        found = hunt(s, frame, log, min((v for v in (level, paper) if sensing.blank(v)), default=None))
    if found is None:
        t = sensing.turned(frame)
        if t is not None:
            t_level, t_reply = read_loaded(s, frame, t)
            log("placement_turned", x=t.x, y=t.y, level=t_level, reply=t_reply)
            if t.reads(t_level) is True:
                raise CutError(
                    "the sheet lies portrait, as it leaves the printer: turn it a quarter anticlockwise so "
                    "the square is top-left, reload and rerun"
                )
        my, mx = frame.on_mat(c.y, c.x)
        raise CutError(
            f"no square at {my:g} mm down, {mx:g} mm in on the mat, where it should be, nor within "
            f"{HUNT_RANGE[1]:g} mm of it any way: put the paper's top-left corner {frame.placed()}, reload "
            "and rerun"
        )
    there = frame if found == (0.0, 0.0) else moved(frame, found)
    log("located", dx=found[0], dy=found[1], paper_at=there.paper_at)
    log.note(
        f"square found {_off(found[0], 'right', 'left')} and {_off(found[1], 'down', 'up')} of where it "
        f"should be: the paper's corner is {there.placed()}"
    )
    return there


def _off(d: float, plus: str, minus: str) -> str:
    return f"{abs(d):g} mm {plus if d >= 0 else minus}"


HUNT_STEP = 3.0  # mm between the hunt's readings: under the square's 5 mm, so one lands on it
# mm the square's middle may lie from where the frame expects it, either way. The frame now
# expects the sheet where the cut logs found such sheets lying (cutting.LAMINATED_AT, the deck box's
# --paper-at), and Jonas lays one within about 3 mm of that, any way. A grid over it finds any
# square whose middle lies within HUNT_STEP / 2 + 1 mm past either end. Until 2026-10-28 it ran 3
# mm up and left to 6 mm down and right, for a frame that expected a pouch's paper 3 mm in, where
# every deck box lay further right.
HUNT_RANGE = (-4.5, 4.5)
BESIDE_HIT = 0.75  # mm past the right edge of any square holding a hunt's ink reading


def hunt(s: Session, frame: Frame, log: SessionLog, paper: int | None) -> tuple[float, float] | None:
    """Where the square sits from where the frame expects it, (right, down) mm, or None: readings on
    a HUNT_STEP grid over HUNT_RANGE, nearest the expected middle first, until one reads ink; from
    there ``measure`` halves its way to the square's top and left edges. An ink reading that isn't
    the square (the pouch's dark band past the paper's edge) has no paper within a square's width
    above or left of it, so it measures as nothing and the hunt goes on. ``paper`` is blank paper's
    level near the square; without it the lowest reading so far stands in.

    Before measuring, one reading BESIDE_HIT past the right edge of any square that could hold the
    hit: blank paper there, or the hit isn't the square. Dark there, it is the pouch's band along
    the paper's top edge or paper lifting off the mat, and nine to eleven readings are saved: on
    2026-10-28 a lifting Betor sheet measured ten such hits and hunted for 7 minutes.

    It replaced rows of readings a millimetre apart, 64 of them at about 4 s each, which took over
    four minutes on 2026-10-26 for a sheet lying 3 mm right: the grid finds a square that far off
    in a few readings, and measuring it takes nine."""
    c = sensing.placement(frame)
    lo, hi = HUNT_RANGE
    steps = [round(lo + i * HUNT_STEP, 4) for i in range(round((hi - lo) / HUNT_STEP) + 1)]
    grid = sorted(
        ((dx, dy) for dx in steps for dy in steps if (dx, dy) != (0.0, 0.0)),
        key=lambda d: (math.hypot(*d), d[1], d[0]),
    )
    seen: list[int] = []
    for dx, dy in grid:
        pt = replace(c, x=c.x + dx, y=c.y + dy)
        level, reply = read_loaded(s, frame, pt)
        log("hunt", x=pt.x, y=pt.y, dx=dx, dy=dy, level=level, reply=reply)
        base = paper if paper is not None else (min(seen) if seen else None)
        if level is not None:
            seen.append(level)
        if level is None or c.reads(level, base) is not True:
            continue
        side = replace(pt, x=pt.x + proof.SQUARE + BESIDE_HIT)
        beside, reply = read_loaded(s, frame, side)
        log("hunt", x=side.x, y=side.y, dx=dx, dy=dy, level=beside, reply=reply, beside=True)
        if beside is not None and beside >= (level + (sensing.PAPER if base is None else base)) / 2:
            continue
        right, down = frame.paper_at
        there = replace(frame, paper_at=(right + dx, down + dy))  # its middle on the ink just read
        off = measure(s, there, log, base)
        if off is not None:
            return round(dx + off[0], 2), round(dy + off[1], 2)
    return None


EDGE_HALVINGS = 5  # the square's 5 mm halved five times: each edge to 0.16 mm
MEASURE_MOVE = 0.25  # mm: a measured square closer than this to the frame's leaves the frame be


def measure(s: Session, frame: Frame, log: SessionLog, paper: int | None) -> tuple[float, float] | None:
    """Where the square sits from where the frame expects it, (right, down) mm, from its top and left
    edges, for a hunt's hit. Ink under the frame's square middle (``sensing.placement``) puts each
    edge within the square's width above or left of it, with blank paper beyond: halving that span
    EDGE_HALVINGS times pins the edge, a reading counting as ink when it is nearer the middle's level
    than ``paper``'s. Eleven readings, about 45 s. None when the middle isn't ink against the paper,
    or an edge's readings never reach paper (no edge in that span: the pouch's dark band past the
    paper's edge reads so, which is why a hit is measured from this side and not ``locate``'s)."""
    c = sensing.placement(frame)
    ink, reply = read_loaded(s, frame, c)
    log("measure", edge="middle", x=c.x, y=c.y, level=ink, reply=reply)
    if ink is None or c.reads(ink, paper) is not True:
        return None
    base = sensing.PAPER if paper is None else paper
    cut = (ink + base) / 2

    def edge(axis: str) -> float | None:
        lo, hi = -proof.SQUARE, 0.0  # offsets from the middle: paper at lo, ink at hi
        for _ in range(EDGE_HALVINGS):
            mid = (lo + hi) / 2
            pt = replace(c, x=c.x + mid) if axis == "x" else replace(c, y=c.y + mid)
            level, reply = read_loaded(s, frame, pt)
            log("measure", edge=axis, x=pt.x, y=pt.y, level=level, reply=reply)
            if level is not None and level >= cut:
                hi = mid
            else:
                lo = mid
        return None if lo == -proof.SQUARE else (lo + hi) / 2

    ex = edge("x")
    ey = None if ex is None else edge("y")
    if ex is None or ey is None:
        return None
    return round(c.x + ex - frame.inset, 2), round(c.y + ey - frame.top, 2)


LOCATE_SPAN = sensing.BESIDE  # mm right of and below the square's middle its edge is looked for in
# Halvings (across x, down y) before the scan. The start sits 1 mm inside the paper's left edge and
# misses only when the sheet lies further right than measured, by 0.7 mm or more (2026-10-26:
# 1.5 mm): three, 0.38 mm, do across. Down, the deck box's start has about a millimetre to spare
# (``locate``): four, 0.19 mm.
LOCATE_HALVINGS = (3, 4)
REMEASURE_HALVINGS = (5, 5)  # after a scan that found nothing: 0.09 mm both ways
# mm past the square's 5 mm an edge may measure from a middle that reads ink and stay the square's.
# That middle lies 0.1 mm or more inside the square (ink reads from 0.1 mm inside the 50 % point of
# an edge measured on 2026-10-28), so a clean sheet's edge lies within 4.93 mm of it (the moves'
# 0.05 mm steps included), which halvings of LOCATE_SPAN place at 5.06 at most; the next estimate
# up, 5.44, is paper reading like ink past where any square can end.
LIFTED_EDGE = 0.3


def locate(
    s: Session,
    frame: Frame,
    log: SessionLog,
    ink: int | None = None,
    beside: int | None = None,
    halvings: tuple[int, int] = LOCATE_HALVINGS,
) -> tuple[float, float] | None:
    """Where the square lies from where the frame expects it, (right, down) mm, from its right and
    bottom edges. Its middle where the frame puts it reads ``ink`` and blank paper right of it
    (``sensing.beside``) reads ``beside`` (each read here when None); not ink against that paper,
    and this returns None. Ink there puts each edge within 5 mm right of and below that middle, with
    blank paper past it: ``halvings`` (across, down) readings across LOCATE_SPAN pin each edge to
    LOCATE_SPAN / 2^halvings, a reading counting as ink when it is nearer full ink than the palest
    paper read. Before the scan, three across and four down, about 24 s: the deck box's scan start,
    3.5 mm into its square (``default_start``), has about a millimetre to spare upward (2 mm in
    failed on 2026-10-26, 2.3 mm in registered on 2026-10-28) and no more known downward, so its
    bottom edge is pinned to 0.19 mm.

    The right and bottom edges, because nothing but blank paper lies past them on any sheet: the
    deck box prints nothing within 6 mm of a mark's legs (formeriet MARK_QUIET), a proxy sheet's
    cards start below the square's row. Past the top and left edges lies the paper's edge, which a
    laminated sheet's pouch shows as dark as ink 5 mm out on the deck box. So paper beside the square
    that reads like ink, LOCATE_SPAN right of its middle or out to an edge more than LIFTED_EDGE past
    the 5 mm a square with ink under its middle can reach, is the sheet lifting off the mat there,
    and the job stops before the scan (``sensing.press_down``)."""
    c = sensing.placement(frame)
    if ink is None:
        ink, reply = read_loaded(s, frame, c)
        log("locate", edge="middle", x=c.x, y=c.y, level=ink, reply=reply)
    if beside is None:
        side = sensing.beside(frame)
        beside, reply = read_loaded(s, frame, side)
        log("locate", edge="beside", x=side.x, y=side.y, level=beside, reply=reply)
    if ink is None or c.reads(ink, beside if sensing.blank(beside) else None) is not True:
        return None
    if beside is not None and not sensing.blank(beside):
        raise CutError(
            sensing.press_down(
                ["top-left"],
                f"blank paper {LOCATE_SPAN:g} mm right of the square's middle reads {beside}, like its ink ({ink})",
            )
        )
    # Halfway between the paper and full ink (sensing.FULL), not the middle's own reading: a middle
    # near an edge of the square (the sheet up to 2.5 mm off) reads dim, and halfway to that would put
    # the other edge up to 0.3 mm out. The paper is the palest read beside the square so far.
    pale = [sensing.PAPER if beside is None else beside]

    def edge(axis: str, n: int) -> float:
        lo, hi = 0.0, LOCATE_SPAN  # offsets from the middle: ink at lo, paper at hi
        levels = []
        for _ in range(n):
            mid = (lo + hi) / 2
            pt = replace(c, x=c.x + mid) if axis == "x" else replace(c, y=c.y + mid)
            level, reply = read_loaded(s, frame, pt)
            log("locate", edge=axis, x=pt.x, y=pt.y, level=level, reply=reply)
            levels.append("no reading" if level is None else str(level))
            if level is not None:
                pale[0] = min(pale[0], level)
            if level is not None and level >= (sensing.FULL + pale[0]) / 2:
                lo = mid
            else:
                hi = mid
        if (lo + hi) / 2 > proof.SQUARE + LIFTED_EDGE:
            side = "right of" if axis == "x" else "below"
            raise CutError(
                sensing.press_down(
                    ["top-left"],
                    f"blank paper {side} the square reads {', '.join(levels)} out to {lo:.1f} mm from its "
                    f"middle, like its ink ({ink}), where the paper beside it reads {pale[0]}",
                )
            )
        return (lo + hi) / 2

    ex = edge("x", halvings[0])
    ey = edge("y", halvings[1])
    return round(c.x + ex - proof.SQUARE - frame.inset, 2), round(c.y + ey - proof.SQUARE - frame.top, 2)


def read_flat(s: Session, frame: Frame, log: SessionLog) -> sensing.Flatness:
    """Blank paper beside each mark and in the middle of the top margin, in the loaded mat's frame
    (``read_loaded``, as the placement reading). The square's point first, then the reference on
    the way to the top-right L, then the bottom-left: one pass along the top."""
    first, *ls = sensing.quiet(frame)
    readings = []
    for c in (first, sensing.reference(frame), *ls):
        level, reply = read_loaded(s, frame, c)
        log("quiet", mark=c.mark, what=c.what, x=c.x, y=c.y, level=level, reply=reply)
        readings.append((c, level))
    (_, mid), rest = readings[1], [readings[0], *readings[2:]]
    return sensing.Flatness(tuple(rest), mid)


def check_flat(s: Session, frame: Frame, log: SessionLog) -> sensing.Flatness:
    """Before the scan, where the placement found the sheet: blank paper beside every mark reads as
    paper, or the sheet lifts off the mat at that corner (``sensing.Flatness``) and nothing is
    scanned. Paper that only just reads as paper is a warning: the scan may misread there, and the
    check after it catches that."""
    flat = read_flat(s, frame, log)
    stop = flat.stop()
    if stop:
        raise CutError(stop)
    for line in flat.notes():
        log.say(f"check: {line}")
    return flat


def check_marks(
    s: Session,
    frame: Frame,
    log: SessionLog,
    flat: sensing.Flatness,
    checks: list[sensing.Check] | None = None,
    seen: dict[tuple[str, str], int | None] | None = None,
) -> list[str]:
    """After the scan, every ``sensing.checks`` reading (or ``checks``) through the registered frame:
    what failed, one line each, or nothing when the print agrees with the frame; each reading goes
    into ``seen`` by (mark, what). A leg's centre line that isn't ink is read NUDGE either side, then,
    still not ink, SIDE either side: it passes as a faint leg when its darkest reading beats the
    paper on both sides by CONTRAST. Ink counts only on a mark whose blank paper read as paper before
    the scan (``flat``): where paper reads like ink, so does every spot, and an ink reading proves
    nothing."""
    failed = []

    def read(c: sensing.Check) -> int | None:
        return s.sense(*sensing.registered(frame, c), SENSE_SLOT)[0]

    for c in sensing.checks(frame) if checks is None else checks:
        paper = flat.paper_at(c.mark)  # dim ink is judged against its own paper (sensing.thresholds)
        level = read(c)
        if seen is not None:
            seen[(c.mark, c.what)] = level
        seen_as, nudged, sides = c.reads(level, paper), [], []
        if c.ink and seen_as is not True and c.nudge:
            for k in (-1, 1):
                nl = read(c.nudged(k * sensing.NUDGE))
                nudged.append(nl)
                if c.reads(nl, paper) is True:
                    seen_as = True
                    break
            if seen_as is not True and c.line:
                sides = [read(c.nudged(k * sensing.SIDE)) for k in (-1, 1)]
                peak = max((v for v in (level, *nudged) if v is not None), default=None)
                if (
                    peak is not None
                    and None not in sides
                    and all(c.reads(v, paper) is False and peak - v >= sensing.CONTRAST for v in sides)
                ):
                    seen_as = True
                    log.say(
                        f"check: {c.mark}, {c.what} reads faint ({peak}) against paper at {sides[0]}/{sides[1]}"
                    )
        unproven = c.ink and seen_as is True and not flat.paper_beside(c.mark)
        ok = seen_as is c.ink and not unproven
        log(
            "check",
            mark=c.mark,
            what=c.what,
            x=c.x,
            y=c.y,
            ink=c.ink,
            level=level,
            nudged=nudged,
            sides=sides,
            ok=ok,
            unproven=unproven,
        )
        if unproven:
            failed.append(f"{c.mark}, {c.what}: reads ink, but the paper beside it didn't read as paper")
        elif not ok:
            want = "ink" if c.ink else "paper"
            got = (
                "nothing"
                if level is None
                else f"{level} ({'ink' if seen_as else 'paper' if seen_as is False else 'between'})"
            )
            failed.append(f"{c.mark}, {c.what}: read {got} where {want} should be")
    return failed


def prove(
    s: Session,
    job: Job,
    log: SessionLog,
    confirm: Confirm,
    target: proof.Target,
    round_: int,
    rescan: bool,
    verify: Callable[[], list[str]] | None = None,
    low: tuple[float, float] = (0.0, 0.0),
    blade: protocol.Blade = proof.BLADE,
    on_target: Callable[[proof.Target], None] | None = None,
) -> bool:
    """Trace round ``round_``'s proof target along its edge and across it (``Target.cuts``) in one pass with ``blade``, set the job's
    own blade again (the same cutting area, back to ``low``), park the head out of the way and ask.
    True: cut the job; False: rescan. Anything but y or r stops the job (CutError). With ``verify``
    (``held``), the marks are read again after the proof cut, before anyone is asked: a machine
    that lost its place cutting it stops the job there."""
    s.setup(blade, job.frame, low)
    s.cut(replace(job, lines=list(target.cuts())))
    if on_target:
        on_target(target)
    of = max(tg.number for tg in job.frame.targets)
    log.say(
        f"proof: {target.name(of)} cut, one pass at force {blade.force}, speed {blade.speed}, depth {blade.depth}"
    )
    s.setup(job.blade, job.frame, low)  # the job's own blade, before anything else is cut
    if verify:
        lost = verify()
        if lost:
            raise CutError(
                f"after the proof cut the marks no longer read where the scan put them ({'; '.join(lost)}): "
                "the machine lost its place cutting it, so the job isn't cut. Rerun"
            )
    s.send(protocol.move(0, job.frame.width / 2))  # the head to the top edge's middle, off every corner
    s.wait_ready(60)
    log("proof", round=round_, target=target.number, x=target.x, y=target.y, size=target.size)
    answer = ask(s, confirm, round_ - 1, rescan)
    log("proof_answer", round=round_, answer=answer)
    if answer == "y":
        return True
    if answer == "r" and rescan:
        log.say(f"rescanning (proof round {round_ + 1}): its cut goes on the next target")
        return False
    raise CutError("stopped at the proof cut: nothing else was cut")


def ask(s: Session, confirm: Confirm, attempt: int, rescan: bool, poll_s: float = 10.0) -> str:
    """The operator's answer; meanwhile a status poll every ``poll_s`` s, so the machine hears
    from us while they look. No answer (stdin closed) means stop."""
    answer: list[str] = []

    def wait() -> None:
        with contextlib.suppress(Exception):  # EOF on stdin, a closed terminal: treated as q
            answer.append(confirm(attempt, rescan))

    th = threading.Thread(target=wait, name="proof-answer", daemon=True)
    th.start()
    while th.is_alive():
        th.join(poll_s)
        if th.is_alive():
            s.status()
    return answer[0] if answer else "q"


def probe(frame: Frame, t: Transport, log: SessionLog, starts: list[tuple[float, float]]) -> dict:
    """Scan without cutting; every probe query's reply before and after the scan."""
    s = Session(t, log)
    replies: dict[str, list[str | None]] = {}
    try:
        firmware = s.preflight()
        log.say(f"connected: {firmware} via {t.name}, mat loaded")
        s.prepare()

        def ask(phase: int) -> None:
            for q in PROBE_QUERIES:
                r = s.query(protocol.cmd(q), timeout=2.0)
                text = r.decode("latin1").rstrip("\x03") if r is not None else None
                replies.setdefault(q, [None, None])[phase] = text
                log("probe", query=q, phase=("before", "after")[phase], reply=text)

        ask(0)
        start = s.register(frame, starts)
        ask(1)
        return {"firmware": firmware, "start": start, "replies": replies}
    finally:
        wrap_up(s, t, log)


SENSE_SLOT = 4  # the light slot TB63 overwrites: one the scan never restores from (it uses slot 0)


def sense(
    frame: Frame,
    t: Transport,
    log: SessionLog,
    starts: list[tuple[float, float]],
    points: list[sensing.Point],
) -> dict:
    """Scan the marks, then read the spot under each point (page mm) through the registered frame.
    Cuts nothing. Refuses when the scan's own light slot is the one TB63 would overwrite."""
    s = Session(t, log)
    try:
        firmware = s.preflight()
        log.say(f"connected: {firmware} via {t.name}, mat loaded")
        slot = check_light_slot(s, log)
        s.prepare()
        start = s.register(frame, starts)
        where = [sensing.registered(frame, pt) for pt in points]
        low = (min(0.0, min(y for y, _ in where) - 0.5), min(0.0, min(x for _, x in where) - 0.5))
        s.send(*protocol.cutting_area(*far_corner(frame), *low))
        readings = []
        for pt, (y, x) in zip(points, where, strict=True):
            level, reply = s.sense(y, x, SENSE_SLOT)
            log("sense", profile=pt.profile, offset=pt.offset, x=pt.x, y=pt.y, level=level, reply=reply)
            readings.append((pt, level))
        s.send(protocol.select_light(slot))  # the next scan starts with the light as it finds it
        return {"firmware": firmware, "start": start, "slot": slot, "readings": readings}
    finally:
        wrap_up(s, t, log)


def diagnose(frame: Frame, t: Transport, log: SessionLog) -> tuple[list[sensing.Seen], sensing.Flatness]:
    """Before any scan, rows of readings across every mark in the loaded mat's frame
    (``read_loaded``): the square first (``sensing.square_rows``), then each leg
    (``sensing.leg_rows``) centred on where the square says the sheet sits, then the blank paper a
    cut reads beside each mark (``read_flat``), also where the square says, as a cut reads it. Where
    each mark really is and how it reads. Cuts nothing and scans nothing, so it works on a sheet the
    scan can't register."""
    s = Session(t, log)
    seen: list[sensing.Seen] = []
    try:
        firmware = s.preflight()
        log.say(f"connected: {firmware} via {t.name}, mat loaded")
        slot = check_light_slot(s, log)
        s.prepare()

        def read(rows: list[sensing.Row]) -> None:
            for row in rows:
                levels = []
                for pt in row.points:
                    level, reply = read_loaded(s, frame, pt)
                    log("diagnose", mark=row.mark, what=row.what, offset=pt.offset, level=level, reply=reply)
                    levels.append(level)
                seen.append(sensing.see(row, levels))

        read(sensing.square_rows(frame))
        dx, dy = (v.shift or 0.0 for v in seen)  # the legs' rows follow the square
        read(sensing.leg_rows(frame, dx, dy))
        flat = read_flat(s, moved(frame, (dx, dy)), log)
        s.send(protocol.select_light(slot))
        return seen, flat
    finally:
        wrap_up(s, t, log)


def wait_for_reload(
    t: Transport, log: SessionLog, interrupted: Callable[[float], bool], poll_s: float = 0.5
) -> bool:
    """Poll the status until the mat has gone out ("unloaded") and come back in ("ready"): True.
    ``interrupted(poll_s)`` waits between polls and returns True to give up (False is returned).
    Only the status query is sent, so nothing moves and nothing needs undoing.

    On the machine (2026-10-20) a reload reads unloaded → CONTROL "0" → ready, with no "moving"
    in between at this poll rate."""
    s = Session(t, log)
    gone = False
    while True:
        state = s.status()
        if state == "unloaded":
            gone = True
        elif state == "ready" and gone:
            log("reloaded")
            return True
        if interrupted(poll_s):
            return False


def wrap_up(s: Session, t: Transport, log: SessionLog) -> None:
    """Back to the origin if anything moved, then disconnect. A failure here is reported but
    never replaces the error that got us here."""
    try:
        if s.touched:
            s.finish()
            s.wait_ready(60)
    except Exception as e:  # the link may already be gone
        log.say(
            f"WARNING: couldn't send the return to the origin ({e}); power-cycle the Cameo before the next job"
        )
    finally:
        t.close()
