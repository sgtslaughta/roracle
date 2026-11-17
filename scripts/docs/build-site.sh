#!/usr/bin/env bash
# Build the Zensical docs site into ./site and fail on any external resource reference (air-gap guard).
# Local: `uv run`; CI (CI set) / Docker: zensical already on PATH. Content links (<a href="https://...">) are allowed.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
if [ -n "${CI:-}" ] || [ -n "${ROR_DOCS_NO_UV:-}" ]; then run() { "$@"; }; else run() { (cd backend && uv run "$@"); }; fi
rm -rf "$ROOT/site"
run zensical build --clean -f "$ROOT/zensical.toml"
run python "$ROOT/scripts/docs/check_site.py" "$ROOT/site"
