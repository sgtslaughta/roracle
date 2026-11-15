# Air-gap Mirror Manifest

Everything rOracle needs at build and run time, so mirrors can be populated before installation.
Pin exact versions/digests in your mirror; the versions below are floors. Lock files are the source
of truth for language packages: `backend/uv.lock`, `frontend/pnpm-lock.yaml`.

## Container images

| Image | Used by | Notes |
|---|---|---|
| `python:3.12-slim` | backend build/runtime | base for `roracle/backend` |
| `node:22-slim` | frontend build | build stage only |
| `nginx:1.27-alpine` (or unprivileged variant) | `roracle/web` runtime | |
| `postgres:16` | dev/CI | prod uses CloudNativePG images |
| `ghcr.io/cloudnative-pg/postgresql:16` + operator | prod Postgres | |
| `docker.elastic.co/elasticsearch/elasticsearch:8.x` + ECK operator | search | ≥ 8.15 for `int8_hnsw`, `semantic` features not required |
| `minio/minio`, `minio/mc`, MinIO operator | blobs | or external S3 |
| `quay.io/keycloak/keycloak:26.x` | dev only | prod uses your Keycloak |
| `quay.io/docling-project/docling-serve` | parsing | CPU or CUDA variant |
| `lfoppiano/grobid:0.8.x` | metadata/references | |
| `gotenberg/gotenberg:8.20.1` | office → PDF (ingest rendition), HTML → PDF | |
| `clamav/clamav:1.4` | malware scan | needs signature mirror (freshclam private mirror) |
| `texlive/texlive` (full) | latex-builder | ~5 GB |
| `pandoc/core` | Markdown drafts | |
| `gcr.io/kaniko-project/executor` | CI image builds | |
| `ghcr.io/astral-sh/uv` (or pip-installed uv) | CI | |

## Python (PyPI mirror via `UV_INDEX_URL`)

Runtime: fastapi, uvicorn[standard], sqlalchemy ≥ 2, psycopg[binary] ≥ 3, alembic, pydantic-settings,
pyjwt[crypto], httpx, boto3, python-multipart; later plans add elasticsearch ≥ 8, openai (client for
the OpenAI-spec endpoint), mcp (official SDK), datasketch (MinHash), docling-core (chunker),
tokenizers/transformers (tokenizer files only), rapidfuzz (reference matching), pypdf (page count), bleach/nh3
(sanitizing). Dev: pytest, ruff.

## npm (registry mirror via `NPM_CONFIG_REGISTRY`)

react, react-dom, vite, typescript, @tanstack/react-query, @tanstack/react-router, tailwindcss,
shadcn/ui component deps (@radix-ui/*, class-variance-authority, clsx, tailwind-merge),
lucide-react, pdfjs-dist, katex, react-markdown + rehype/remark plugins, vitest, @playwright/test
(+ Playwright browser bundle mirrored separately), @fontsource/newsreader, @fontsource/manrope,
@fontsource/stix-two-text.

## Models (on the inference endpoint or as files)

| Purpose | Model | Notes |
|---|---|---|
| Embeddings | EmbeddingGemma-300m (768-d) or e5-mistral-7b-instruct (4096-d) | admin config (ADR-0006) |
| Tokenizer files | matching the embedding model | for chunk sizing in workers (no network) |
| Reranker | site reranker (cross-encoder) | |
| Chat | site allowlist | ≥ 32k context recommended |
| Document parsing | granite-docling (VLM) or Docling's bundled layout/OCR/table models | bake model files into the docling-serve image |
| GROBID models | bundled in image | |

## Other data

ClamAV signature database (private freshclam mirror), CRL/OCSP endpoints for X.509 if checked,
TeX Live packages (bundled in image).
