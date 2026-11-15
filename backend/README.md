# rOracle backend

FastAPI + SQLAlchemy/Alembic, managed with `uv`. CLI: `python -m roracle {api,worker,migrate}`.

## Dev stack

```sh
docker compose -f deploy/dev/compose.yaml up -d    # Postgres :55433, MinIO :9000, Keycloak :8080
cd backend
cp .env.example .env
set -a; source .env; set +a                         # required before alembic / uvicorn
uv sync
uv run python -m roracle migrate                    # or: uv run alembic upgrade head
uv run python -m roracle api                        # http://localhost:8000
uv run python -m roracle worker                     # separate shell, same env
```

Air-gapped: set `REGISTRY_MIRROR` (trailing slash, e.g. `registry.example.internal/`) for compose and
image builds, and `UV_INDEX_URL` for the package mirror.

## Tests

```sh
cd backend
uv run ruff check . && uv run ruff format --check .
uv run pytest -q -m "not integration"
```

Tests read `ROR_*` env and default to the dev stack (Postgres DB `roracle_test`, MinIO). Tests marked
`integration` need the Keycloak dev stack and skip without it; CI runs `-m "not integration"`.

## Get a token (dev Keycloak)

```sh
TOKEN=$(curl -s http://localhost:8080/realms/roracle/protocol/openid-connect/token \
  -d grant_type=password -d client_id=roracle-web -d username=researcher -d password=researcher \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -H "Authorization: Bearer $TOKEN" -F file=@paper.pdf http://localhost:8000/documents
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me/uploads
```

## Image / CI

`docker build -f backend/Dockerfile --build-arg REGISTRY_MIRROR=... --build-arg UV_INDEX_URL=... .`
(context is the repo root). Default command runs the API; override with `worker` or `migrate`.
`.gitlab-ci.yml` runs lint, test (Postgres + MinIO services; override `MINIO_IMAGE` if needed) and a
kaniko build; images are pushed on `main` and tags only, merge requests build without pushing.

## Air-gapped builds

Set the `UV_INDEX_URL` and `REGISTRY_MIRROR` CI variables. Dependencies are installed hash-pinned
(`uv export` from `uv.lock`, `--require-hashes`) from the mirror, so nothing is fetched from pypi.org. The index URL is a
build ARG only (not baked into image env), but ARG values can appear in build logs/metadata; avoid
credentials in it where possible.
