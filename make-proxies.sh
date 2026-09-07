#!/usr/bin/env bash
# make-proxies.sh — decklist in, print-ready PDF + Silhouette cut file out.
#
# Usage:
#   ./make-proxies.sh decks/mydeck.txt [options]      # mtga format; bare card name = 1 copy
#   ./make-proxies.sh https://moxfield.com/decks/<id> # fetch from Moxfield (saved to decks/)
#   ./make-proxies.sh <moxfield url> --board considering   # just its "Considering" board
#   ./make-proxies.sh --test                          # ink-light placeholder sheet, no Scryfall
#   ./make-proxies.sh --notes [name]                  # CUT-NOTES of a previous run (default: latest)
#
# Options:
#   -f, --format FMT      decklist format (default: mtga)
#   -o, --out DIR         parent dir for <deckname>/ (default: current directory)
#   -p, --paper SIZE      a4 (default) | letter
#   -r, --registration N  4 (default) | 3 registration marks
#   --board NAME          Moxfield board: main (default) | side | considering
#   --backs               double-sided cards (default: fronts only)
#   --basics              include basic lands (skipped by default)
#   -t, --tokens [N]      also N of each distinct token (default 2; no emblems)
#   --tokens-only [N]     ONLY the tokens, no deck cards
#   --split-faces         each DFC face as its own card (default: separate manual-duplex PDF)
#   --plain               normal frames (default prefers showcase/extended/full art)
#   --skip-fetch          reuse already-downloaded card images
#   --print [PRINTER]     send the PDF straight to CUPS at 100%. Real sheets go to the
#                         printer instance EPSON_ET_8550_Series/4x2-glossy (the Windows
#                         "4x2 Glossy" preset; MTG_PROXY_LP_INSTANCE overrides), --test
#                         sheets to the default printer with plain-paper defaults.
#                         Extra lp options via MTG_PROXY_LP_OPTS.
#   -- <args>             extra fetch.py args (e.g. -- --prefer_set sld)
#
# Output goes to ./<deckname>/ in the CURRENT directory (-o DIR to choose another parent;
# the repo itself is an implementation detail): <deckname>.pdf (print at 100%, NO
# borderless), <deckname>-duplex.pdf (DFCs, manual duplex), <template>+y1mm.studio3
# (open in Studio; cut offset from data/cut_offset.json pre-applied), CUT-NOTES.md.
# Under WSL the files are also mirrored FLAT to C:\Users\jonas\Desktop\projects\mtg-proxy
# (override: MTG_PROXY_WIN_OUT). Decklist paths are relative to the current directory too.
#
# The template base (templates/a4-standard-v5-alpha.studio3) bakes in the Studio settings:
# Cameo 5 Alpha machine, material, A4 media (must be A4 — "Custom" media warps cuts).
# To bake changed settings: open a generated *+y1mm.studio3 in Studio, change settings
# only (never move shapes), save, then rebase it offset-free (details: README §2¾):
#   silhouette-card-maker/venv/bin/python tools/rebase_template.py \
#     '/mnt/c/Users/jonas/Desktop/projects/mtg-proxy/a4-standard-v5-alpha+y1mm.studio3' \
#     silhouette-card-maker/cutting_templates/a4-standard-v5.studio3 \
#     templates/a4-standard-v5-alpha.studio3

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCM="$ROOT/silhouette-card-maker"
PY="$SCM/venv/bin/python"
[[ -x "$PY" ]] || PY="$SCM/.venv/bin/python"   # uv's default venv name on the Mac clone

FORMAT="mtga"
OUT_PARENT="$PWD"
PAPER="a4"
BOARD="main"
REG="4"
CARD_SIZE="standard"
FRONTS_ONLY=1
DUPLEX_DFC=1
FANCY_ART=1
INCLUDE_BASICS=0
TOKEN_COPIES=0
TOKENS_ONLY=0
SKIP_FETCH=0
TEST_MODE=0
PRINT_MODE=0
PRINTER=""
NOTES_MODE=0
NOTES_NAME=""
FETCH_ARGS=()
DECK=""

usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--format)      FORMAT="$2"; shift 2 ;;
    -o|--out)         OUT_PARENT="$2"; shift 2 ;;
    -p|--paper)       PAPER="$2"; shift 2 ;;
    -r|--registration) REG="$2"; shift 2 ;;
    --board)          BOARD="$2"; shift 2 ;;
    --fronts-only)    FRONTS_ONLY=1; shift ;;
    --backs|--double-sided) FRONTS_ONLY=0; shift ;;
    --basics)         INCLUDE_BASICS=1; shift ;;
    --duplex)         DUPLEX_DFC=1; shift ;;
    --split-faces)    DUPLEX_DFC=0; shift ;;
    --plain)          FANCY_ART=0; shift ;;
    -t|--tokens)      TOKEN_COPIES=2
                      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then TOKEN_COPIES="$2"; shift; fi
                      shift ;;
    --tokens-only)    TOKENS_ONLY=1; TOKEN_COPIES=2
                      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then TOKEN_COPIES="$2"; shift; fi
                      shift ;;
    --skip-fetch)     SKIP_FETCH=1; shift ;;
    --test)           TEST_MODE=1; shift ;;
    --print)          PRINT_MODE=1
                      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then PRINTER="$2"; shift; fi
                      shift ;;
    --notes)          NOTES_MODE=1
                      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then NOTES_NAME="$2"; shift; fi
                      shift ;;
    -h|--help)        usage ;;
    --)               shift; FETCH_ARGS=("$@"); break ;;
    -*)               echo "unknown option: $1" >&2; usage 1 ;;
    *)                DECK="$1"; shift ;;
  esac
done

# --notes [name]: display the CUT-NOTES for a previous run (latest run if no name)
if [[ $NOTES_MODE -eq 1 ]]; then
  if [[ -z "$NOTES_NAME" ]]; then
    # newest CUT-NOTES.md in any subfolder of the output parent (default: cwd)
    NOTES_LATEST="$(find "$OUT_PARENT" -mindepth 2 -maxdepth 2 -name CUT-NOTES.md -print0 2>/dev/null \
      | xargs -0 ls -1t 2>/dev/null | head -1)"
    [[ -n "$NOTES_LATEST" ]] || { echo "error: no make-proxies output under $OUT_PARENT" >&2; exit 1; }
    NOTES_NAME="$(basename "$(dirname "$NOTES_LATEST")")"
  fi
  NOTES_FILE="$OUT_PARENT/$NOTES_NAME/CUT-NOTES.md"
  [[ -f "$NOTES_FILE" ]] || { echo "error: no notes for '$NOTES_NAME' ($NOTES_FILE)" >&2; exit 1; }
  if command -v glow >/dev/null 2>&1; then glow -p "$NOTES_FILE"; else cat "$NOTES_FILE"; fi
  exit 0
fi

[[ $TOKENS_ONLY -eq 1 && $TEST_MODE -eq 1 ]] && { echo "error: --tokens-only cannot be combined with --test" >&2; exit 1; }

[[ -x "$PY" ]] || { echo "error: venv missing — run: cd $SCM && uv venv venv && uv pip install -p venv/bin/python -r requirements.txt" >&2; exit 1; }

if [[ $TEST_MODE -eq 1 ]]; then
  NAME="test-sheet"
else
  [[ -n "$DECK" ]] || { echo "error: no decklist given" >&2; usage 1; }
  # A Moxfield URL as the decklist: pull the deck (or one board of it, --board
  # main|side|considering) from their JSON API into decks/ and continue with
  # that file. Exists because Moxfield itself cannot export the "Considering"
  # board. Exact printings are preserved (set + collector number per line).
  if [[ "$DECK" == *moxfield.com/decks/* ]]; then
    DECK="$("$PY" "$ROOT/tools/fetch_moxfield.py" "$DECK" --board "$BOARD" --to "$ROOT/decks")"
    FORMAT="mtga"
  fi
  # Accept absolute Windows paths (C:\...) pasted from Explorer
  if [[ ! -f "$DECK" ]] && command -v wslpath >/dev/null 2>&1; then
    CONV="$(wslpath -u "$DECK" 2>/dev/null || true)"
    [[ -n "$CONV" && -f "$CONV" ]] && DECK="$CONV"
  fi
  [[ -f "$DECK" ]] || { echo "error: decklist not found: $DECK" >&2; exit 1; }
  DECK="$(readlink -f "$DECK")"
  NAME="$(basename "$DECK")"; NAME="${NAME%.*}"
  [[ $TOKENS_ONLY -eq 1 ]] && NAME="$NAME-tokens"
fi
# Output folder next to where you are. Only OUR artifacts from a previous run are
# removed (never the folder itself — it's in the user's space now, not the repo's).
OUT_PARENT="$(cd "$OUT_PARENT" 2>/dev/null && pwd)" || { echo "error: --out dir not found" >&2; exit 1; }
OUT="$OUT_PARENT/$NAME"
mkdir -p "$OUT"
rm -f "$OUT/$NAME.pdf" "$OUT/$NAME-duplex.pdf" "$OUT/CUT-NOTES.md" "$OUT"/*.studio3 2>/dev/null || true

if [[ "$PAPER" == "a3" ]]; then
  echo "NOTE: A3 sheets need the 12x24\" cutting mat — the standard 12x12\" mat is too short." >&2
fi

cd "$SCM"

# 1. Fresh image dirs (clean_up.py empties front/ and double_sided/ — and crashes
#    if a fresh clone/checkout is missing them, so create them first)
mkdir -p game/front game/double_sided game/back game/output
if [[ $TEST_MODE -eq 1 ]]; then
  # Placeholder mode: ink-light 63x88mm gauge cards, no Scryfall, light test back
  "$PY" clean_up.py
  rm -f game/back/*.png game/back/*.jpg 2>/dev/null || true
  case "$PAPER" in
    a4|letter) COUNT=8 ;;
    tabloid)   COUNT=16 ;;
    a3|arch_b) COUNT=18 ;;
    *)         COUNT=8 ;;
  esac
  TEST_ARGS=(--count "$COUNT")
  # Double-sided test sheet: number each back so you can see which front it
  # belongs to (verifies back-page ordering, not just the offset).
  [[ $FRONTS_ONLY -eq 0 ]] && TEST_ARGS+=(--per_card_backs)
  "$PY" "$ROOT/tools/make_test_cards.py" "${TEST_ARGS[@]}"
elif [[ $SKIP_FETCH -eq 0 ]]; then
  "$PY" clean_up.py

  # 2. Card back: default proxy back unless the user dropped a custom one in assets/
  rm -f game/back/*.png game/back/*.jpg 2>/dev/null || true
  cp "$ROOT/assets/back.png" game/back/back.png

  # 3. Fetch card images from Scryfall (basic lands skipped unless --basics)
  BASIC_ARGS=()
  [[ $INCLUDE_BASICS -eq 0 ]] && BASIC_ARGS=(--skip_basics)
  TOKEN_ARGS=()
  [[ $TOKEN_COPIES -gt 0 ]] && TOKEN_ARGS=(--tokens --token_copies "$TOKEN_COPIES")
  ART_ARGS=()
  [[ $FANCY_ART -eq 1 ]] && ART_ARGS=(--prefer_showcase --prefer_extra_art)
  "$PY" plugins/mtg/fetch.py "$DECK" "$FORMAT" ${BASIC_ARGS[@]+"${BASIC_ARGS[@]}"} ${TOKEN_ARGS[@]+"${TOKEN_ARGS[@]}"} ${ART_ARGS[@]+"${ART_ARGS[@]}"} ${FETCH_ARGS[@]+"${FETCH_ARGS[@]}"}
else
  echo "--skip-fetch: reusing images already in game/front/"
fi

# --tokens-only: tokens are fetched alongside the deck (fetch.py has no
# tokens-only mode), so drop every non-token image afterwards. Token images are
# named <index><CardName>_token<n>.png (see plugins/mtg/scryfall.py).
if [[ $TOKENS_ONLY -eq 1 ]]; then
  PRUNED=0
  for f in game/front/* game/double_sided/*; do
    [[ -f "$f" ]] || continue
    case "$(basename "$f")" in
      *_token*) ;;
      *) rm -f "$f"; PRUNED=$((PRUNED + 1)) ;;
    esac
  done
  echo "--tokens-only: dropped $PRUNED non-token image(s)"
  [[ $(find game/front -type f | wc -l) -gt 0 ]] || { echo "error: this deck produces no tokens" >&2; exit 1; }
fi

FRONT_COUNT=$(find game/front -type f | wc -l)
[[ $FRONT_COUNT -gt 0 ]] || { echo "error: no card images in game/front/" >&2; exit 1; }
echo "cards fetched: $FRONT_COUNT"

# 4. Build the PDF(s).
#    --extend_corners 3.5mm: Scryfall scans have rounded corners; this fills the
#    corner bleed so cut cards don't get white corner slivers.
#    Saved duplex offset (data/offset_data.json, from offset_pdf.py --save) is
#    applied automatically to any double-sided output.
PDF_ARGS=(
  --card_size "$CARD_SIZE"
  --paper_size "$PAPER"
  --registration "$REG"
  --extend_corners 3.5mm
)
OFFSET_ARGS=()
[[ -f data/offset_data.json ]] && OFFSET_ARGS=(--load_offset)

DFC_COUNT=0
if [[ $FRONTS_ONLY -eq 1 ]]; then
  # Symlink views leave game/* untouched (safe for --skip-fetch).
  VIEW="$(mktemp -d)"
  trap 'rm -rf "$VIEW"' EXIT
  mkdir -p "$VIEW/main_front" "$VIEW/dfc_front" "$VIEW/no_backs"
  for f in game/front/*; do
    base="$(basename "$f")"
    if [[ $DUPLEX_DFC -eq 1 && -f "game/double_sided/$base" ]]; then
      ln -s "$SCM/$f" "$VIEW/dfc_front/$base"
    else
      ln -s "$SCM/$f" "$VIEW/main_front/$base"
    fi
  done
  if [[ $DUPLEX_DFC -eq 0 ]]; then
    # Default: each face of a double-faced card becomes its own single-sided
    # card in the main PDF (front stays in place, back slots in next to it).
    for f in game/double_sided/*.png game/double_sided/*.jpg; do
      [[ -f "$f" ]] || continue
      base="$(basename "$f")"
      ln -s "$SCM/$f" "$VIEW/main_front/${base%.*}-back.${base##*.}"
      DFC_COUNT=$((DFC_COUNT + 1))
    done
    [[ $DFC_COUNT -gt 0 ]] && echo "double-faced cards: $DFC_COUNT — printing both faces as separate cards (--split-faces)"
  else
    DFC_COUNT=$(find "$VIEW/dfc_front" -type l | wc -l)
  fi
  MAIN_COUNT=$(find "$VIEW/main_front" -type l | wc -l)

  if [[ $MAIN_COUNT -gt 0 ]]; then
    "$PY" create_pdf.py "${PDF_ARGS[@]}" --only_fronts \
      --front_dir_path "$VIEW/main_front" --double_sided_dir_path "$VIEW/no_backs"
    cp game/output/game.pdf "$OUT/$NAME.pdf"
  else
    echo "NOTE: every card in this deck is double-sided — no fronts-only PDF to build."
  fi

  if [[ $DUPLEX_DFC -eq 1 && $DFC_COUNT -gt 0 ]]; then
    echo "double-sided cards: $DFC_COUNT — building $NAME-duplex.pdf"
    if [[ ${#OFFSET_ARGS[@]} -eq 0 ]]; then
      echo "NOTE: no saved duplex offset — run the calibration once for clean front/back alignment (see README)."
    fi
    "$PY" create_pdf.py "${PDF_ARGS[@]}" ${OFFSET_ARGS[@]+"${OFFSET_ARGS[@]}"} \
      --front_dir_path "$VIEW/dfc_front" --output_path game/output/duplex.pdf
    cp game/output/duplex.pdf "$OUT/$NAME-duplex.pdf"
  fi
else
  # --backs: one double-sided PDF; DFCs get their real backs, the rest assets/back.png
  if [[ ${#OFFSET_ARGS[@]} -gt 0 ]]; then
    echo "applying saved duplex offset from data/offset_data.json"
  else
    echo "NOTE: no saved duplex offset — run the calibration once before double-sided decks (see README)."
  fi
  "$PY" create_pdf.py "${PDF_ARGS[@]}" ${OFFSET_ARGS[@]+"${OFFSET_ARGS[@]}"}
  cp game/output/game.pdf "$OUT/$NAME.pdf"
fi

# 5. Cutting template: prefer the project base (templates/ — a Studio save
#    rebased offset-free by tools/rebase_template.py, so it opens with the
#    Cameo 5 Alpha machine profile and Page Setup already baked in), falling
#    back to the vendored stock template for sizes without one. Then apply
#    the machine cut offset from data/cut_offset.json — the Cameo 5 Alpha cuts
#    ~1mm high relative to the registration marks it scans, so the cut paths
#    are pre-shifted at build time (tools/offset_studio3.py). The placed
#    template carries the offset in its name (e.g. ...+y1mm.studio3).
TEMPLATE_BAKED=0
TEMPLATE="$(find "$ROOT/templates" -name "$PAPER-$CARD_SIZE-*.studio3" 2>/dev/null | sort -V | tail -1)"
if [[ -n "$TEMPLATE" ]]; then
  TEMPLATE_BAKED=1
else
  TEMPLATE="$(find cutting_templates -maxdepth 1 -name "$PAPER-$CARD_SIZE-v*.studio3" | sort -V | tail -1)"
fi
if [[ -n "$TEMPLATE" ]]; then
  TEMPLATE="$("$PY" "$ROOT/tools/offset_studio3.py" "$TEMPLATE" "$OUT")"
else
  echo "warning: no cutting template found for $PAPER/$CARD_SIZE" >&2
fi
CUT_OFF_Y="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("y_mm", 0))' "$ROOT/data/cut_offset.json" 2>/dev/null || echo 0)"

cat > "$OUT/CUT-NOTES.md" <<EOF
# $NAME — print & cut checklist

Generated: $(date +%F) | paper: $PAPER | card: $CARD_SIZE | registration: $REG-mark | cards: $FRONT_COUNT | sides: $( [[ $FRONTS_ONLY -eq 1 ]] && echo 'fronts only' || echo 'double-sided' )$( [[ $DFC_COUNT -gt 0 ]] && { [[ $DUPLEX_DFC -eq 1 ]] && echo " | double-faced cards: $DFC_COUNT (separate duplex PDF)" || echo " | double-faced cards: $DFC_COUNT (both faces as separate cards)"; } )

## Print (your usual ET-8550 profile)
1. **Actual size / 100%** scale, borderless **OFF** — any scaling shifts the registration
   marks off their expected positions and registration fails.
2. $( [[ $FRONTS_ONLY -eq 1 ]] && echo 'Fronts only (default) — print single-sided. Rerun with --backs for double-sided.' || echo 'Double-sided = manual duplex, **long-edge flip**. Check front/back alignment against a light before laminating a whole batch.' )$( [[ $DFC_COUNT -gt 0 && $DUPLEX_DFC -eq 1 ]] && printf '\n   - **%s** holds the %s double-sided card image(s): print that file with manual duplex\n     (**long-edge flip**), then laminate and cut it like any other sheet — same template,\n     same machine profile, same cut settings.' "$NAME-duplex.pdf" "$DFC_COUNT" )$( [[ $DFC_COUNT -gt 0 && $DUPLEX_DFC -eq 0 && $FRONTS_ONLY -eq 1 ]] && printf '\n   - %s double-faced card(s): both faces are in the main PDF as separate single-sided\n     cards (--split-faces). Rerun without it for one physical double-sided card.' "$DFC_COUNT" )
3. Let ink dry before laminating.

## Laminate
- 80 µm pouches; run one grade hotter than the pouch rating if lamination looks cloudy.
- Feed the sealed edge first. Re-laminate cut cards once more at the end to seal edges.

## Cut (Cameo 5 Alpha)
**Preferred — no Studio:** with the Cameo on USB or Bluetooth from the Mac, run
\`cut-proxies -r $REG$( [[ "$PAPER" != a4 ]] && printf ' -p %s' "$PAPER" )\` (4-mark scan is sent explicitly; see README §5).
Studio fallback:
1. Open \`$(basename "${TEMPLATE:-<template>}")\` in Silhouette Studio (Studio **v5.0.402+** needed for the Alpha;
   the limited "Starter" edition of Studio v5 is incompatible — use the full edition).
2. $( if [[ "$REG" == 4 && $TEMPLATE_BAKED -eq 1 ]]; then
  echo 'Machine & media: the template opens with **Cameo 5 Alpha** selected and media set
   to **A4** (both baked into the template base) — verify, change nothing else. If media
   ever reads "Custom", switch it to A4: Custom media warps the cuts.'
else
  echo "Set the machine profile MANUALLY to match this PDF's marks — auto-detect picks wrong on some
   firmware/Studio combos: this PDF is **${REG}-mark**, so select **$( [[ "$REG" == 4 ]] && echo 'Cameo 5 Alpha' || echo 'Cameo 5 (yes, plain 5 — 3-mark PDFs need the old profile even on Alpha hardware)' )**."
fi )$( [[ "$CUT_OFF_Y" != "0" && "$CUT_OFF_Y" != "0.0" ]] && printf '\n   - Cut paths in this template are pre-shifted **%smm down** (the machine cuts high\n     relative to the scanned marks). Do NOT nudge shapes in Studio — tune the number in\n     data/cut_offset.json instead and rerun.' "$CUT_OFF_Y" )
3. Sheet on mat: top-left of the mat grid, aligned to the *paper* edge, not the laminate edge.
4. Post-it trick (light-colored, remove after registration scan, before cutting):
   $( [[ "$REG" == 4 ]] && echo '4-mark pattern: cover the cards nearest BOTH bottom corners.' || echo '3-mark pattern: cover the card nearest the bottom-left L mark.' )
5. Starting cut settings (AutoBlade, 135 gsm photo paper + 80 µm matte laminate):
   **Force 25 · Speed 25 · Depth 5 · Passes 3** (cut-proxies defaults)
   Tune passes first, then force. Rippled/torn edges = force too high or blade dull.
EOF

# 5½. Optional: print via CUPS at exact size. Scaling is what breaks registration, so
#    print-scaling=none is forced; media is A4/letter to match the PDF. The duplex PDF
#    is deliberately NOT sent — manual duplex on photo paper is a hands-on job.
if [[ $PRINT_MODE -eq 1 && -f "$OUT/$NAME.pdf" ]]; then
  if ! command -v lp >/dev/null 2>&1; then
    echo "warning: --print needs CUPS (lp) — not available here, print $OUT/$NAME.pdf by hand" >&2
  else
    case "$PAPER" in
      a4)     LP_MEDIA="A4" ;;
      letter) LP_MEDIA="Letter" ;;
      a3)     LP_MEDIA="A3" ;;
      *)      LP_MEDIA="$PAPER" ;;
    esac
    # shellcheck disable=SC2206  # MTG_PROXY_LP_OPTS is a deliberate word-split list of lp options
    LP_EXTRA=(${MTG_PROXY_LP_OPTS:-})
    LP_ARGS=(-o media="$LP_MEDIA" -o print-scaling=none -o fit-to-page=false)
    if [[ $TEST_MODE -eq 1 ]]; then
      # Test sheet: printer defaults on plain paper — only the size and "no scaling" matter.
      LP_DEST=(); [[ -n "$PRINTER" ]] && LP_DEST=(-d "$PRINTER")
      LP_DESC="${PRINTER:-default printer}, plain paper / driver defaults"
    else
      # Real sheets: the "4x2 Glossy" preset from the Windows driver, kept on the Mac as
      # the CUPS printer instance EPSON_ET_8550_Series/4x2-glossy (~/.cups/lpoptions,
      # chezmoi-managed; docs/printer-presets/README.md). Explicit options if the
      # instance isn't there (other machine, or -p PRINTER given).
      INSTANCE="${MTG_PROXY_LP_INSTANCE:-EPSON_ET_8550_Series/4x2-glossy}"
      if [[ -z "$PRINTER" ]] && grep -qs "^Dest $INSTANCE " "$HOME/.cups/lpoptions"; then
        LP_DEST=(-d "$INSTANCE"); LP_DESC="$INSTANCE (rear feeder, glossy photo, High)"
      else
        LP_DEST=(); [[ -n "$PRINTER" ]] && LP_DEST=(-d "$PRINTER")
        LP_ARGS+=(-o MediaType=photographic-glossy -o InputSlot=rear -o cupsPrintQuality=High -o ColorModel=RGB)
        LP_DESC="${PRINTER:-default printer}, explicit glossy options"
      fi
    fi
    echo "printing $NAME.pdf → $LP_DESC (media=$LP_MEDIA, 100%, no scaling)"
    lp ${LP_DEST[@]+"${LP_DEST[@]}"} "${LP_ARGS[@]}" ${LP_EXTRA[@]+"${LP_EXTRA[@]}"} "$OUT/$NAME.pdf"
    [[ -f "$OUT/$NAME-duplex.pdf" ]] && echo "NOTE: $NAME-duplex.pdf not sent — print it by hand with manual duplex (long-edge flip)."
  fi
fi

# 6. Mirror the output to the Windows side (real copies — symlinks don't cross
#    the WSL/Windows boundary). Files land FLAT in the Windows proxy folder —
#    no per-deck subfolder. Override the target with MTG_PROXY_WIN_OUT. The
#    deck-agnostic CUT-NOTES.md is mirrored as <deck>-CUT-NOTES.md so different
#    decks don't clobber each other's notes.
WIN_OUT="${MTG_PROXY_WIN_OUT:-/mnt/c/Users/jonas/Desktop/projects/mtg-proxy}"
WIN_NOTE=""
MIRROR_FAILED=()
if [[ -d "$(dirname "$WIN_OUT")" || -d /mnt/c/Users ]]; then
  mkdir -p "$WIN_OUT"
  # remove a stale duplex PDF from a previous run; other files get overwritten
  [[ -f "$OUT/$NAME-duplex.pdf" ]] || rm -f "$WIN_OUT/$NAME-duplex.pdf"
  MIRROR_FAILED=()
  # Wipe this run's previous artifacts first (plus templates — regenerated
  # fresh every run, and stale ones are dangerous to cut with). CUT-NOTES.md
  # is not mirrored; view it with: make-proxies --notes <name>
  rm -f "$WIN_OUT/$NAME.pdf" "$WIN_OUT/$NAME-duplex.pdf" "$WIN_OUT/$NAME-CUT-NOTES.md" "$WIN_OUT"/*.studio3 2>/dev/null || true
  for f in "$OUT"/*; do
    base="$(basename "$f")"
    [[ "$base" == "CUT-NOTES.md" ]] && continue
    cp -f "$f" "$WIN_OUT/$base" 2>/dev/null || MIRROR_FAILED+=("$base")
  done
  WIN_NOTE="$(echo "$WIN_OUT" | sed 's|^/mnt/c|C:|; s|/|\\|g')"
fi

echo
echo "=== DONE ==="
if [[ -f "$OUT/$NAME.pdf" ]]; then echo "  PDF:      $OUT/$NAME.pdf"; fi
if [[ -f "$OUT/$NAME-duplex.pdf" ]]; then echo "  Duplex:   $OUT/$NAME-duplex.pdf ($DFC_COUNT double-sided cards — manual duplex, long-edge flip)"; fi
if [[ -n "$TEMPLATE" ]]; then echo "  Cut file: $OUT/$(basename "$TEMPLATE")"; fi
echo "  Notes:    make-proxies --notes $NAME"
echo "  Cut:      cut-proxies -r $REG$( [[ "$PAPER" != a4 ]] && printf ' -p %s' "$PAPER" )   (direct to the Cameo, no Studio)"
if [[ -n "$WIN_NOTE" ]]; then echo "  Windows:  $WIN_NOTE"; fi
if [[ ${#MIRROR_FAILED[@]} -gt 0 ]]; then
  echo
  echo "  *** MIRROR INCOMPLETE — the Windows copy of these files is STALE (old run!):"
  for f in "${MIRROR_FAILED[@]}"; do echo "  ***   $f"; done
  echo "  *** Close the file on the Windows side (PDF viewer / Silhouette Studio) and rerun,"
  echo "  *** or copy it manually from the WSL output path above. Do NOT print the stale copy."
  exit 1
fi
