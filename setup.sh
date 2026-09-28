#!/usr/bin/env bash
# setup.sh — make a fresh clone runnable. Idempotent; rerun any time.
#
#   1. uv (https://docs.astral.sh/uv/)                — must already be on PATH
#   2. silhouette-card-maker (PDF engine)             → ./silhouette-card-maker
#      fork j0nas/silhouette-card-maker, branch local-patches (batch + parallel
#      Scryfall fetching, --token_copies, MTGA parser fixes, exact page boxes)
#   3. the project venv (.venv) with the engine's pinned deps + mtg-proxy itself
#      (cut-proxies' Cameo driver included; USB cutting on a Mac also needs brew's libusb)
#   4. git hooks (ruff + pytest before every commit)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ENGINE_REPO="https://github.com/j0nas/silhouette-card-maker.git"
ENGINE_BRANCH="local-patches"
for a in "$@"; do
  case "$a" in
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 1 ;;
  esac
done

command -v uv >/dev/null 2>&1 || { echo "error: uv not found — install it first: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "error: git not found" >&2; exit 1; }

echo "== engine: silhouette-card-maker ($ENGINE_BRANCH)"
if [[ ! -d silhouette-card-maker/.git ]]; then
  git clone --branch "$ENGINE_BRANCH" "$ENGINE_REPO" silhouette-card-maker
else
  echo "   present ($(git -C silhouette-card-maker rev-parse --abbrev-ref HEAD) @ $(git -C silhouette-card-maker rev-parse --short HEAD))"
fi
grep -q "token_copies" silhouette-card-maker/plugins/mtg/fetch.py || {
  echo "error: engine clone lacks the local patches — checkout branch $ENGINE_BRANCH of $ENGINE_REPO" >&2; exit 1; }
mkdir -p silhouette-card-maker/game/front silhouette-card-maker/game/back \
         silhouette-card-maker/game/double_sided silhouette-card-maker/game/output

echo "== project venv (.venv)"
uv sync --quiet
echo "   $(uv run --quiet mtg-proxy --version)"

echo "== git hooks"
git config core.hooksPath .githooks
echo "   core.hooksPath = .githooks (ruff + pytest on commit)"

echo "== doctor"
uv run --quiet mtg-proxy doctor || true
echo
echo "Ready. Try:  ./make-proxies.sh --test"
