"""Silhouette Studio .studio3 template surgery: apply the machine cut offset, rebase a Studio save.

The Cameo 5 Alpha cuts ~1mm high relative to the registration marks it scans
(a machine bias, independent of paper size or layout). Rather than keeping
hand-edited template blobs around, the offset lives as data in
data/cut_offset.json and is applied at build time to whichever stock template
the run selects.

Reverse-engineered from a known-good pair (stock a4-standard-v5.studio3 vs the
same template nudged 1mm down in Silhouette Studio and re-saved): moving a
shape rewrites exactly 14 float32 fields per "Polygon" record, at fixed
offsets relative to the record's trailing "Polygon" marker — the 12 Y values
of the page-space path (a palindrome for the rounded rect), the center Y, and
one Y-extent field. X fields sit 4 bytes before each Y. Local-space geometry
and everything else is untouched. ``patch`` replicates that rewrite bit-for-bit
and refuses to write anything if the file doesn't match the expected shape.

``rebase`` does the reverse for Studio-authored state: it transplants pristine
stock geometry into a Studio-saved file (donor of machine profile / Page Setup)
and proves the result by checking base + measured offset == the Studio save
byte-for-byte.
"""

from __future__ import annotations

import json
import shutil
import struct
from itertools import pairwise
from pathlib import Path

from .paths import CUT_OFFSET_FILE

MARKER = b"Polygon"
# Relative to record span start (marker position minus record size): Y fields.
Y_EXTENT = 896
Y_CENTER = 916
Y_PATH = [963, 981, 1007, 1025, 1043, 1069, 1087, 1105, 1131, 1149, 1167, 1193]
FIELDS = [Y_EXTENT, Y_CENTER, *Y_PATH]


class Studio3Error(Exception):
    pass


def rd(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def wr(buf: bytearray, off: int, v: float) -> None:
    struct.pack_into("<f", buf, off, v)


def find_records(data: bytes, label: str = "template") -> tuple[list[int], int]:
    marks = [i for i in range(len(data)) if data[i : i + len(MARKER)] == MARKER]
    if len(marks) < 2:
        raise Studio3Error(f"{label}: expected multiple Polygon records, found {len(marks)}")
    spacings = {b - a for a, b in pairwise(marks)}
    if len(spacings) != 1:
        raise Studio3Error(f"{label}: non-uniform Polygon record spacing {sorted(spacings)} — unknown layout")
    size = spacings.pop()
    if marks[0] < size:
        raise Studio3Error(f"{label}: first Polygon record would start before the file does")
    return marks, size


def _tail_end(data: bytes, last_mark: int) -> int:
    # Search stops at the embedded preview PNG to avoid touching image bytes.
    t = data.find(b"\x89PNG", last_mark)
    return t if t >= 0 else len(data)


def _bbox_hits(data: bytes, marks: list[int], size: int, axis_off: int) -> tuple[float, list[int]]:
    target = min(rd(data, m - size + o + axis_off) for m in marks for o in Y_PATH)
    tail = _tail_end(data, marks[-1])
    hits = [i for i in range(marks[-1], tail - 3) if abs(rd(data, i) - target) < 0.0005]
    return target, hits


def patch(data: bytes, dx: float, dy: float) -> bytes:
    """Shift every card outline by (dx, dy) mm. Bit-exact replica of a Studio nudge."""
    marks, size = find_records(data)
    out = bytearray(data)
    for m in marks:
        start = m - size
        ys = [rd(data, start + o) for o in Y_PATH]
        center = rd(data, start + Y_CENTER)
        extent = rd(data, start + Y_EXTENT)
        # Fingerprint the record before touching it: the page-space path of a
        # rounded rect reads as a palindrome, its center Y is the midpoint,
        # and the extent field lies beyond the path.
        if any(abs(a - b) > 0.01 for a, b in zip(ys, ys[::-1], strict=True)):
            raise Studio3Error(f"record at {m}: Y path is not a palindrome — layout mismatch")
        lo, hi = min(ys), max(ys)
        if not 10 < hi - lo < 500:
            raise Studio3Error(f"record at {m}: implausible path height {hi - lo:.2f}mm")
        if abs(center - (lo + hi) / 2) > 0.01:
            raise Studio3Error(f"record at {m}: center Y {center:.3f} != path midpoint {(lo + hi) / 2:.3f}")
        if extent <= hi:
            raise Studio3Error(f"record at {m}: extent {extent:.3f} not beyond path max {hi:.3f}")
        for o in FIELDS:
            wr(out, start + o, rd(data, start + o) + dy)
            if dx:
                wr(out, start + o - 4, rd(data, start + o - 4) + dx)

    # The settings block after the records holds one copy of the cut bounding
    # box's (min X, min Y); Studio updates it when shapes move (observed in the
    # hand-tuned ground-truth file), so shift it too.
    for axis_off, delta in ((-4, dx), (0, dy)):
        if not delta:
            continue
        target, hits = _bbox_hits(data, marks, size, axis_off)
        if len(hits) != 1:
            raise Studio3Error(f"expected 1 trailing bbox copy of {target:.4f}, found {len(hits)}")
        wr(out, hits[0], rd(data, hits[0]) + delta)
    return bytes(out)


def read_cut_offset(path: Path = CUT_OFFSET_FILE) -> tuple[float, float]:
    """(x_mm, y_mm) from data/cut_offset.json; (0, 0) when absent."""
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        return 0.0, 0.0
    return float(cfg.get("x_mm", 0)), float(cfg.get("y_mm", 0))


def offset_tag(dx: float, dy: float) -> str:
    return "".join(f"+{a}{v:g}mm" for a, v in (("x", dx), ("y", dy)) if v)


def place_template(template: Path, outdir: Path, dx: float, dy: float) -> Path:
    """Copy ``template`` into ``outdir`` with the cut offset applied; returns the placed path."""
    outdir.mkdir(parents=True, exist_ok=True)
    if dx == 0 and dy == 0:
        dest = outdir / template.name
        shutil.copyfile(template, dest)
        return dest
    dest = outdir / f"{template.stem}{offset_tag(dx, dy)}.studio3"
    dest.write_bytes(patch(template.read_bytes(), dx, dy))
    return dest


def rebase(saved: bytes, stock: bytes) -> tuple[bytes, float, float]:
    """Transplant stock geometry into a Studio save → offset-free base. Returns (base, dx, dy)."""
    s_marks, s_size = find_records(saved, "saved")
    k_marks, k_size = find_records(stock, "stock")
    if len(s_marks) != len(k_marks):
        raise Studio3Error(f"record count mismatch: saved {len(s_marks)} vs stock {len(k_marks)}")

    # Measure the offset baked into the saved file (must be uniform across
    # every record and field — anything else means the pairing is wrong or
    # Studio moved shapes, and we refuse to guess).
    dys, dxs = [], []
    for sm, km in zip(s_marks, k_marks, strict=True):
        for o in FIELDS:
            dys.append(rd(saved, sm - s_size + o) - rd(stock, km - k_size + o))
            dxs.append(rd(saved, sm - s_size + o - 4) - rd(stock, km - k_size + o - 4))
    for name, ds in (("y", dys), ("x", dxs)):
        if max(ds) - min(ds) > 0.001:
            raise Studio3Error(f"baked {name} offset is not uniform ({min(ds):.4f}..{max(ds):.4f}mm)")
    dy = round(sum(dys) / len(dys), 4)
    dx = round(sum(dxs) / len(dxs), 4)

    out = bytearray(saved)
    for sm, km in zip(s_marks, k_marks, strict=True):
        for o in FIELDS:
            out[sm - s_size + o - 4 : sm - s_size + o + 4] = stock[km - k_size + o - 4 : km - k_size + o + 4]

    for axis_off, delta in ((0, dy), (-4, dx)):
        if not delta:
            continue
        _, s_hits = _bbox_hits(saved, s_marks, s_size, axis_off)
        _, k_hits = _bbox_hits(stock, k_marks, k_size, axis_off)
        if len(s_hits) != 1 or len(k_hits) != 1:
            raise Studio3Error(
                f"expected 1 trailing bbox copy each, found saved={len(s_hits)} stock={len(k_hits)}"
            )
        out[s_hits[0] : s_hits[0] + 4] = stock[k_hits[0] : k_hits[0] + 4]

    # Proof: re-applying the measured offset must reproduce the Studio save exactly.
    if patch(bytes(out), dx, dy) != saved:
        raise Studio3Error("verification failed: base + measured offset != Studio-saved file")
    return bytes(out), dx, dy
