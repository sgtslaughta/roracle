# Air-gap Mirror Manifest

Everything rOracle needs at build and run time, so mirrors can be populated before installation.
Pin exact versions/digests in your mirror; the versions below are floors. Lock files are the source
of truth for language packages: `backend/uv.lock`, `frontend/pnpm-lock.yaml`.

## Container images

Tags are exact as referenced on 2026-11-15. `sha256:` digests are the manifest-list digest the tag resolved to when this
manifest was written (`docker buildx imagetools inspect <img>`); mutable tags can move, so re-verify and pin in the mirror
(`crane digest` / `skopeo inspect`), then set `image.*.digest` / `*.image.digest` in values (digest wins over tag).
"pin in mirror" = lookup was not possible from the authoring host (registry blocked); never guessed.

### Runtime (Helm chart, `ci/full-values.yaml` render)

| Image | Tag | Used by | Digest |
|---|---|---|---|
| `roracle/backend` | `appVersion` (CI pushes `X.Y.Z` and `vX.Y.Z` on tags) | api, worker, migrate hook, CA-bundle init containers | built by CI: pin the pushed digest |
| `roracle/web` | `appVersion` | web | built by CI: pin the pushed digest |
| `ghcr.io/cloudnative-pg/postgresql` | `16.6` | CNPG `Cluster` | `sha256:53be6d727c7b3cf0df2b147f7ce7cdd31d37fd285314294871a4ccc3837b6872` |
| `docker.elastic.co/elasticsearch/elasticsearch` | `9.5.6` (chart default repo path is wrong, see note) | ECK `Elasticsearch` (+ init containers) | `sha256:79b4f1de4c15e6eca824723086a51e5934406d614d410cc11be77748cafac537` |
| `quay.io/minio/minio` | `RELEASE.2024-10-13T13-34-11Z` | MinIO `Tenant` | pin in mirror (quay.io returned 401) |
| `quay.io/minio/mc` | `RELEASE.2024-10-08T09-37-26Z` | `minio-policy` hook Job | pin in mirror (quay.io returned 401) |
| `curlimages/curl` | `8.10.1` | ES snapshot repo/SLM hook Job | `sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b` |
| `gotenberg/gotenberg` | `8.20.1` | parser | `sha256:cda4386c7ed38f18bc6897828be01ba7361c99929a5c84ec5e293d7916e29bac` |
| `quay.io/docling-project/docling-serve-cpu` | `v1.38.2` | parser (models baked into the image; a CUDA variant needs its own tag) | `sha256:7a480a1604fa3b42efeddc7db9527690d9f9bc903226136013e2bfe6b1a4dfb0` |
| `lfoppiano/grobid` | `0.8.1` | parser (models bundled) | `sha256:820a623a0234cda235d5eb83e924934162c5f0554a843cf665c339da8b51f900` |
| `ghcr.io/huggingface/text-embeddings-inference` | `89-1.9` | reranker on the GPU host (`deploy/game-01`); runs `HF_HUB_OFFLINE=1`, model mounted read-only | pin in mirror (`reranker.sh pull` records the digest) |
| `clamav/clamav` | `1.4` | parser + seed init container | `sha256:4cc4350d3fded380caf1799e76c0d1c93f3fa40af99c0cb2862ec0c9b117718a` |


### Build and CI

| Image | Tag | Used by | Digest |
|---|---|---|---|
| `python` | `3.12-slim` | backend Dockerfile, CI backend jobs, assert-render | `sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` |
| `node` | `22-slim` | frontend Dockerfile build stage, CI frontend job | `sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee` |
| `nginxinc/nginx-unprivileged` | `1.27-alpine` | web runtime stage | `sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0` |
| `postgres` | `16` | CI test service, dev compose | `sha256:dd2bf3ce2dc83a81674cf7f217fcfd8a5009a4b325dda5b843bfee82a06cff6f` |
| `bitnamilegacy/minio` | `2025.5.24` | CI test service, dev compose | `sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c` |
| `gcr.io/kaniko-project/executor` | `v1.23.2-debug` | CI image builds | `sha256:c3109d5926a997b100c4343944e06c6b30a6804b2f9abe0994d3de6ef92b028e` |
| `alpine/helm` | `3.17.3` | CI `helm` job (`HELM_IMAGE`) | `sha256:d899e6316789fec04ee95300a18e454b7942539cbb3d89bde3e0655d6ca2e895` |
| `ghcr.io/yannh/kubeconform` | `v0.6.7-alpine` | CI `kubeconform` job (`KUBECONFORM_IMAGE`) | `sha256:824e0c248809e4b2da2a768b16b107cf17ada88a89ec6aa6050e566ba93ebbc6` |

Dev only (`deploy/dev/compose.yaml`): `quay.io/keycloak/keycloak:26.0` (pin in mirror), docling-serve-cpu `:latest` (use the chart tag instead).
Planned, not referenced by the chart yet: `texlive/texlive` (latex-builder, ~5 GB). Draft PDF builds (P9) use a second, Chromium-enabled instance of the same `gotenberg/gotenberg` image (`ROR_DRAFT_GOTENBERG_URL`); pandoc ships inside the backend image via the hash-pinned `pypandoc-binary` wheel (PyPI mirror).

### Operator and observability charts (floor, pin in mirror)

| Chart | Floor / constraint | Installed by |
|---|---|---|
| `cloudnative-pg` operator | >= 1.26 (barman-cloud plugin method) | platform |
| `plugin-barman-cloud` | version compatible with the CNPG release; pin in mirror | platform |
| `cert-manager` | any supported release (required by the plugin); pin in mirror | platform |
| `eck-operator` | release whose support matrix lists Elasticsearch 9.5.x (ECK 3.x line; verify against the operator's docs before mirroring); pin in mirror | platform |
| `minio-operator` | release supporting the `Tenant` CRD and the MinIO image above; pin in mirror | platform |
| `kube-prometheus-stack`, `loki`, `opentelemetry-collector` | pin in mirror; images follow each chart's defaults, registry set via `MIRROR.example.com` in `deploy/observability/*.values.yaml` | optional |

Each chart's images (operator, plugin sidecar, cert-manager, ECK, MinIO operator, Prometheus, Grafana, Loki, collector-contrib) must be mirrored at the tag the chosen chart version pins.

### Other build-time inputs

| Input | Mirror |
|---|---|
| pnpm (via corepack) | `COREPACK_NPM_REGISTRY`, derived from `NPM_CONFIG_REGISTRY` in the Dockerfile and CI; re-signing mirrors may also need `COREPACK_INTEGRITY_KEYS=0` |
| `uv==0.10.4`, `pyyaml==6.0.3` (CI) | PyPI mirror via `UV_INDEX_URL` |
| kubeconform schemas | set `KUBECONFORM_SCHEMA_LOCATION` to a mirror carrying the Kubernetes schemas and the CRD catalog (CNPG, ECK, MinIO, barman-cloud, Prometheus); empty = online GitHub schemas |
| ClamAV signatures | private freshclam mirror (`parsers.clamav.mirrorUrl` + `mirrorCIDRs`); otherwise only the signatures baked in the image |
| Docling models | baked into the docling-serve image (no cache volume: it would hide them) |

## Python (PyPI mirror via `UV_INDEX_URL`)

Runtime: fastapi, uvicorn[standard], sqlalchemy ≥ 2, psycopg[binary] ≥ 3, alembic, pydantic-settings,
pyjwt[crypto], httpx, boto3, python-multipart; later plans add elasticsearch (9.x client, matching the 9.5 server), openai (client for
the OpenAI-spec endpoint), mcp (official SDK), datasketch (MinHash), docling-core (chunker),
tokenizers/transformers (tokenizer files only), rapidfuzz (reference matching), bleach/nh3
(sanitizing), pyyaml, pypandoc-binary (wheel bundling the pinned pandoc binary for draft builds; hash-locked in
`uv.lock`, so the PyPI mirror is the only source: no apt/pandoc image needed). Dev: pytest, ruff.

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
| Reranker | `BAAI/bge-reranker-v2-m3` @ HF commit `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, served by TEI (below) | `deploy/game-01/reranker.sh pull` writes `SHA256SUMS`. LFS sha256: `model.safetensors` `d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286`, `sentencepiece.bpe.model` `cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865`, `tokenizer.json` `69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15`; plus `config.json`, `special_tokens_map.json`, `tokenizer_config.json` (sum them at pull) |
| Chat | site allowlist | ≥ 32k context recommended |
| Document parsing | granite-docling (VLM) or Docling's bundled layout/OCR/table models | bake model files into the docling-serve image |
| GROBID models | bundled in image | |

## Evaluation datasets (dev reference set, optional)

| Artifact | Mirror | Notes |
|---|---|---|
| SciFact (BEIR format: `corpus.jsonl`, `queries.jsonl`, `qrels/test.tsv`) | download once outside the enclave, unpack to a directory on the host that runs the CLI | imported offline by `roracle eval import-beir <dir> --set scifact --owner <non-admin user> --principals a,b,c` (private by default; the worker then ingests each abstract like an upload, so the embedding and summary endpoints must be up; re-running resumes); the importer contains no network code. License **CC BY-NC 2.0**: non-commercial research and evaluation only; keep it out of production corpora and do not redistribute it with the product |

## Other data

ClamAV signature database (private freshclam mirror), CRL/OCSP endpoints for X.509 if checked,
TeX Live packages (bundled in image).
