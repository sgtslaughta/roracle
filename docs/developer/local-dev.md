# Local development

How to run rOracle on a workstation. The dev stack is Docker Compose; the backend and web app run from source. Layout is in [repository layout](repo-layout.md).

Prerequisites: Docker with Compose, uv for Python (3.12 or newer), Node 22 or newer with `corepack` (pnpm 9).

## 1. Start the dev stack

```sh
scripts/dev-up.sh
```

The script runs `docker compose -f deploy/dev/compose.yaml up -d`, waits until Postgres, MinIO, Elasticsearch, Keycloak and Docling answer, then sources `backend/.env.example` and runs `alembic upgrade head` on the `roracle` and `roracle_test` databases.

| Service | Image | Host port |
|---|---|---|
| Postgres | `postgres:16` | 55433 (`ROR_PG_PORT` overrides) |
| MinIO (bucket `roracle` made by `minio-init`) | `bitnamilegacy/minio:2025.5.24` | 9000, console 9001 |
| Keycloak (realm import, see below) | `keycloak:26.0` | 8080 |
| Elasticsearch, single node, security off, 1 GB heap | `elasticsearch:9.5.6` | 9200 |
| Docling | `docling-serve-cpu:latest` | 5001 |

Profile `full` adds GROBID (8070), two Gotenberg instances (3300 for office conversion, 3301 for draft PDFs, both bound to 127.0.0.1) and ClamAV (3310). `scripts/dev-up.sh` does not start them. Start them with `docker compose -f deploy/dev/compose.yaml --profile full up -d grobid clamav gotenberg gotenberg-drafts`, then set the matching `ROR_GROBID_URL`, `ROR_CLAMAV_HOST`, `ROR_GOTENBERG_URL` and `ROR_DRAFT_GOTENBERG_URL` values in `backend/.env` (commented examples are in `backend/.env.example`; every setting is in the [settings reference](../reference/settings.md)). An empty URL skips that stage.

Notes:

- `deploy/dev/postgres/init.sql` creates `roracle_test` and runs only on a fresh volume. Use `docker compose -f deploy/dev/compose.yaml down -v` to recreate.
- Elasticsearch uses a new volume (`esdata9`) because a 8.x data directory cannot be opened by 9.x. Reindex or rebuild instead (see [search index lifecycle](../architecture/search-index-lifecycle.md)).
- Air-gapped: set `REGISTRY_MIRROR` (with a trailing slash, for example `registry.example.internal/`) for compose and image builds, and `UV_INDEX_URL` for the Python package mirror. Dependencies in the image are installed hash-pinned from that mirror.

### Troubleshooting: GROBID crashes with a NullPointerException

On newer kernels the JDK's cgroup v2 detection throws a `NullPointerException` (in `CgroupInfo.getMountPoint`) at start. The dev compose file sets `JAVA_TOOL_OPTIONS=-XX:-UseContainerSupport` on the `grobid` service to skip container probing. If you run GROBID some other way, set the same variable.

## 2. Backend

```sh
cd backend
cp .env.example .env
set -a; source .env; set +a      # required before alembic or the API
uv sync                          # includes the dev dependency group
uv run python -m roracle migrate # or: uv run alembic upgrade head
uv run python -m roracle api     # http://localhost:8000
uv run python -m roracle worker  # second shell, same environment
```

`backend/.env.example` points at the dev stack. It expects an OpenAI-compatible endpoint for the LLM and embeddings on `localhost:21434` (an SSH tunnel to the inference box, or a local Ollama on 11434), `embeddinggemma` with 768 dimensions and `qwen2.5:14b`. Unit tests need none of it. The reranker, contextual notes and agentic retrieval are off while their settings are empty or `false`.

Run the API without `source .env` and the tests will refuse to start, by design: see [testing](testing.md).

### Get a token

The dev realm accepts the password grant for the public client `roracle-web`:

```sh
TOKEN=$(curl -s http://localhost:8080/realms/roracle/protocol/openid-connect/token \
  -d grant_type=password -d client_id=roracle-web -d username=researcher -d password=researcher \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -H "Authorization: Bearer $TOKEN" -F file=@paper.pdf http://localhost:8000/documents
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me/uploads
```

Calling the backend directly on port 8000 has no `/api` prefix. Clients behind the web proxy or ingress use `/api/documents` and so on; the proxy strips the prefix. The [HTTP API guide](../reference/api.md) uses the client-visible form.

## 3. Web app

```sh
cd frontend
corepack pnpm install
VITE_MOCK=1 corepack pnpm dev   # mock API (MSW): sample documents, no backend, no login
corepack pnpm dev               # real API
```

Real mode proxies `/api` to `http://localhost:8000` with the prefix stripped (`vite.config.ts`) and signs in through Keycloak. Its settings are in `frontend/.env.example`: `VITE_API_BASE` (`/api`), `VITE_OIDC_AUTHORITY` (`http://localhost:8080/realms/roracle`), `VITE_OIDC_CLIENT_ID` (`roracle-web`) and `VITE_MOCK`. The mock worker and the logo source are removed from production builds unless `VITE_MOCK=1`.

## The Keycloak dev realm

`deploy/dev/keycloak/realm-roracle.json` is imported at start (`start-dev --import-realm`; Keycloak admin console login `admin` / `admin`). It is for development only: TLS is not required (`sslRequired: none`).

| Item | Value |
|---|---|
| Realm | `roracle` |
| Client | `roracle-web`: public, standard flow plus password grant, PKCE `S256`, redirects `http://localhost:*` and `http://127.0.0.1:*` |
| Token mappers | audience `roracle`; the `groups` claim with full paths |
| User | `researcher` / `researcher`, member of `/Mathematics/Spectral Theory` |
| Realm role | `roracle-admin` (nobody has it by default) |

To try the admin screens, give `researcher` the `roracle-admin` realm role in the Keycloak console and sign in again. `backend/.env.example` also allows a client `roracle-mcp` in `ROR_OIDC_ALLOWED_CLIENTS`, but the dev realm does not define it. For agents in development use a personal access token ([agents and tokens](../user/agents-and-tokens.md)). Production Keycloak is covered by [Keycloak setup](../enterprise/keycloak-setup.md).

## The inference box (`deploy/game-01`)

Production-like inference runs on a GPU host called game-01. The repository carries what is needed to reproduce its parts; it is not part of the dev stack.

- `deploy/game-01/reranker.sh` installs and runs a Hugging Face TEI reranker with `bge-reranker-v2-m3`, bound to `127.0.0.1:28080` only. Subcommands: `pull` (connected host: pinned model revision, `SHA256SUMS`, image tar), `install` (copy to the host and verify), `run`, `health`. The container runs with `HF_HUB_OFFLINE=1`, so nothing downloads at runtime.
- Dev access is an SSH tunnel (`ssh -N -L 21480:127.0.0.1:28080 game-01`), then `ROR_RERANK_URL=http://localhost:21480/rerank`, `ROR_RERANK_API=tei`, `ROR_RERANK_MODEL=bge-reranker-v2-m3`. On a timeout, HTTP error or malformed reply the fused order is kept and `roracle_rerank_fallback_total` increments.
- The same README gives the Ollama recipe for the contextual-notes model (`num_ctx` of at least 16384). `ROR_CONTEXT_NOTES` stays `false` until an [eval report](eval-harness.md) says PASS.

The live reranker test (`tests/test_rerank_live.py`) skips unless `ROR_RERANK_URL` answers `/health`.

## Building images

```sh
docker build -f backend/Dockerfile --build-arg REGISTRY_MIRROR=... --build-arg UV_INDEX_URL=... .
docker build -f frontend/Dockerfile --build-arg REGISTRY_MIRROR=... --build-arg NPM_CONFIG_REGISTRY=... --build-arg UV_INDEX_URL=... .
```

The build context is the repository root. The backend image's default command runs the API; override it with `worker` or `migrate` (`python -m roracle worker`). CI builds both images with kaniko (`build`, `build-web`): merge requests build without pushing, other pipelines push. In an air-gapped build set the `UV_INDEX_URL`, `NPM_CONFIG_REGISTRY` and `REGISTRY_MIRROR` CI variables. Python dependencies are installed hash-pinned (`uv export` from `uv.lock`, then `--require-hashes`) from the mirror, so nothing is fetched from pypi.org. The index URL is a build argument only and is not baked into the image environment, but argument values can appear in build logs and metadata: avoid credentials in it where possible.

## Docs and screenshots

`scripts/docs/check.sh` runs every docs check (generated-reference drift, links, code references, Markdown lint, the 500-line cap, the site build and its air-gap guard); the CI job `docs` runs the same script. `scripts/docs/build-site.sh` builds the site alone. To re-capture the user and admin guide screenshots, follow `scripts/docs/screenshots/README.md`: it drives the web app in mock mode, so it needs no backend.
