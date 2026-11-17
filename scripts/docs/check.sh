#!/usr/bin/env bash
# Doc checks: generated reference drift, links, code refs, Markdown lint, line cap, script lint + tests.
# Runs from backend/ (ruff first-party detection). Local: `uv run`; CI (CI set): backend venv already on PATH.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
if [ -n "${CI:-}" ]; then run() { (cd backend && "$@"); }; else run() { (cd backend && uv run "$@"); }; fi

echo "== generated reference"; run python "$ROOT/scripts/docs/gen_reference.py" --check
echo "== links"; run python "$ROOT/scripts/docs/check_links.py"
echo "== code refs"; run python "$ROOT/scripts/docs/check_refs.py"
echo "== markdown lint"
targets=("$ROOT/docs" "$ROOT/deploy")  # root + component READMEs are added once they exist
for f in README.md backend/README.md frontend/README.md; do [ -f "$f" ] && targets+=("$ROOT/$f"); done
run pymarkdown --config "$ROOT/.pymarkdown.json" scan "${targets[@]}"
echo "== line cap (500)"
# No exceptions needed today: the longest existing file (docs/superpowers/plans) is 430 lines.
files=$(find docs deploy -name '*.md' -not -path '*/node_modules/*'; for f in README.md backend/README.md frontend/README.md; do [ -f "$f" ] && echo "$f"; done)
over=$(echo "$files" | xargs wc -l | awk '$2 != "total" && $1 > 500 {print $2 " (" $1 " lines)"}')
if [ -n "$over" ]; then echo "over 500 lines:"; echo "$over"; exit 1; fi
echo "== scripts lint + tests"
run ruff check "$ROOT/scripts/docs"; run ruff format --check "$ROOT/scripts/docs"
run python -m unittest discover -s "$ROOT/scripts/docs/tests" -t "$ROOT/scripts/docs"
echo "docs checks OK"
