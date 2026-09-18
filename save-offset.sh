#!/usr/bin/env bash
# save-offset.sh — shim: `mtg-proxy offset -x <x> -y <y> [-a <deg>]` (see README §3).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --quiet --project "$ROOT" mtg-proxy offset "$@"
