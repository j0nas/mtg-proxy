#!/usr/bin/env python3
"""Apply the machine cut offset to a .studio3 cutting template.

The Cameo 5 Alpha cuts ~1mm high relative to the registration marks it scans
(a machine bias, independent of paper size or layout). Rather than keeping
hand-edited template blobs around, the offset lives as data in
data/cut_offset.json and is applied here, at build time, to whichever stock
template the run selects.

Reverse-engineered from a known-good pair (stock a4-standard-v5.studio3 vs the
same template nudged 1mm down in Silhouette Studio and re-saved): moving a
shape rewrites exactly 14 float32 fields per "Polygon" record, at fixed
offsets relative to the record's trailing "Polygon" marker — the 12 Y values
of the page-space path (a palindrome for the rounded rect), the center Y, and
one Y-extent field. X fields sit 4 bytes before each Y. Local-space geometry
and everything else is untouched. This tool replicates that rewrite bit-for-bit
and refuses to write anything if the file doesn't match the expected shape.

Usage: offset_studio3.py <template.studio3> <outdir>
Reads data/cut_offset.json ({"x_mm": 0.0, "y_mm": 1.0}); copies the template
into <outdir> unchanged when both offsets are 0, otherwise writes
<outdir>/<base>+y1.0mm.studio3 (tag reflects the offsets). Prints the path of
the placed template on stdout.
"""

import json
import shutil
import struct
import sys
from pathlib import Path

MARKER = b"Polygon"
# Relative to record span start (marker position minus record size): Y fields.
Y_EXTENT = 896
Y_CENTER = 916
Y_PATH = [963, 981, 1007, 1025, 1043, 1069, 1087, 1105, 1131, 1149, 1167, 1193]


def die(msg: str) -> None:
    sys.exit(f"offset_studio3: {msg}")


def rd(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def wr(buf: bytearray, off: int, v: float) -> None:
    struct.pack_into("<f", buf, off, v)


def patch(data: bytes, dx: float, dy: float) -> bytes:
    marks = [i for i in range(len(data)) if data[i : i + len(MARKER)] == MARKER]
    if len(marks) < 2:
        die(f"expected multiple Polygon records, found {len(marks)}")
    spacings = {b - a for a, b in zip(marks, marks[1:])}
    if len(spacings) != 1:
        die(f"non-uniform Polygon record spacing {sorted(spacings)} — unknown layout")
    size = spacings.pop()
    if marks[0] < size:
        die("first Polygon record would start before the file does")

    out = bytearray(data)
    for m in marks:
        start = m - size
        ys = [rd(data, start + o) for o in Y_PATH]
        center = rd(data, start + Y_CENTER)
        extent = rd(data, start + Y_EXTENT)
        # Fingerprint the record before touching it: the page-space path of a
        # rounded rect reads as a palindrome, its center Y is the midpoint,
        # and the extent field lies beyond the path.
        if any(abs(a - b) > 0.01 for a, b in zip(ys, ys[::-1])):
            die(f"record at {m}: Y path is not a palindrome — layout mismatch")
        lo, hi = min(ys), max(ys)
        if not 10 < hi - lo < 500:
            die(f"record at {m}: implausible path height {hi - lo:.2f}mm")
        if abs(center - (lo + hi) / 2) > 0.01:
            die(f"record at {m}: center Y {center:.3f} != path midpoint {(lo + hi) / 2:.3f}")
        if extent <= hi:
            die(f"record at {m}: extent {extent:.3f} not beyond path max {hi:.3f}")
        for o in [Y_EXTENT, Y_CENTER, *Y_PATH]:
            wr(out, start + o, rd(data, start + o) + dy)
            if dx:
                wr(out, start + o - 4, rd(data, start + o - 4) + dx)

    # The settings block after the records holds one copy of the cut bounding
    # box's (min X, min Y); Studio updates it when shapes move (observed in the
    # hand-tuned ground-truth file), so shift it too. Search stops at the
    # embedded preview PNG to avoid touching image bytes.
    tail_end = data.find(b"\x89PNG", marks[-1])
    if tail_end < 0:
        tail_end = len(data)
    min_x = min(rd(data, m - size + o - 4) for m in marks for o in Y_PATH)
    min_y = min(rd(data, m - size + o) for m in marks for o in Y_PATH)
    for target, delta in ((min_x, dx), (min_y, dy)):
        if not delta:
            continue
        hits = [i for i in range(marks[-1], tail_end - 3)
                if abs(rd(data, i) - target) < 0.0005]
        if len(hits) != 1:
            die(f"expected 1 trailing bbox copy of {target:.4f}, found {len(hits)}")
        wr(out, hits[0], rd(data, hits[0]) + delta)
    return bytes(out)


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: offset_studio3.py <template.studio3> <outdir>")
    template, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    cfg_path = Path(__file__).resolve().parent.parent / "data" / "cut_offset.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    dx, dy = float(cfg.get("x_mm", 0)), float(cfg.get("y_mm", 0))

    outdir.mkdir(parents=True, exist_ok=True)
    if dx == 0 and dy == 0:
        dest = outdir / template.name
        shutil.copyfile(template, dest)
        print(dest)
        return

    tag = "".join(f"+{a}{v:g}mm" for a, v in (("x", dx), ("y", dy)) if v)
    dest = outdir / f"{template.stem}{tag}.studio3"
    dest.write_bytes(patch(template.read_bytes(), dx, dy))
    print(dest)


if __name__ == "__main__":
    main()
