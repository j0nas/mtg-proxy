#!/usr/bin/env bash
# save-offset.sh — store your printer's front/back duplex offset once;
# make-proxies.sh then applies it to every future PDF automatically.
#
# 1. Print silhouette-card-maker/calibration/a4-calibration.pdf
#    (both pages on one sheet, exactly the way you print card sheets:
#    actual size, manual duplex, long-edge flip).
# 2. Hold it against a strong light, find the front/back square pair that
#    lines up, and read its red (x, y) label.
# 3. Run: ./save-offset.sh -x <x> -y <y>
#    Units are 300 PPI pixels (1 unit ≈ 0.085 mm). Positive x = right,
#    positive y = up, relative to the back page.
#
# Verify with: ./save-offset.sh --pdf_path calibration/a4-calibration.pdf
# then reprint the produced calibration/a4-calibration_offset.pdf — the
# (0, 0) square should now line up.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT/silhouette-card-maker"
PY=./venv/bin/python
[[ -x "$PY" ]] || PY=./.venv/bin/python   # uv's default venv name on the Mac clone
mkdir -p data
"$PY" offset_pdf.py --save "$@"
# offset_pdf.py writes the engine clone's (gitignored) data/offset_data.json; keep the
# tracked copy in this repo's data/ as the source of truth so it follows the printer
# across machines. make-proxies.sh copies it back before every build.
if [[ -f data/offset_data.json ]]; then
  cp -f data/offset_data.json "$ROOT/data/offset_data.json"
  echo "offset saved to $ROOT/data/offset_data.json — commit it."
fi
