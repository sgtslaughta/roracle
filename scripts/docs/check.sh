#!/usr/bin/env bash
# Doc checks: site build + air-gap guard, generated reference drift, links, code refs, Markdown lint, line cap, script lint + tests.
# Runs from backend/ (ruff first-party detection). Local: `uv run`; CI (CI set): backend venv already on PATH.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
if [ -n "${CI:-}" ]; then run() { (cd backend && "$@"); }; else run() { (cd backend && uv run "$@"); }; fi

echo "== generated reference"; run python "$ROOT/scripts/docs/gen_reference.py" --check
echo "== links"; run python "$ROOT/scripts/docs/check_links.py"
echo "== code refs"; run python "$ROOT/scripts/docs/check_refs.py"
echo "== markdown lint"
# docs/superpowers/plans (historical plan files, kept as written) and docs/stylesheets are not linted.
targets=("$ROOT/deploy")
for d in "$ROOT"/docs/*; do case "$d" in */superpowers|*/stylesheets) ;; *) targets+=("$d");; esac; done
for f in README.md backend/README.md frontend/README.md; do if [ -f "$f" ]; then targets+=("$ROOT/$f"); fi; done
run pymarkdown --config "$ROOT/.pymarkdown.json" scan -r "${targets[@]}"
echo "== line cap (500)"
# No exceptions needed today: the longest existing file (docs/superpowers/plans) is 430 lines.
files=$(find docs deploy -name '*.md' -not -path '*/node_modules/*'; for f in README.md backend/README.md frontend/README.md; do if [ -f "$f" ]; then echo "$f"; fi; done)
over=$(echo "$files" | xargs wc -l | awk '$2 != "total" && $1 > 500 {print $2 " (" $1 " lines)"}')
if [ -n "$over" ]; then echo "over 500 lines:"; echo "$over"; exit 1; fi
echo "== scripts lint + tests"
run ruff check "$ROOT/scripts/docs"; run ruff format --check "$ROOT/scripts/docs"
run python -m unittest discover -s "$ROOT/scripts/docs/tests" -t "$ROOT/scripts/docs"
echo "== site build + air-gap check"; "$ROOT/scripts/docs/build-site.sh"
echo "docs checks OK"
