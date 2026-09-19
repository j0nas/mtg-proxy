#!/usr/bin/env bash
# mtg-proxy.sh — shim: any `mtg-proxy <command> ...` (redo, backlog, notes, doctor, ...) from
# any directory. The dotfiles' mtg-proxy function calls this. `uv run` syncs the venv on demand.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --quiet --project "$ROOT" mtg-proxy "$@"
