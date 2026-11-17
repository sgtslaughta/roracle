# rOracle on Kubernetes with Terraform

Terraform wraps the Helm chart `deploy/helm/roracle` ([ADR-0016](../../docs/adr/0016-terraform-iac.md)). It targets a
**pre-existing cluster** through a kubeconfig (`kubeconfig_path`, `kubeconfig_context`) and manages **no cloud resources**.
Providers: `hashicorp/kubernetes ~> 3.0`, `hashicorp/helm ~> 3.0`; Terraform `>= 1.6`.

| Layer | Module | What |
|---|---|---|
| gate | `terraform_data.placeholder_gate` | fails `apply` while placeholders remain |
| platform | `modules/platform` | app namespace, optional ResourceQuota + LimitRange, image pull Secret |
| operators | `modules/operators` | `helm_release` per operator (toggle with `charts.<key>.enabled`), operator namespaces, CRD readiness check |
| secrets | `modules/secrets` | every Secret the chart references (`secrets_mode=create`) |
| app | `modules/app` | `helm_release` of the chart, values from typed variables |
| inference | `modules/inference` | optional GPU OpenAI-spec server + reranker (vLLM score engine; off by default) |

## Prerequisites

- Terraform >= 1.6 (CI uses 1.15.8), `kubectl` access, a StorageClass, an ingress controller (default namespace `ingress-nginx`).
- Mirror populated (see [airgap-mirror-manifest.md](../../docs/enterprise/airgap-mirror-manifest.md)): images, operator charts (OCI recommended),
  Terraform providers. On a connected host: `terraform providers mirror ./mirror`; on the workstation/CI set a CLI config
  (`TF_CLI_CONFIG_FILE`) with a `provider_installation { filesystem_mirror { path = "..." } }` block.
- OCI charts: `helm registry login <mirror>` before plan/apply (the helm provider reuses the local Helm registry credentials).
- Keycloak realm/client per [keycloak-setup.md](../../docs/enterprise/keycloak-setup.md); TLS Secret for the ingress (see [security.md](../../docs/enterprise/security.md)).
- Prometheus-operator CRDs if `postgres.monitoring` stays on (enable `kube_prometheus_stack` or install them elsewhere).

## Placeholders (explicit, gated)

Defaults: repository `oci://MIRROR.example.com/charts`, version `0.0.0-PLACEHOLDER`, registry `MIRROR.example.com`.
A chart whose repository/version is still a placeholder is **left out of the plan** (it cannot be fetched), so `terraform plan` works for review and shows a
`check` warning plus output `placeholders_remaining`. `terraform apply` fails at the gate (before anything is created) while any placeholder of an enabled
component remains, unless `allow_placeholders = true` (dry runs only: the placeholder charts are then simply not installed). The gate is replaced on every run
(`plantimestamp()`), so a failed apply never leaves a sticky failing gate in state. Example tfvars need a reachable mirror at plan time because their charts are real.

| Name | Where | What to set |
|---|---|---|
| `mirror_registry` | `variables.tf` | image registry host/path; replaces `MIRROR.example.com` in `deploy/observability/*.values.yaml` and becomes `global.imageRegistry` |
| `charts.cnpg` / `barman` / `cert_manager` / `eck` / `rook_ceph` / `rook_ceph_cluster` | `variables.tf` `charts` | `repository` (e.g. `oci://registry/charts`), `version` pinned in the mirror; `chart` if renamed |
| `operator_images.<chart>` (`repository`, `tag`) | `variables.tf` | operator images: default `<mirror_registry>/<upstream path>` + tag `0.0.0-PLACEHOLDER`, pushed to the chart values (cnpg, barman, cert-manager, eck incl. `config.containerRegistry`, rook-ceph operator). Operands/sidecars differ per chart (barman is wired for chart 0.9.0: `image.registry` + `sidecarImage.registry`, tags `operator_images.barman.tag`/`.sidecar_tag`): verify the value keys against the mirrored chart and extend with `charts.<key>.values`. Alternative: containerd/CRI registry mirroring, then set these to the upstream names |
| `pull_secret` | `variables.tf` | mirror credentials; the Secret is created in the app, every operator and the inference namespace and referenced by the charts |
| `charts.kube_prometheus_stack` / `loki` / `otel_collector` | same (disabled by default) | as above, set `enabled = true`; Loki also needs `loki_s3_credentials` and its `s3.example.com` endpoint (override via `charts.loki.values`) |
| `app_chart.repository` / `.version` | `variables-app.tf` | only when installing the chart from an OCI mirror instead of the local path |
| `images.backend_digest` / `web_digest` (or tags) | `variables-app.tf` | the CI-pushed digests (empty tag = chart `appVersion`) |
| `inference.llm.image`, `inference.rerank.image`, `model_path` | `variables-app.tf` | only when `inference.enabled = true`; models pre-loaded on PVC `inference-models` |
| `ingress.host`, `keycloak.issuer`, `models.*`, `network.*` CIDRs | tfvars | site values (not gated; required by the chart) |

## Secrets: create vs existing

`secrets_mode = "create"` (default): Terraform builds the app Secret (`ROR_DATABASE_URL`, `ROR_ELASTIC_URL`, S3 and LLM keys), CNPG/ES basic-auth Secrets from sensitive `secret_data`; S3 keys come from `secret_data.s3_*` (external) or the Ceph ObjectBucketClaim Secrets (rook-ceph). The values **are stored in Terraform state**; pass them via `TF_VAR_secret_data` and protect the state.
`secrets_mode = "existing"`: nothing is created and no secret value touches state; the Secrets named in `secret_names` (defaults in `variables-app.tf`, layout in
deployment guide 4.2) must exist in the app namespace first (External Secrets, Vault, `kubectl`). Recommended for production.
The chart has no confidential OIDC client secret (the web client is public); other keys can go into `secret_data.extra` (added to the app Secret) and be consumed from the extra values file via `api.extraEnv`/`worker.extraEnv` `valueFrom.secretKeyRef`. In create mode `secret_data.llm_api_key` is required while `models.llm_key_used=true` (the chart secretKeyRef is not optional) and credentials for the bundled MinIO users are checked.

## Plan / apply order

```sh
cd deploy/terraform
terraform init                                   # providers from the mirror
terraform plan  -var-file=environments/dev.tfvars -var allow_placeholders=true   # review (copy environments/example.tfvars first)
terraform apply -var-file=environments/dev.tfvars                                 # placeholders must be replaced
```

Dependency chain inside one apply: placeholder gate -> platform (namespaces, quota, pull secret) -> operators (barman plugin after CNPG + cert-manager) -> CRD readiness
(`data.kubernetes_resource` on the operator CRDs, deferred to apply) -> Secrets/inference -> `helm_release.roracle` (hooks wait up to `helm_timeout_s`, default 30 min).
Staged alternative (also for first installs where operator CRDs are slow): `-var deploy_app=false`, apply, then re-apply with `deploy_app=true`.
Prod: `environments/prod.example.tfvars` (`ha = true`, `secrets_mode = "existing"`, observability on). Dev profile (`ha = false`) is single-instance with small resources.
Anything not exposed as a variable: `extra_values_file = "my-values.yaml"` (merged last, wins).

Note: the placeholder gate is replaced on every plan (`plantimestamp()`), so plans are never empty and `-detailed-exitcode` returns 2; this is deliberate (a static gate would stick after a failed apply on Terraform < 1.10). It also defers data sources (e.g. `kube-root-ca.crt`) to apply, showing an in-place update of the CA Secret on every plan.

## State backend

Default is local state. `backend.tf` holds commented examples: S3-compatible (an S3 outside this cluster, `use_lockfile`) and the Kubernetes Secret backend.
State contains secrets in `create` mode: restrict access, encrypt at rest, never commit it. Do not host state on the tenant this stack deploys.
Switch with `terraform init -migrate-state`. The S3 `use_lockfile` example needs Terraform >= 1.10. `*.tfvars` (except `environments/*example*`) are git-ignored; `.terraform.lock.hcl` is committed.

## Upgrade

1. Take a Postgres backup and ES snapshot ([runbook](../../docs/enterprise/operations-runbook.md), section 2).
2. Mirror the new images/charts, bump `charts.*.version` / `images.*`. Operators first (`deploy_app=false`), then the app. CRD upgrades: Helm does not upgrade CRDs in a chart's `crds/`
   directory (chart specific: cnpg, cert-manager with `crds.enabled` and ECK ship CRDs as templates, which Helm does upgrade); follow each operator's upgrade notes.
3. `terraform plan` and read the helm diff before apply. Keep `helm_timeout_s` large: migrate runs as a hook. The release uses `wait` + `wait_for_jobs`, so a green apply means workloads are Ready; the chart's worker waits for the schema (bounded 25 min) so the post-install migrate hook does not deadlock against `--wait`.

## Destroy cautions

- Uninstalling the `roracle` release deletes the data-tier CRs: CNPG PVCs are garbage-collected with the `Cluster`, ECK deletes its PVCs
  (`volumeClaimDeletePolicy` default `DeleteOnScaledownAndClusterDeletion`), the Ceph OSDs and buckets (rook-ceph) live outside the release (`preservePoolsOnDelete`, bucket class `Retain`). The chart sets no
  `helm.sh/resource-policy: keep`. Whether the PersistentVolume (and disk) is then reclaimed follows the StorageClass `reclaimPolicy`: use `Retain` for production.
  **A targeted `terraform destroy -target=module.app` and `state rm` of the namespaces do NOT protect the data.**
- Guard: `protect_data = true` (default) puts `lifecycle { prevent_destroy = true }` on the release, so any plan that would destroy or replace it errors.
  `prevent_destroy` cannot take a variable, so the module holds two identical resources (`protected` / `unprotected`). To change the setting on a live install:
  `terraform state mv 'module.app[0].helm_release.protected[0]' 'module.app[0].helm_release.unprotected[0]'` (or the reverse), then apply with the new value.
  Disposable environments set `protect_data = false`.
  **Warning:** flipping `protect_data` with a plain apply destroys and re-creates (uninstalls, then installs) the release in either direction, deleting the data tier: always `state mv` first.
  A `moved` block maps the pre-0.1 address `helm_release.roracle` to `protected[0]`, so states from the first commit keep their release when `protect_data = true`.
- Before any deliberate teardown: backups and snapshots (and replicate the backup buckets off-cluster: with rook-ceph the backup buckets live in the same Ceph cluster: replicate them (RGW multisite / `rclone`) or use `external` S3 in another site), StorageClass `Retain`.
- Operator namespaces are created by Terraform; destroying operators before their CRs are gone leaves orphaned finalizers. Terraform destroys app before operators.
- In `existing` mode the Secrets are not Terraform-owned and survive destroy.

## Failed apply (tainted release)

A failed install (e.g. a hook failed, timeout) leaves the `helm_release` in state **tainted** (`atomic = false`). Fix the cause first, then
`terraform untaint 'module.app[0].helm_release.protected[0]'` (or `.unprotected[0]`) and apply again (Helm upgrades the failed release).
**Never apply a tainted release of the unprotected variant (`protect_data = false`) that holds data**: Terraform replaces it, i.e. uninstalls the release and
deletes the data-tier CRs and PVCs. With `protect_data = true` the next plan errors on `prevent_destroy` instead (untaint, then apply).

## Object storage: Rook-Ceph or external S3

`object_store = "external"` (default; small installs, dev, kind): set `s3.endpoint` (+ `s3.ca_secret` for a private CA) and `secret_data.s3_access_key/s3_secret_key`
(optional `s3_pg_backup`, `s3_es_snapshot`; they default to the app identity). Buckets (`s3.bucket`, `pg_backup_bucket`, `es_snapshot_bucket`) must exist; backups and snapshots
are enabled by the chart defaults. Terraform never deploys MinIO: the community images ended in Oct 2025 (see the [mirror manifest](../../docs/enterprise/airgap-mirror-manifest.md)).

`object_store = "rook-ceph"` (production example in `environments/prod.example.tfvars`): the operator layer installs `rook-ceph` + `rook-ceph-cluster` (placeholder-gated charts and
images `docker.io/rook/ceph:v1.18.0`, `quay.io/ceph/ceph:v19.2.3`; CSI, toolbox and monitoring off), `modules/ceph` claims the three buckets with ObjectBucketClaims, waits
(`kubectl wait`, so `kubectl` must be on the runner) until they are Bound, reads the generated credentials into the app Secrets and adds a NetworkPolicy from api/worker/migrate to RGW.
Variables: `ceph.device_filter` / `ceph.nodes` (OSD devices: **never** all devices; empty is a gated placeholder), `mon_count` (3), `data_pool` (erasure `data_chunks`+`coding_chunks`, default 2+1, or replicated), `metadata_replicas` (3),
`rgw.instances` / `rgw.tls_secret` (HTTPS; add `s3.ca_secret`), `resources` (mon/mgr/osd). In `secrets_mode = "existing"` the OBC Secrets (`roracle-app`, `roracle-pg-backup`, `roracle-es-snapshot` in the app namespace) must be mapped into your Secrets (see the comment in prod.example).

Ceph prerequisites: >= 3 nodes with **raw, unformatted disks** (no partitions or filesystems), kernel with RBD/LVM support, around 4-6 GiB RAM per OSD plus 1-2 GiB per mon and 2-4 GiB per RGW,
a low-latency network between nodes, and (for erasure 2+1) at least 3 failure domains. For fewer nodes or no spare disks use `external`.
Backups: the PG/ES backup buckets sit in the same Ceph cluster, which the former bundled MinIO did as well; for disaster recovery replicate them off-cluster (RGW multisite sync or a scheduled `rclone`/`rgw` replication to a second site), or point `s3.*_bucket` at an external S3 in another site.
This path is validated only by `terraform validate/plan` and `helm template` (the kind rehearsal has no raw disks); treat the first real install as a rehearsal.

## Optional inference module

`inference = { enabled = true, ... }` deploys vLLM-style LLM and reranker Deployments (GPU, `Recreate`, models read-only from PVC `inference-models`, which must be
ReadOnlyMany/ReadWriteMany if pods land on different nodes; images are gated placeholders). Controls: default-deny NetworkPolicy in the inference namespace admitting only the
rOracle `api`/`worker` pods on 8000 (plus DNS egress); the app chart egress is opened to that namespace/port 8000 through `networkPolicy.egressCIDRs.llm` values;
the LLM server enforces the API key from Secret `inference-keys` (= `secret_data.llm_api_key`, same value rOracle sends). Reranker: default engine `vllm` (`--task score`, Cohere-style
`/v1/rerank`, which is what the backend sends). TEI (`engine = "tei"`) has a different wire format (`/rerank` with `texts`) and needs a backend adapter first. Reranker API key
(`rerank.api_key_enabled`, `secret_data.rerank_api_key`) stays off until the backend sends `ROR_RERANK_API_KEY` (P13 Task 4, branch feat/retrieval-quality); the NetworkPolicy is the control until then.
Pods run non-root with a read-only root filesystem (emptyDirs for `/tmp`, home/cache, `/dev/shm`), seccomp RuntimeDefault, no SA token; verify the mirrored image works as `run_as_user`.
`app_fullname` (generated DB/ES URLs) assumes no `nameOverride`/`fullnameOverride` in `extra_values_file`.

## Verification

`terraform fmt -check -recursive`, `terraform init -backend=false`, `terraform validate` (CI job `terraform`, image `TERRAFORM_IMAGE`).

## Local rehearsal (kind)

`test/kind-e2e.sh` applies this stack for real on a throwaway kind cluster, with a local `registry:2` as the air-gap mirror
(`localhost:5055`, containerd mirror hosts on the node). Subcommands: `up | apply | verify | destroy | down | all`; idempotent; state, kubeconfig,
tools (pinned kind v0.33.0, kubectl v1.34.12, Helm 3.17.3, `registry:2.8.3@sha256`; sha256 in the script) live in git-ignored `test/.tools/`; `~/.kube/config` is never touched.
Needs bash 4+, docker, terraform, curl, jq, tar, openssl, sha256sum and internet on the first run (the dev kubectl/helm on PATH are shadowed by the pinned ones). `KIND_E2E_KEEP=1` keeps the cluster when `all` fails (otherwise a trap runs `down`). Names: cluster `rorac-kind`, registry `rorac-kind-registry`.

```sh
deploy/terraform/test/kind-e2e.sh all      # up, apply, verify, destroy, down
```

Timings (16 CPU host, warm Docker cache): up 41 s (images mirrored, backend/web built), apply 80 s (18 resources; migrate hook included),
verify 3 s, destroy 140 s, down 5 s; whole cycle about 5 min (cold: add upstream pulls and the backend/web image builds).

Verified: operator charts fetched from the mirror as OCI (`helm_release` against `oci://localhost:5055/charts`, plain HTTP on localhost) and operator images
from the mirror (cnpg, barman plugin + sidecar, cert-manager, ECK); gate refuses (also `object_store=rook-ceph` without mirror values or OSD devices); apply with `MIRROR.example.com` and creates nothing, while
`allow_placeholders=true` passes it (separate throwaway state, gate resource only); all pods Ready; migrate job succeeded; API `/healthz` and `/readyz`
(db, Elasticsearch, S3) 200; web served; every container image outside kube-system comes from the mirror; `terraform destroy` with `protect_data=true` is
refused by `prevent_destroy` with the data tier intact; then `state mv` protected -> unprotected and destroy with `protect_data=false` removes everything.
Profile: `environments/kind.tfvars` + `environments/kind-values.yaml` (1 replica, small requests, ES 1.5 GiB limit, observability and inference off).

Known gaps (not faked):
- **Object storage is external, Rook-Ceph is not rehearsed** (Ceph needs raw disks; kind has none). The app uses `object_store = "external"` with a `bitnamilegacy/minio` stand-in
  (`test/s3-stub.yaml`; dev/CI-only image, never shipped; pod CIDR `10.244.0.0/16` as `network.s3_cidrs`). The rook-ceph path is validated by `terraform validate/plan` and `helm template` only.
  PG/ES backups and snapshots (`postgres.backup`, `elasticsearch.snapshot`) are off, so the barman plugin and cert-manager are installed but unused.
- Parsers docling, grobid, clamav and the draft gotenberg are off (memory); only ingest gotenberg runs. No ingress controller (Ingress off), no Keycloak, no LLM: auth and chat are not exercised.
- containerd mirror hosts also redirect quay.io, ghcr.io and docker.elastic.co to the registry, but the node still has internet: image origin is proven by the pod specs (all `localhost:5055/...`), not by network isolation.
- Destroy leaves operator CRDs behind (Helm never deletes CRDs of installed charts; terraform prints a warning per CRD): delete by hand on a reused cluster. `down` removes the whole cluster.
- kindnet NetworkPolicy enforcement is basic; `network.kube_api_cidrs` is left empty (any destination on 443/6443).

CI: manual job `iac-kind-e2e` (stage `terraform`, `when: manual`). It needs a **privileged** runner with Docker-in-Docker (kind nodes and registry are
containers), the dind service sharing the job's network namespace (Kubernetes executor; the script uses `127.0.0.1`), ~8 GB RAM, internet on first run, and
`KIND_E2E_IMAGE` (bash 4+, docker CLI, terraform, curl, jq, tar, openssl, sha256sum; the job fails fast when unset). dockerd is started on loopback only
(`--host=tcp://127.0.0.1:2375`), never on the pod IP. Run the job in a runner namespace with a default-deny ingress NetworkPolicy. Not part of the default pipeline.
