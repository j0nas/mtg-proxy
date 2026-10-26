#!/usr/bin/env bash
# print-sheet.sh — shim: `mtg-proxy print ...` (see README). Prints a PDF through the
# Windows PC's Epson queues: print-sheet.sh PDF [--paper plain|glossy|premium] [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --quiet --project "$ROOT" mtg-proxy print "$@"
