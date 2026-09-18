#!/usr/bin/env bash
# cut-proxies.sh — shim: `mtg-proxy cut ...` (see README §5). Run it inside a deck's
# output folder and it reads run.json for paper/card/registration.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --quiet --project "$ROOT" mtg-proxy cut "$@"
