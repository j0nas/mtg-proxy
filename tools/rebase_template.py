#!/usr/bin/env python3
"""Rebase a Silhouette-Studio-saved template into an offset-free base template.

Why: Studio state worth keeping (the selected machine profile — Cameo 5 Alpha —
and other Page Setup preferences) can only be authored by saving from Studio
itself. But a file saved from Studio carries the machine cut offset already
baked into its geometry (it was opened from a build output), and the build
pipeline applies data/cut_offset.json at build time — so the saved file can't
be used as a base directly without double-applying the offset.

This tool marries the two: it takes the Studio-saved file (donor of machine
state and preferences) and the pristine stock template (donor of geometry),
and transplants the stock geometry bytes into the saved file, producing an
offset-free base that still opens in Studio with the saved machine/preferences.

It then PROVES the result: re-applying the measured offset (offset_studio3.py's
own patch function) to the produced base must reproduce the Studio-saved file
byte-for-byte. Studio round-trips cut coordinates bit-exact (verified on the
2026-08 save), so this equality is exact, not approximate; the tool refuses to
write anything if it doesn't hold.

Usage: rebase_template.py <studio_saved.studio3> <stock.studio3> <out.studio3>
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offset_studio3 as off


def find_records(data: bytes, label: str):
    marks = [i for i in range(len(data)) if data[i : i + len(off.MARKER)] == off.MARKER]
    if len(marks) < 2:
        off.die(f"{label}: expected multiple Polygon records, found {len(marks)}")
    spacings = {b - a for a, b in zip(marks, marks[1:])}
    if len(spacings) != 1:
        off.die(f"{label}: non-uniform Polygon record spacing {sorted(spacings)}")
    size = spacings.pop()
    if marks[0] < size:
        off.die(f"{label}: first record would start before the file does")
    return marks, size


def main() -> None:
    if len(sys.argv) != 4:
        off.die("usage: rebase_template.py <studio_saved.studio3> <stock.studio3> <out.studio3>")
    saved_path, stock_path, out_path = (Path(p) for p in sys.argv[1:4])
    saved = saved_path.read_bytes()
    stock = stock_path.read_bytes()

    s_marks, s_size = find_records(saved, "saved")
    k_marks, k_size = find_records(stock, "stock")
    if len(s_marks) != len(k_marks):
        off.die(f"record count mismatch: saved {len(s_marks)} vs stock {len(k_marks)}")

    fields = [off.Y_EXTENT, off.Y_CENTER, *off.Y_PATH]

    # Measure the offset baked into the saved file (must be uniform across
    # every record and field — anything else means the pairing is wrong or
    # Studio moved shapes, and we refuse to guess).
    dys, dxs = [], []
    for sm, km in zip(s_marks, k_marks):
        for o in fields:
            dys.append(off.rd(saved, sm - s_size + o) - off.rd(stock, km - k_size + o))
            dxs.append(off.rd(saved, sm - s_size + o - 4) - off.rd(stock, km - k_size + o - 4))
    for name, ds in (("y", dys), ("x", dxs)):
        if max(ds) - min(ds) > 0.001:
            off.die(f"baked {name} offset is not uniform ({min(ds):.4f}..{max(ds):.4f}mm)")
    dy = round(sum(dys) / len(dys), 4)
    dx = round(sum(dxs) / len(dxs), 4)
    print(f"baked offset measured: x={dx:g}mm y={dy:g}mm", file=sys.stderr)

    # Transplant stock geometry bytes (bit-exact) into the saved file.
    out = bytearray(saved)
    for sm, km in zip(s_marks, k_marks):
        for o in fields:
            out[sm - s_size + o - 4 : sm - s_size + o + 4] = stock[km - k_size + o - 4 : km - k_size + o + 4]

    # The trailing settings block holds one copy of the cut bbox (min X, min Y);
    # restore stock's bytes there too (same single-hit search offset_studio3 uses).
    for axis_off, delta in ((0, dy), (-4, dx)):
        if not delta:
            continue
        s_min = min(off.rd(saved, m - s_size + o + axis_off) for m in s_marks for o in off.Y_PATH)
        k_min = min(off.rd(stock, m - k_size + o + axis_off) for m in k_marks for o in off.Y_PATH)
        s_tail = saved.find(b"\x89PNG", s_marks[-1])
        s_tail = s_tail if s_tail >= 0 else len(saved)
        k_tail = stock.find(b"\x89PNG", k_marks[-1])
        k_tail = k_tail if k_tail >= 0 else len(stock)
        s_hits = [i for i in range(s_marks[-1], s_tail - 3) if abs(off.rd(saved, i) - s_min) < 0.0005]
        k_hits = [i for i in range(k_marks[-1], k_tail - 3) if abs(off.rd(stock, i) - k_min) < 0.0005]
        if len(s_hits) != 1 or len(k_hits) != 1:
            off.die(f"expected 1 trailing bbox copy each, found saved={len(s_hits)} stock={len(k_hits)}")
        out[s_hits[0] : s_hits[0] + 4] = stock[k_hits[0] : k_hits[0] + 4]

    # Proof: re-applying the measured offset must reproduce the Studio save
    # exactly. If Studio ever stops round-tripping coordinates bit-exact,
    # this fails loudly instead of shipping a subtly-off base.
    if off.patch(bytes(out), dx, dy) != saved:
        off.die("verification failed: base + measured offset != Studio-saved file")
    print("verified: base + offset reproduces the Studio save byte-for-byte", file=sys.stderr)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))
    print(out_path)


if __name__ == "__main__":
    main()
