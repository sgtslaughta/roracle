# Repository layout

rOracle is a monorepo: one backend image with three entrypoints, one web image, a Helm chart and Terraform. Components and data flows are in the [architecture overview](../architecture/overview.md); terms are in the [glossary](../glossary.md).

```text
backend/    FastAPI + SQLAlchemy/Alembic backend (uv, pytest, ruff)
frontend/   React web app (pnpm, Vitest)
deploy/     dev compose stack, Helm chart, Terraform, observability values, game-01 inference box
docs/       this documentation, ADRs, perf and eval reports, plans
scripts/    dev-up.sh, check-version.sh, docs checks and generators
VERSION     the single product version
```

## Backend

`backend/src/roracle` is one Python package. `python -m roracle {api,worker,migrate,eval,rebuild,reparse-refs,perf}` selects the entrypoint (the full list is in the [CLI reference](../reference/cli.md)). The container runs `python -m roracle api` by default.

| Path | Holds |
|---|---|
| `api/app.py`, `api/routes/` | the FastAPI app and one module per route group (documents, library, search, chats, drafts, handoffs, tokens, admin and so on) |
| `api/input_guard.py` | request input rejection (see [errors](../reference/errors.md)) |
| `mcp/` | the MCP server mounted in the API process: auth, read tools, write tools, writing tools |
| `worker.py`, `worker_health.py`, `queue.py` | the job worker, its liveness endpoint, and the Postgres job queue (`FOR UPDATE SKIP LOCKED`, leases); see [jobs and queue](../architecture/jobs-queue.md) |
| `ingest/` | the ingest pipeline: intake, scan, office conversion, parse, chunking, embedding, near-duplicates, summaries, RAPTOR, contextual notes, rebuild and reparse CLIs |
| `connectors/` | S3, folder and website sources and the network guard |
| `chat/` | retrieval for chat, the router, answer generation, global answers, agentic retrieval |
| `retrieval.py`, `retrieval_util.py`, `search_*.py` | hybrid retrieval and the Elasticsearch index, ACL filter and cache |
| `access.py`, `acl_sync.py`, `labels/` | visibility rule, ACL convergence to Elasticsearch, sensitivity labels |
| `notes/`, `handoff.py`, `citations.py`, `identifiers.py`, `bibtex.py` | notes, annotations, proposals, evidence, handoffs, references |
| `writing/` | drafts, profiles (YAML), checks, citation check, LaTeX build, agent skill guide |
| `eval/` | the retrieval-quality harness (see [eval harness](eval-harness.md)) |
| `perf/` | synthetic data and load tooling (see [perf suite](perf-suite.md)) |
| `config.py`, `runtime.py` | `ROR_*` settings, and the admin-changeable overrides stored in `app_settings` |
| `models*.py` | SQLAlchemy models, split by area to respect the 500-line limit |
| `auth.py`, `pats.py`, `ratelimit.py`, `audit.py`, `metrics.py` | identity, tokens, rate limit, audit trail, Prometheus metrics |
| `reembed*.py`, `maintenance.py` | embedding-model switch, retention purge, blob GC, tag reconcile |

Other backend paths:

- `backend/migrations/versions/` holds the Alembic migrations (`python -m roracle migrate` runs `alembic upgrade head`).
- `backend/tests/` is flat: `test_*.py` plus shared helpers (`conftest.py`, `*_helpers.py`, `es_fakes.py`, `e2e_support.py`). See [testing](testing.md).
- `backend/Dockerfile` builds from the repo root context. It installs hash-pinned dependencies from the package mirror.

## Frontend

`frontend/src` (Vite, React, TypeScript strict, Tailwind v4, shadcn/ui vendored in `components/ui`, TanStack Query and Router, pdf.js).

| Path | Holds |
|---|---|
| `app/` | shell, router, top bar, sidebar, global search |
| `routes/` | route components (library, document, ask, uploads, settings, admin, handoff) |
| `features/` | one folder per area: `admin`, `agents`, `ask`, `drafts`, `labels`, `library`, `notes`, `notifications`, `palette`, `proposals`, `reader`, `uploads` |
| `api/` | typed client and shared types |
| `auth/` | OIDC sign-in (`oidc-client-ts`) |
| `components/` | shared components; `components/ui` is vendored shadcn/ui |
| `mocks/` | MSW handlers, sample data and generated sample PDFs; used by tests and by mock mode |
| `styles/` | design tokens (`tokens.css`) and per-area CSS |
| `lib/`, `test/` | helpers; `test/setup.ts` is the Vitest setup |

Other frontend paths: `frontend/nginx/` (web server config and the `/api` proxy), `frontend/scripts/` (`check-dist.mjs`, `check-size.mjs`, `gen-contours.mjs`), `frontend/Dockerfile`.

## Deploy

| Path | Holds |
|---|---|
| `deploy/dev/` | `compose.yaml`, the dev Keycloak realm, the Postgres init script; see [local development](local-dev.md) |
| `deploy/helm/roracle/` | the Helm chart; `ci/` has `min-values.yaml`, `full-values.yaml`, `assert-render.sh` |
| `deploy/terraform/` | Terraform wrapping the chart (modules, `environments/`, `test/` rehearsal scripts) |
| `deploy/observability/` | values for the Prometheus stack, Loki and the OpenTelemetry collector |
| `deploy/game-01/` | the GPU inference box: reranker script and the Ollama context-model recipe |

Installing is covered by the [deployment guide](../enterprise/deployment-guide.md) and [Terraform guide](../enterprise/deployment-terraform.md).

## Scripts and docs tooling

- `scripts/dev-up.sh` starts the dev stack and migrates the dev and test databases.
- `scripts/check-version.sh` enforces the single version (see [conventions](conventions.md)).
- `scripts/docs/` holds the docs CI. `check.sh` runs every docs check, `build-site.sh` builds the Zensical site into `site/` (gitignored), `gen_reference.py` regenerates `docs/reference/{openapi.json,settings.md,cli.md,mcp-tools.md,helm-values.md}`, and `screenshots/` re-captures the user and admin guide images (see its `README.md`).
- Zensical settings are in `zensical.toml`. The site nav is generated from the files under `docs/`; `docs/superpowers/` and `docs/prototype/` are excluded from the site.
