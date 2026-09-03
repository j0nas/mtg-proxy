#!/usr/bin/env bash
# cut-proxies.sh — send a proxy sheet's card outlines to the Cameo 5 Alpha directly,
# bypassing Silhouette Studio (inkscape-silhouette's pure-Python USB/BLE driver).
#
# Why: Studio + Alpha firmware 1.05 mis-detect the machine and pick the wrong
# registration-mark scan (3-mark vs 4-mark) on its own. Here the scan command is
# chosen explicitly (-r 4 → TB124 four L-marks, -r 3 → TB123 square + 2 L-marks),
# the mark geometry comes from the same layouts.json that placed the marks in
# the PDF, and force/speed/depth/passes are plain flags.
#
# Usage:
#   ./cut-proxies.sh                  # A4, standard cards, 4-mark, USB, F25/S25/D5/P3
#   ./cut-proxies.sh --ble            # over Bluetooth LE (no cable, no pairing)
#   ./cut-proxies.sh --scan           # list nearby BLE devices, then stop
#   ./cut-proxies.sh --dry-run        # build the SVG + command transcript, send nothing
#   ./cut-proxies.sh --preview        # show the cut paths in a window before sending
#
# Options:
#   -p, --paper SIZE       a4 (default) | letter | ... (any silhouette-card-maker paper)
#   -c, --card SIZE        standard (default) | poker | ...
#   -r, --registration N   4 (default) | 3 — MUST match the marks printed on the sheet
#       --force N          blade force 1..40   (default 25)
#       --speed N          speed 1..30         (default 25)
#       --depth N          AutoBlade depth 0..10 (default 5)
#       --passes N         1..8               (default 3)
#       --y-off MM         shift cuts down by MM (default: data/cut_offset.json y_mm,
#                          the ~1mm-high bias measured with Studio; use 0 to disable)
#       --x-off MM         shift cuts right by MM (default: data/cut_offset.json x_mm)
#       --ble [NAME]       connect over BLE (advertised name, default "CAMEO 5 ALPHA")
#       --usb              connect over USB (default)
#       --svg FILE         cut this SVG instead of generating one (page-sized, mm)
#   -n, --dry-run          no machine needed; writes output/cut/<name>.cmds
#       --preview          matplotlib preview window before sending
#       --                 pass the rest straight to sendto_silhouette.py
#
# Sheet on the mat exactly as for Studio: top-left of the mat grid, aligned to the
# paper edge, mat against the left notch, lid closed. The machine scans the marks
# itself; the job aborts with "Couldn't find registration marks" if it can't.
#
# First run: use the placeholder sheet from `make-proxies --test` and read the
# 0.5mm gauge ticks — tune --y-off/--x-off from that, then bake the result into
# data/cut_offset.json (shared with the Studio path).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCM="$ROOT/silhouette-card-maker"
SCM_PY="$SCM/venv/bin/python"
DRV="$ROOT/inkscape-silhouette"
DRV_PY="$DRV/.venv/bin/python"

PAPER="a4"
CARD="standard"
REG="4"
FORCE=25
SPEED=25
DEPTH=5
PASSES=3
XOFF=""
YOFF=""
CONN="usb"
BLE_NAME="CAMEO 5 ALPHA"
SVG=""
DRY=0
PREVIEW=0
SCAN=0
EXTRA=()

usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--paper)        PAPER="$2"; shift 2 ;;
    -c|--card)         CARD="$2"; shift 2 ;;
    -r|--registration) REG="$2"; shift 2 ;;
    --force)           FORCE="$2"; shift 2 ;;
    --speed)           SPEED="$2"; shift 2 ;;
    --depth)           DEPTH="$2"; shift 2 ;;
    --passes)          PASSES="$2"; shift 2 ;;
    --y-off)           YOFF="$2"; shift 2 ;;
    --x-off)           XOFF="$2"; shift 2 ;;
    --ble)             CONN="ble"
                       if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then BLE_NAME="$2"; shift; fi
                       shift ;;
    --usb)             CONN="usb"; shift ;;
    --scan)            SCAN=1; CONN="ble"; shift ;;
    --svg)             SVG="$2"; shift 2 ;;
    -n|--dry-run)      DRY=1; shift ;;
    --preview)         PREVIEW=1; shift ;;
    -h|--help)         usage ;;
    --)                shift; EXTRA=("$@"); break ;;
    *)                 echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

[[ "$REG" == 3 || "$REG" == 4 ]] || { echo "error: --registration must be 3 or 4" >&2; exit 1; }
[[ -x "$SCM_PY" ]] || { echo "error: silhouette-card-maker venv missing at $SCM_PY" >&2; exit 1; }
[[ -x "$DRV_PY" ]] || {
  echo "error: inkscape-silhouette venv missing. Set it up with:" >&2
  echo "  git clone https://github.com/fablabnbg/inkscape-silhouette $DRV" >&2
  echo "  cd $DRV && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt libusb1 bleak && uv pip install --python .venv/bin/python --no-deps inkex" >&2
  echo "  (libusb itself: brew formula 'libusb', managed in the dotfiles)" >&2
  exit 1
}

OUT="$ROOT/output/cut"
mkdir -p "$OUT"

# BLE scan: list devices and stop.
if [[ $SCAN -eq 1 ]]; then
  exec "$DRV_PY" "$DRV/sendto_silhouette.py" --connection_type=ble --bluetooth_scan=True --preview False "$DRV/examples/testcut_square_triangle.svg"
fi

# 1. Cut geometry from the card-maker's own layout engine (same source as the PDF).
NAME="$PAPER-$CARD"
if [[ -z "$SVG" ]]; then
  SVG="$OUT/$NAME.svg"
  GEOM="$("$SCM_PY" "$ROOT/tools/cut_svg.py" --paper "$PAPER" --card_size "$CARD" --out "$SVG")"
else
  [[ -f "$SVG" ]] || { echo "error: SVG not found: $SVG" >&2; exit 1; }
  NAME="$(basename "${SVG%.*}")"
  GEOM="$("$SCM_PY" "$ROOT/tools/cut_svg.py" --paper "$PAPER" --card_size "$CARD" --out /dev/null)"
fi
read -r REG_X REG_Y INSET CARDS <<<"$("$SCM_PY" -c 'import json,sys; g=json.loads(sys.argv[1]); print(g["reg_x_mm"], g["reg_y_mm"], g["reg_inset_mm"], g["cards"])' "$GEOM")"

# 2. Machine cut bias (measured with Studio; same file the .studio3 path uses).
if [[ -z "$YOFF" || -z "$XOFF" ]]; then
  read -r CFG_X CFG_Y <<<"$("$SCM_PY" -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
print(d.get("x_mm",0), d.get("y_mm",0))' "$ROOT/data/cut_offset.json")"
  [[ -n "$XOFF" ]] || XOFF="$CFG_X"
  [[ -n "$YOFF" ]] || YOFF="$CFG_Y"
fi

QUAD=False; [[ "$REG" == 4 ]] && QUAD=True
PREV=False; [[ $PREVIEW -eq 1 ]] && PREV=True
DRYF=False; [[ $DRY -eq 1 ]] && DRYF=True

ARGS=(
  --preview "$PREV"
  --dry_run "$DRYF"
  --tool autoblade
  --pressure "$FORCE" --speed "$SPEED" --depth "$DEPTH" --multipass "$PASSES"
  --cuttingmat cameo_12x12
  --regmark True --regsearch True --quadregmarks "$QUAD"
  --reg-x "$REG_X" --reg-y "$REG_Y" --rego-x "$INSET" --rego-y "$INSET"
  --x-off "$XOFF" --y-off "$YOFF"
  --endposition below
  --logfile "$OUT/$NAME.log"
  --cmdfile "$OUT/$NAME.cmds"
)
if [[ "$CONN" == "ble" ]]; then
  ARGS+=(--connection_type ble --bluetooth_name "$BLE_NAME")
fi
if [[ $DRY -eq 1 ]]; then
  # No device to answer the firmware query in a dry run: pin the model so the
  # transcript shows the Alpha's real command set (TB124 for 4-mark).
  ARGS+=(--force_hardware Silhouette_Cameo5_Alpha)
fi

echo "cut-proxies: $NAME — $CARDS cards, $REG-mark registration (marks inset ${INSET}mm, ${REG_X}x${REG_Y}mm apart)"
echo "cut-proxies: force $FORCE · speed $SPEED · depth $DEPTH · passes $PASSES · offset x=${XOFF}mm y=${YOFF}mm · via $CONN"
[[ $DRY -eq 1 ]] && echo "cut-proxies: DRY RUN — nothing is sent; transcript in $OUT/$NAME.cmds"

set +e
if [[ $DRY -eq 1 ]]; then
  # The dry run necessarily dies at the registration scan (no machine answers), so
  # its traceback is noise; keep it in a file and judge the transcript instead.
  "$DRV_PY" "$DRV/sendto_silhouette.py" "${ARGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} "$SVG" 2>"$OUT/$NAME.stderr"
else
  "$DRV_PY" "$DRV/sendto_silhouette.py" "${ARGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} "$SVG"
fi
RC=$?
set -e

if [[ $DRY -eq 1 ]]; then
  WANT="TB123"; [[ "$REG" == 4 ]] && WANT="TB124"
  CMD="$(grep -o 'TB12[34],[0-9,]*' "$OUT/$NAME.cmds" 2>/dev/null | head -1 || true)"
  if [[ "$CMD" == "$WANT"* ]]; then
    echo "cut-proxies: dry run OK — regmark scan command $CMD ($REG-mark), transcript $OUT/$NAME.cmds"
    exit 0
  fi
  echo "cut-proxies: dry run did not produce the expected $WANT scan (got '${CMD:-nothing}') — see $OUT/$NAME.stderr" >&2
  exit 1
fi

if [[ $RC -ne 0 ]]; then
  echo "cut-proxies: FAILED (exit $RC) — log: $OUT/$NAME.log" >&2
  if grep -q "registration marks" "$OUT/$NAME.log" 2>/dev/null; then
    echo "cut-proxies: the machine could not find the marks. Check: sheet printed at 100% (marks ${INSET}mm from the paper edge)," >&2
    echo "cut-proxies: -r $REG matches the printed pattern, sheet top-left on the mat, lid closed, no glare." >&2
  fi
  exit "$RC"
fi
echo "cut-proxies: done — $CARDS cards cut. Don't eject yet: lift a corner and rerun with more --passes if needed."
