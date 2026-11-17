# Testing

What runs where, and how to run it. CI job names are the GitLab jobs in `.gitlab-ci.yml`.

## Backend

Run from `backend/` with `uv run`.

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest -q -m "not integration and not e2e"
```

That pair is what the CI jobs `lint` and `test` run. There is no mypy step.

### Unit and integration split

Markers are declared in `backend/pyproject.toml`:

| Marker | Needs | Runs in CI |
|---|---|---|
| none (unit) | Postgres (a `*_test` database) and an S3 endpoint (MinIO); no Keycloak, Elasticsearch, GPU or LLM | `test` job (Postgres and MinIO services) |
| `integration` | the dev stack: Keycloak, Elasticsearch, Postgres, MinIO | no; run locally |
| `e2e` | real MinIO, Docling, Elasticsearch and a model endpoint; runs only with `ROR_E2E=1` | no |

Unit tests mock the outside world: `httpx.MockTransport`, `tests/ingest_helpers.py`, `tests/es_fakes.py`, `tests/mcp_helpers.py` and the offline fakes in `roracle/eval/fakes.py`. The tests mint their own RSA key and JWTs (`tests/conftest.py`), which is why CI points `ROR_OIDC_ISSUER` at `keycloak.invalid`. Some integration tests skip themselves when their service is missing, for example `tests/test_rerank_live.py` without `ROR_RERANK_URL`.

### The test database guard

The `db_session` fixture runs `TRUNCATE ... RESTART IDENTITY CASCADE` on every table, and the session fixture calls `pytest.exit` unless the database name in `ROR_DATABASE_URL` ends in `_test`. Defaults in `tests/conftest.py` (set only if unset) point at the dev stack: Postgres `roracle_test` on `localhost:55433`, MinIO on `localhost:9000`, the dev Keycloak URLs. Rules:

- Do not `source backend/.env` before running tests: it points `ROR_DATABASE_URL` at the dev database `roracle`, and the guard will abort.
- A separate checkout or branch can use its own database: create it, then export `ROR_DATABASE_URL=postgresql+psycopg://roracle:roracle@localhost:55433/roracle_mybranch_test` on every run. The fixtures migrate it to head.
- Never point `ROR_DATABASE_URL` at a database you care about.

### The integration lock

Integration and e2e tests share one dev stack (Postgres, Elasticsearch, Keycloak, MinIO). When more than one checkout or agent may run them at the same time, serialize them with a lock file:

```sh
flock /tmp/roracle-integ.lock uv run pytest -m integration tests/test_sharing_es.py
```

Unit tests need no lock. The convention is recorded in the plans (for example `docs/superpowers/plans/2026-11-16-retrieval-quality.md`).

### The Elasticsearch service

Integration tests and `roracle eval ci` use the Elasticsearch version the product ships against: `docker.elastic.co/elasticsearch/elasticsearch:9.5.6`, single node, security off. It is the `elasticsearch` service in `deploy/dev/compose.yaml` and the `ELASTICSEARCH_IMAGE` variable for the CI jobs `eval` and `perf`, which start it as a service with a 512 MB or 1 GB heap. Tests read `ROR_ELASTIC_URL` (default `http://localhost:9200`). The Python client is pinned `elasticsearch>=9.0,<10`. An 8.x data directory cannot be opened by 9.x, so use a fresh volume (see [local development](local-dev.md)).

### e2e

`ROR_E2E=1 uv run pytest -m e2e` needs a model endpoint; `tests/e2e_support.py` defaults the LLM to `http://localhost:21434/v1` with `qwen2.5:14b` unless the matching `ROR_*` variables are set.

## Frontend

Run from `frontend/` (Node 22 or newer, `corepack enable`, pnpm 9.15.9).

```sh
corepack pnpm install --frozen-lockfile
corepack pnpm lint                  # tsc --noEmit; no eslint is configured
corepack pnpm exec vitest run       # tests; must print no warnings
corepack pnpm build                 # tsc --noEmit + vite build
corepack pnpm check:dist            # fails on an external font, script or style URL, or mockServiceWorker.js in dist
corepack pnpm check:size            # gzip bundle budget (scripts/check-size.mjs)
corepack pnpm contours              # regenerate public/contour-*.svg (deterministic, fixed seed); not a CI step
```

CI job `frontend` runs these, in this order, only when `frontend/` or the CI file changed on a merge request, and always on other pipelines.

Test setup (`vite.config.ts`, `src/test/setup.ts`): Vitest with jsdom and globals, Testing Library, and an MSW node server from `src/mocks/server.ts`. The server errors on unhandled requests (`onUnhandledFrame: "error"`), so a test that calls an endpoint needs a handler in `src/mocks/`. Mock state is reset after every test. `VITE_API_BASE` is `http://localhost/api` in tests. Browser-only APIs missing from jsdom (canvas, `matchMedia`, observers) are stubbed in the setup file. Tests sit beside the code (`*.test.ts`, `*.test.tsx`); `src/routes/security.test.tsx` and `src/app/shell.a11y.test.tsx` are examples of route-level tests.

## Helm and Terraform

CI job `helm` (stage `lint`, `alpine/helm`):

1. `sh scripts/check-version.sh`;
2. `helm lint --strict` with `ci/min-values.yaml` and with `ci/full-values.yaml`;
3. a render with `config.ROR_DB_POOL_SIZE=40` that must fail with the text "connection budget" (the guard must fire for that reason);
4. render `min.yaml` and `full.yaml` into `rendered/` (with the Grafana dashboard on) as artifacts.

Then two jobs consume the artifacts:

- `assert-render` runs `deploy/helm/roracle/ci/assert-render.sh rendered/full.yaml` (needs only Python 3 and PyYAML). It parses the full render and fails on a broken security invariant: the ClamAV limits and `AlertExceedsMax`, parser pods running with no egress (a mirror-only egress for the ClamAV pod when the mirror is on), a default-deny NetworkPolicy, no empty peers in any ingress or egress rule (which would allow everything), the draft-build pod reachable from the API only, and the draft Gotenberg wiring. Run it locally with `sh deploy/helm/roracle/ci/assert-render.sh`; without a file argument it renders the chart itself and needs `helm`. When you add a NetworkPolicy or parser pod, extend the script.
- `kubeconform` runs `kubeconform -strict -summary` on both renders. With `KUBECONFORM_SCHEMA_LOCATION` empty it uses the upstream schema location and ignores missing schemas (CRDs are skipped); air-gapped runners must set the variable to a mirror that carries the CRD catalog.

CI job `terraform` runs `terraform fmt -check -recursive`, `terraform init -backend=false` and `terraform validate` in `deploy/terraform`. The manual job `iac-kind-e2e` rehearses an apply on kind (see the [kind rehearsal](../enterprise/deployment-kind-rehearsal.md)).

Helm and Terraform jobs run on a merge request only when `deploy/**`, `VERSION`, `scripts/check-version.sh` or the CI file changed.

## Evaluation, performance and docs gates

- Retrieval quality: CI job `eval`, see [eval harness](eval-harness.md).
- Budgets: CI job `perf`, see [perf suite](perf-suite.md).
- Docs: CI job `docs` runs `scripts/docs/check.sh`; see [conventions](conventions.md) and [local development](local-dev.md).
- Images: `build` and `build-web` build with kaniko; merge-request pipelines build without pushing.
