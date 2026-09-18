#!/usr/bin/env bash
# make-proxies.sh — shim: `mtg-proxy make ...` (see README §2). Kept so the dotfiles'
# make-proxies function and old habits keep working. `uv run` syncs the venv on demand.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --quiet --project "$ROOT" mtg-proxy make "$@"
