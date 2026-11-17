# Air-gap Mirror Manifest

Everything rOracle needs at build and run time, so mirrors can be populated before installation. Back to the [enterprise index](README.md).
Pin exact versions and digests in your mirror; the versions below are floors. Lock files are the source
of truth for language packages: `backend/uv.lock`, `frontend/pnpm-lock.yaml`. Deployment steps are in the
[deployment guide](deployment-guide.md) and [Terraform guide](deployment-terraform.md); Helm keys are in the
[Helm values reference](../reference/helm-values.md).

**CI guard.** `scripts/docs/check_refs.py` fails when an `image:`, `image =` or `repository:` value in `deploy/**`
names a repository that is not written in backticks on this page. Add a row here whenever you add an image.

## Container images

Tags are exact as referenced in the repo. The Digest column says what the repo pins: values files and compose files use
mutable tags, so the answer is `not pinned in repo` for every row except the single `registry:2.8.3@sha256:...` pin of the
kind rehearsal (see below). Mutable tags can move: resolve the digest in your mirror (`crane digest` / `skopeo inspect`),
then set `image.*.digest` / `*.image.digest` in values (digest wins over tag). Digests that were looked up on 2026-11-15
are kept for reference at the end of this section; they are not part of the repo.

### Runtime (Helm chart, `ci/full-values.yaml` render)

| Image | Tag | Used by | Digest |
|---|---|---|---|
| `roracle/backend` | `appVersion` (CI pushes `X.Y.Z` and `vX.Y.Z` on tags) | api, worker, migrate hook, CA-bundle init containers, Ceph bucket-setup Job | not pinned in repo (built by CI: pin the pushed digest; `ci/full-values.yaml` carries an all-zero placeholder) |
| `roracle/web` | `appVersion` | web | not pinned in repo (built by CI: pin the pushed digest) |
| `ghcr.io/cloudnative-pg/postgresql` | `16.6` | CNPG `Cluster` | not pinned in repo |
| `docker.elastic.co/elasticsearch/elasticsearch` | `9.5.6` (`elasticsearch.version`; external ES must be 9.x: the 9.x client sends `compatible-with=9`) | ECK `Elasticsearch` (+ init containers) | not pinned in repo |
| `curlimages/curl` | `8.10.1` | ES snapshot repo/SLM hook Job | not pinned in repo |
| `gotenberg/gotenberg` | `8.20.1` | parser | not pinned in repo |
| `quay.io/docling-project/docling-serve-cpu` | `v1.38.2` | parser (models baked into the image; a CUDA variant needs its own tag) | not pinned in repo |
| `lfoppiano/grobid` | `0.8.1` | parser (models bundled) | not pinned in repo |
| `ghcr.io/huggingface/text-embeddings-inference` | `89-1.9` | reranker on the GPU host (`deploy/game-01`); runs `HF_HUB_OFFLINE=1`, model mounted read-only | not pinned in repo (`reranker.sh pull` writes `image.digest` into the bundle) |
| `clamav/clamav` | `1.4` | parser + seed init container | not pinned in repo |

**MinIO (removed).** MinIO stopped publishing community container images in Oct 2025 (the community edition is source-only; `quay.io/minio/minio` and `quay.io/minio/mc` answer 401). The chart and Terraform no longer deploy MinIO: object storage is Rook-Ceph (`object_store = "rook-ceph"`, images below) or an S3 you operate. `bitnamilegacy/minio` (dev compose, CI, the kind rehearsal stand-in) is dev/CI only and never shipped.

### Rook-Ceph object storage (`object_store = "rook-ceph"`)

| Image / chart | Tag | Mirror path (default) | Used by |
|---|---|---|---|
| `docker.io/rook/ceph` | `v1.18.0` | `<mirror_registry>/rook/ceph` (`operator_images.rook_ceph`) | rook-ceph operator |
| `quay.io/ceph/ceph` | `v19.2.3` | `<mirror_registry>/ceph/ceph` (`ceph.image`) | mon, mgr, osd, rgw |
| chart `rook-ceph`, `rook-ceph-cluster` | `v1.18.0` | `oci://<mirror>/charts` (`charts.rook_ceph`, `charts.rook_ceph_cluster`) | Terraform `helm_release` |

CSI (including the ceph-csi-operator subchart: `csi.rookUseCsiOperator=false`), toolbox and monitoring are disabled by the Terraform values, so cephcsi, csi-operator and sidecar images are not needed. cert-manager (already mirrored) issues the RGW certificate. Confirm the tags against the mirrored charts (`helm show values`) before pinning.


### Build and CI

| Image | Tag | Used by | Digest |
|---|---|---|---|
| `python` | `3.12-slim` | backend Dockerfile, CI backend jobs, assert-render | not pinned in repo |
| `node` | `22-slim` | frontend Dockerfile build stage, CI frontend job | not pinned in repo |
| `nginxinc/nginx-unprivileged` | `1.27-alpine` | web runtime stage | not pinned in repo |
| `postgres` | `16` | CI test service, dev compose | not pinned in repo |
| `bitnamilegacy/minio` | `2025.5.24` | CI test service, dev compose | not pinned in repo |
| `gcr.io/kaniko-project/executor` | `v1.23.2-debug` | CI image builds | not pinned in repo |
| `alpine/helm` | `3.17.3` | CI `helm` job (`HELM_IMAGE`) | not pinned in repo |
| `ghcr.io/yannh/kubeconform` | `v0.6.7-alpine` | CI `kubeconform` job (`KUBECONFORM_IMAGE`) | not pinned in repo |
| `hashicorp/terraform` | `1.15.8` | CI `terraform` job (`TERRAFORM_IMAGE`) and the operator workstation | not pinned in repo |
| `docker` (service `docker:27-dind`) | `27-dind` | CI `iac-kind-e2e` job (manual) | not pinned in repo |

Dev only (`deploy/dev/compose.yaml`, all through `${REGISTRY_MIRROR}`): `quay.io/keycloak/keycloak` `26.0`, `postgres` `16`, `bitnamilegacy/minio` `2025.5.24`, `docker.elastic.co/elasticsearch/elasticsearch` `9.5.6`, `lfoppiano/grobid` `0.8.1`, `gotenberg/gotenberg` `8.20.1`, `clamav/clamav` `1.4`, and `quay.io/docling-project/docling-serve-cpu` `latest` (use the chart tag `v1.38.2` instead). Digest: not pinned in repo.

**`bitnamilegacy/minio` is dev and CI only.** It is the S3 stand-in of the dev compose file, the CI test services and the kind rehearsal (`deploy/terraform/test/s3-stub.yaml`). No production path deploys it.
Planned, not referenced by the chart yet: `texlive/texlive` (latex-builder, ~5 GB). Draft PDF builds (P9) use a second, Chromium-enabled instance of the same `gotenberg/gotenberg` image (`ROR_DRAFT_GOTENBERG_URL`); pandoc ships inside the backend image via the hash-pinned `pypandoc-binary` wheel (PyPI mirror).

### Operator images (set through `operator_images` in Terraform)

Tags are the ones the example tfvars and `deploy/terraform/test/kind-e2e.sh` pin; the Terraform default path is `<mirror_registry>/<upstream path>` (`locals.tf`, tag a gated placeholder). Digest: not pinned in repo for every row.

| Image | Tag in tfvars | Notes |
|---|---|---|
| `ghcr.io/cloudnative-pg/cloudnative-pg` | `1.26.0` | CNPG operator |
| `ghcr.io/cloudnative-pg/plugin-barman-cloud` | `v0.16.0` | barman-cloud plugin (chart 0.9.0) |
| `ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar` | `v0.16.0` | plugin sidecar |
| `quay.io/jetstack/cert-manager-controller`, `quay.io/jetstack/cert-manager-webhook`, `quay.io/jetstack/cert-manager-cainjector`, `quay.io/jetstack/cert-manager-startupapicheck` | `v1.17.0` | cert-manager |
| `docker.elastic.co/eck/eck-operator` | `2.16.0` | **Wrong for Elasticsearch 9.5.6.** The tfvars pin ECK 2.16.0, but the chart default Elasticsearch 9.5.6 needs an ECK 3.x release whose support matrix lists 9.5. Raise `charts.eck` and `operator_images.eck` before mirroring ([upgrades](upgrades.md)) |
| `docker.io/rook/ceph` | `v1.18.0` | only for `object_store = "rook-ceph"`; see the Rook-Ceph table above |

### Observability, inference and rehearsal images

| Image | Tag | Used by | Digest |
|---|---|---|---|
| `otel/opentelemetry-collector-contrib` | chart default (set `image.repository` to `<mirror>/otel/opentelemetry-collector-contrib` in `deploy/observability/otel-collector.values.yaml`) | optional OpenTelemetry collector | not pinned in repo |
| `vllm/vllm-openai` | `0.0.0-PLACEHOLDER` (gated: apply fails until you set it) | optional Terraform `inference` module: LLM and reranker (`rerank.engine = "vllm"`) | not pinned in repo |
| `registry` | `2.8.3` | kind rehearsal: local mirror registry (`deploy/terraform/test/kind-e2e.sh`) | `sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373` (the only digest the repo pins) |
| `docker.elastic.co/elasticsearch/elasticsearch` | `8.15.3` | kind rehearsal only (upgrade path) | not pinned in repo |

Grafana, Prometheus, Loki and their sidecars come with the `kube-prometheus-stack` and `loki` charts (see below); their images are the chart defaults, re-registried by `global.imageRegistry` / `global.image.registry` (`MIRROR.example.com` placeholder in `deploy/observability/*.values.yaml`).

### Operator and observability charts (floor, pin in mirror)

| Chart | Floor / constraint | Installed by |
|---|---|---|
| `cloudnative-pg` operator | >= 1.26 (barman-cloud plugin method) | platform |
| `plugin-barman-cloud` | version compatible with the CNPG release; pin in mirror | platform |
| `cert-manager` | any supported release (required by the plugin); pin in mirror | platform |
| `eck-operator` | release whose support matrix lists Elasticsearch 9.5.x (ECK 3.x line; verify against the operator's docs before mirroring); **not 2.16.0, which the example tfvars pin**; pin in mirror | platform |
| `rook-ceph`, `rook-ceph-cluster` | v1.18.0 (Ceph v19.2.3); only for `object_store = "rook-ceph"`; pin in mirror | platform |
| `kube-prometheus-stack`, `loki`, `opentelemetry-collector` | the example prod tfvars use 70.0.0, 6.30.0 and 0.120.0; pin in mirror; images follow each chart's defaults, registry set via `MIRROR.example.com` in `deploy/observability/*.values.yaml` | optional |

Each chart's images (operator, plugin sidecar, cert-manager, ECK, Prometheus, Grafana, Loki, collector-contrib) must be mirrored at the tag the chosen chart version pins.

### Terraform providers (`deploy/terraform`, mirror via `provider_installation` in the CLI config / `TF_CLI_CONFIG_FILE`)

| Provider | Constraint | Lock file |
|---|---|---|
| `registry.terraform.io/hashicorp/kubernetes` | `~> 3.0` (locked 3.5.0) | `deploy/terraform/.terraform.lock.hcl` |
| `registry.terraform.io/hashicorp/helm` | `~> 3.0` (locked 3.3.0) | same |

Populate with `terraform providers mirror <dir>` on a connected host. The Terraform stack also consumes the operator charts above and,
when the optional inference module is enabled, a vLLM-style OpenAI-spec image (also used for the reranker with `--task score`; optionally `ghcr.io/huggingface/text-embeddings-inference` for engine `tei`). Operator images are set through `operator_images`.

### Operations tooling (runbook)

| Image | Use |
|---|---|
| `amazon/aws-cli` (pin tag/digest in mirror) | S3-native checks and repairs in the [runbooks](operations-runbook.md) and [backup and DR](backup-dr.md) (`aws s3api ... --endpoint-url`): lifecycle, object lock, versions, multipart |
| `rclone/rclone` (pin in mirror) | bucket replication to a DR site ([backup and DR](backup-dr.md)) |

### Other build-time inputs

| Input | Mirror |
|---|---|
| pnpm (via corepack) | `COREPACK_NPM_REGISTRY`, derived from `NPM_CONFIG_REGISTRY` in the Dockerfile and CI; re-signing mirrors may also need `COREPACK_INTEGRITY_KEYS=0` |
| `uv==0.10.4`, `pyyaml==6.0.3` (CI) | PyPI mirror via `UV_INDEX_URL` |
| kubeconform schemas | set `KUBECONFORM_SCHEMA_LOCATION` to a mirror carrying the Kubernetes schemas and the CRD catalog (CNPG, ECK, Rook-Ceph `ceph.rook.io` + `objectbucket.io`, barman-cloud, cert-manager, Prometheus); empty = online GitHub schemas |
| ClamAV signatures | private freshclam mirror (`parsers.clamav.mirrorUrl` + `mirrorCIDRs`); otherwise only the signatures baked in the image |
| Docling models | baked into the docling-serve image (no cache volume: it would hide them) |

## Build the mirror (connected host)

This is the only place the documentation asks you to reach the internet. Run it on a host outside the enclave, then move the result in.

```bash
# images: copy each row of the tables above (resolve the digest, then copy by digest)
skopeo copy --all docker://docker.io/curlimages/curl:8.10.1 docker://registry.internal.example/curlimages/curl:8.10.1
# Terraform providers (kubernetes, helm)
terraform providers mirror ./provider-mirror
# charts: helm pull <chart> --version <v>, then helm push to oci://registry.internal.example/charts
# Python and npm: populate the PyPI / npm mirrors from backend/uv.lock and frontend/pnpm-lock.yaml
# reranker: deploy/game-01/reranker.sh pull   (pinned HF commit, SHA256SUMS, TEI image tar)
```

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
| Embeddings | `embeddinggemma` (768-d, `ROR_EMBED_MODEL` / `ROR_EMBED_DIMS` defaults), or e5-mistral-7b-instruct (4096-d) | served on the OpenAI-spec endpoint (`ROR_LLM_BASE_URL`); changing it is a re-embed ([ADR-0006](../adr/0006-embedding-model-config.md)) |
| Tokenizer files | `sentence-transformers/all-MiniLM-L6-v2` by default (`ROR_EMBED_TOKENIZER`); use the tokenizer of the embedding model you serve | chunk sizing in workers. Docling's `HuggingFaceTokenizer.from_pretrained` passes the setting to `transformers.AutoTokenizer.from_pretrained`, which takes a Hub id (from the local HF cache) or a local directory. The repo neither bakes the files into the image nor sets an HF offline switch: set `ROR_EMBED_TOKENIZER` to a mounted directory or pre-populate the cache |
| Reranker | `BAAI/bge-reranker-v2-m3` @ HF commit `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, served by TEI (below) | `deploy/game-01/reranker.sh pull` writes `SHA256SUMS`. LFS sha256: `model.safetensors` `d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286`, `sentencepiece.bpe.model` `cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865`, `tokenizer.json` `69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15`; plus `config.json`, `special_tokens_map.json`, `tokenizer_config.json` (sum them at pull) |
| Chat | `ROR_CHAT_MODELS` (comma-separated, default `qwen2.5:14b`, first is the default) | site allowlist; ≥ 32k context recommended |
| Summary / planner / router small models | `ROR_SUMMARY_MODEL` (default `qwen2.5:3b`); `ROR_AGENTIC_PLANNER_MODEL` (empty = first chat model; `qwen2.5:3b` suggested); optional `ROR_ROUTER_MODEL`, `ROR_RAPTOR_MODEL`, `ROR_EVAL_JUDGE_MODEL` | any model on the OpenAI-spec endpoint; empty settings fall back as in `deployment-guide.md` 5.1 |
| Context-note model (optional) | `ROR_CONTEXT_MODEL`: an Ollama variant (`FROM qwen2.5:14b`, `PARAMETER num_ctx 16384`, `deploy/game-01/README.md`) | built on the inference host from a mirrored base model; nothing downloads at runtime |
| Document parsing | granite-docling (VLM) or Docling's bundled layout/OCR/table models | bake model files into the docling-serve image |
| GROBID models | bundled in image | |

## Evaluation datasets (dev reference set, optional)

| Artifact | Mirror | Notes |
|---|---|---|
| SciFact (BEIR format: `corpus.jsonl`, `queries.jsonl`, `qrels/test.tsv`) | download once outside the enclave, unpack to a directory on the host that runs the CLI | imported offline by `roracle eval import-beir <dir> --set scifact --owner <non-admin user> --principals a,b,c` (private by default; the worker then ingests each abstract like an upload, so the embedding and summary endpoints must be up; re-running resumes); the importer contains no network code. License **CC BY-NC 2.0**: non-commercial research and evaluation only; keep it out of production corpora and do not redistribute it with the product |

## Other data

ClamAV signature database (private freshclam mirror), CRL/OCSP endpoints for X.509 if checked,
TeX Live packages (bundled in image).

## Digests recorded at authoring time (reference only)

These are the manifest-list digests the tags resolved to on 2026-11-15, looked up from a connected host. They are **not**
pinned anywhere in the repo, may have moved, and must be re-verified (`crane digest <image>:<tag>`) before you rely on them.

| Image | Tag | Digest |
|---|---|---|
| `ghcr.io/cloudnative-pg/postgresql` | `16.6` | `sha256:53be6d727c7b3cf0df2b147f7ce7cdd31d37fd285314294871a4ccc3837b6872` |
| `docker.elastic.co/elasticsearch/elasticsearch` | `9.5.6` | `sha256:79b4f1de4c15e6eca824723086a51e5934406d614d410cc11be77748cafac537` |
| `curlimages/curl` | `8.10.1` | `sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b` |
| `gotenberg/gotenberg` | `8.20.1` | `sha256:cda4386c7ed38f18bc6897828be01ba7361c99929a5c84ec5e293d7916e29bac` |
| `quay.io/docling-project/docling-serve-cpu` | `v1.38.2` | `sha256:7a480a1604fa3b42efeddc7db9527690d9f9bc903226136013e2bfe6b1a4dfb0` |
| `lfoppiano/grobid` | `0.8.1` | `sha256:820a623a0234cda235d5eb83e924934162c5f0554a843cf665c339da8b51f900` |
| `clamav/clamav` | `1.4` | `sha256:4cc4350d3fded380caf1799e76c0d1c93f3fa40af99c0cb2862ec0c9b117718a` |
| `python` | `3.12-slim` | `sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` |
| `node` | `22-slim` | `sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee` |
| `nginxinc/nginx-unprivileged` | `1.27-alpine` | `sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0` |
| `postgres` | `16` | `sha256:dd2bf3ce2dc83a81674cf7f217fcfd8a5009a4b325dda5b843bfee82a06cff6f` |
| `bitnamilegacy/minio` | `2025.5.24` | `sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c` |
| `gcr.io/kaniko-project/executor` | `v1.23.2-debug` | `sha256:c3109d5926a997b100c4343944e06c6b30a6804b2f9abe0994d3de6ef92b028e` |
| `alpine/helm` | `3.17.3` | `sha256:d899e6316789fec04ee95300a18e454b7942539cbb3d89bde3e0655d6ca2e895` |
| `ghcr.io/yannh/kubeconform` | `v0.6.7-alpine` | `sha256:824e0c248809e4b2da2a768b16b107cf17ada88a89ec6aa6050e566ba93ebbc6` |
