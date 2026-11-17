# rOracle backend

FastAPI + SQLAlchemy/Alembic, managed with `uv`. CLI: `python -m roracle {api,worker,migrate}` (plus `eval`, `rebuild`, `reparse-refs`, `perf`; see the [CLI reference](../docs/reference/cli.md)).

Quickstart (dev stack, `.env`, API and worker, a dev Keycloak token, image builds and air-gapped builds):
[local development](../docs/developer/local-dev.md). Tests, the `_test` database guard and CI:
[testing](../docs/developer/testing.md). Module map: [repository layout](../docs/developer/repo-layout.md).
The full developer guide starts at [docs/developer](../docs/developer/README.md).

```sh
scripts/dev-up.sh                # from the repo root
cd backend && cp .env.example .env && set -a && source .env && set +a
uv sync && uv run python -m roracle migrate && uv run python -m roracle api
uv run ruff check . && uv run ruff format --check . && uv run pytest -q -m "not integration and not e2e"
```
