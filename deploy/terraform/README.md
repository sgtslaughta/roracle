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
The chart has no confidential OIDC client secret (the web client is public); other keys can go into `secret_data.extra` (added to the app Secret) and be consumed from the extra values file via `api.extraEnv`/`worker.extraEnv` `valueFrom.secretKeyRef`. In create mode `secret_data.llm_api_key` is required while `models.llm_key_used=true` (the chart secretKeyRef is not optional) and, for external S3 with backups on, separate backup/snapshot identities are checked.

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

Note: the placeholder gate is replaced on every plan (`plantimestamp()`), so plans are never empty and `-detailed-exitcode` returns 2; this is deliberate (a static gate would stick after a failed apply on Terraform < 1.10). It also defers data sources  to apply, showing an in-place update of the CA Secret on every plan.

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

The chart has no bundled object store any more (MinIO community images ended in Oct 2025, see the [mirror manifest](../../docs/enterprise/airgap-mirror-manifest.md)); `ROR_S3_ENDPOINT` is required.

`object_store = "external"` (variable default; small installs, dev, kind): set `s3.endpoint` (+ `s3.ca_secret` for a private CA) and `secret_data.s3_access_key/s3_secret_key`.
Buckets (`s3.bucket`, `pg_backup_bucket`, `es_snapshot_bucket`) must exist and be configured as under "External S3 commands" below. While `backups_enabled` (default on) the apply requires **separate identities**:
`secret_data.s3_pg_backup` and `s3_es_snapshot` with access keys different from the app key (the app identity must not be able to write or delete backups); `backups_enabled = { postgres = false, elasticsearch = false }` turns them off.

`object_store = "rook-ceph"` (production example in `environments/prod.example.tfvars`): the operator layer installs `rook-ceph` + `rook-ceph-cluster` (placeholder-gated charts and
images `docker.io/rook/ceph:v1.18.0`, `quay.io/ceph/ceph:v19.2.3`; CSI incl. the ceph-csi-operator, toolbox and monitoring off, so nothing else is pulled), `modules/ceph` claims the three buckets with ObjectBucketClaims (one Ceph user per bucket, so identities are
separate automatically), waits (`kubectl wait`, so `kubectl` must be on the runner) until they are Bound, and creates the S3 Secrets (`roracle-s3`, `roracle-pg-backup-s3`, `roracle-es-s3`) from the generated credentials **in every `secrets_mode`**
(they hold only bucket credentials; in `existing` mode create the other Secrets yourself, and do not create these three). The credentials are in Terraform state.
- **TLS is required** (gated): `ceph.rgw.cert_manager_issuer = "self-signed-ca"` (internal CA chain) or a ClusterIssuer name makes Terraform issue the RGW certificate and copy the CA into the app namespace (`roracle-s3-ca`, wired as trust for the app, CNPG and ES);
  or bring your own `ceph.rgw.tls_secret` (kubernetes.io/tls, in the Ceph namespace) plus `s3.ca_secret`; `ceph.rgw.allow_plain_http = true` is for test clusters only. RGW then serves only the secure port.
- **NetworkPolicy**: RGW ingress is limited to the app namespace and the Ceph namespace; egress from api/worker/migrate **and** the CNPG/ES pods (backups, snapshots) is the chart's `networkPolicy.s3` set to the RGW pods.
- Variables: `ceph.device_filter` / `ceph.nodes` (OSD devices: **never** all devices; empty is a gated placeholder), `mon_count` (3), `data_pool` (erasure `data_chunks`+`coding_chunks`, default 2+1, or replicated), `metadata_replicas` (3), `rgw.instances`, `resources` (mon/mgr/osd).
- Prerequisites: >= 3 nodes with **raw, unformatted disks**, ~4-6 GiB RAM per OSD plus 1-2 GiB per mon and 2-4 GiB per RGW, low-latency network, >= 3 failure domains for erasure 2+1. For fewer nodes or no spare disks use `external`.
- **Backup buckets are locked and writer-only** (rook-ceph): a Job (backend image from the mirror, boto3, run as the admin object-store user `roracle-admin`; keys read from Rook's Secret, never copied) creates the PG backup and ES snapshot buckets with S3 object lock, default retention (`ceph.object_lock`, GOVERNANCE 30 d, keep >= the chart retention), a lifecycle rule (noncurrent versions, multipart abort) and a bucket policy: the per-bucket writers (`roracle-pg-backup`, `roracle-es-snapshot`, Rook `CephObjectStoreUser`s) may put/get/list/delete (marker) but are denied version deletion, lock bypass, lock/lifecycle/policy changes; the app identity has no grant. The app bucket is an OBC with a `tmp/` 1-day expiry and multipart-abort lifecycle (`obcAllowAdditionalConfigFields` allows only `bucketLifecycle`; `bucketPolicy` stays off). Unverified on live Ceph: run the pre-GA check that barman-cloud and the ES S3 repository work against locked RGW buckets.
- Backups still live in the same Ceph cluster: replicate them off-cluster (RGW multisite or a scheduled `rclone`) or point `s3.*_bucket` at an external S3 in another site.
- **TLS rotation**: the self-signed CA is valid 10 years, the RGW certificate 1 year (renewed 30 days before). Rook may not reload a changed certificate Secret: after a leaf renewal run `kubectl -n rook-ceph rollout restart deploy -l app=rook-ceph-rgw` (verify with `openssl s_client`). The CA copy in the app namespace (`roracle-s3-ca`) is a Terraform snapshot: after a CA renewal re-run `terraform apply`, then restart api/worker. A third-party ClusterIssuer whose Secret has no `ca.crt` is refused (use `tls_secret` + `s3.ca_secret`).
- With rook-ceph, Terraform owns the Secret names `roracle-s3`, `roracle-pg-backup-s3` and `roracle-es-s3` in every `secrets_mode`: do not create them yourself (the create fails with "already exists").
- Destroy: `rook-ceph-cluster` has no `protect_data` guard; Rook blocks deleting the object store while buckets exist (the destroy hangs rather than wiping data), `preservePoolsOnDelete` and the bucket class `Retain` keep pools and data.
This path is validated only by `terraform validate/plan` and `helm template` (the kind rehearsal has no raw disks); treat the first real install as a rehearsal.

### External S3 commands (object lock, lifecycle, policy)

The chart no longer creates users, retention or lifecycle: with `object_store = "external"` these are operator duties. Replace `$S3` (endpoint URL), buckets and the admin
profile; the admin identity owns the buckets, the writers are the identities in `secret_data.s3_pg_backup` / `s3_es_snapshot`. Retention must be >= the chart retention (30 d).
```sh
export S3=https://s3.internal.example:9000 AWS_CA_BUNDLE=/path/ca.pem
for b in roracle-pg-backup roracle-es-snapshots; do
  aws --endpoint-url $S3 s3api create-bucket --bucket $b --object-lock-enabled-for-bucket
  aws --endpoint-url $S3 s3api put-object-lock-configuration --bucket $b \
    --object-lock-configuration '{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"GOVERNANCE","Days":30}}}'
  aws --endpoint-url $S3 s3api put-bucket-lifecycle-configuration --bucket $b --lifecycle-configuration \
    '{"Rules":[{"ID":"noncurrent","Status":"Enabled","Filter":{"Prefix":""},"NoncurrentVersionExpiration":{"NoncurrentDays":31},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]}'
done
# bucket policy per backup bucket: Allow the writer Get/Put/List/Delete/multipart, Deny version delete + lock bypass + policy changes
# (see modules/ceph/bucket-setup.py for the exact statements; adapt the Principal to your IAM), then:
aws --endpoint-url $S3 s3api put-bucket-policy --bucket roracle-pg-backup --policy file://pg-policy.json
# app bucket: expire tmp/ staging and stale multipart uploads
aws --endpoint-url $S3 s3api put-bucket-lifecycle-configuration --bucket roracle --lifecycle-configuration \
  '{"Rules":[{"ID":"tmp-expire","Status":"Enabled","Filter":{"Prefix":"tmp/"},"Expiration":{"Days":1}},{"ID":"abort-multipart","Status":"Enabled","Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]}'
```
The backend does not set the lifecycle itself yet (tracked on the backend branch); until then the `tmp/` rule is an operator duty on external S3.

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
from the mirror (cnpg, barman plugin + sidecar, cert-manager, ECK); gate refuses apply with `MIRROR.example.com` and creates nothing (also `object_store=rook-ceph` without mirror values, OSD devices or TLS), while
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

## Decommissioning the object store (rook-ceph)

Locked backup buckets and Rook's delete protections make this deliberate. Do it in this order; the admin identity is the break-glass identity.
1. **Export and verify.** Copy every bucket to the new store (`rclone sync` or `aws s3 sync` with `--endpoint-url`, one run per bucket: `roracle`, `roracle-pg-backup`, `roracle-es-snapshots`) and compare object counts/sizes; take a last Postgres backup and ES snapshot first (runbook 2). Point the app at the new store before deleting anything.
2. **Admin credentials** (from the cluster, never from state):
   `kubectl -n rook-ceph get secret rook-ceph-object-user-roracle-roracle-admin -o jsonpath='{.data.AccessKey}' | base64 -d` (and `.data.SecretKey`); export `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_CA_BUNDLE` (the `ca.crt` of `roracle-rgw-tls`), `S3=https://rook-ceph-rgw-roracle.rook-ceph.svc:443` (run from a pod in the Ceph or app namespace, e.g. the backend image, or `kubectl port-forward`).
3. **Empty the buckets.** App bucket (owner = its OBC user: use the `roracle-s3` keys, or delete the OBC in step 5 which empties nothing, so empty it first): `aws --endpoint-url $S3 s3 rm s3://roracle --recursive`.
   Backup buckets (GOVERNANCE): the admin owns them and may bypass retention: list every version and delete it with the bypass flag:
   `aws --endpoint-url $S3 s3api list-object-versions --bucket roracle-pg-backup --query '[Versions,DeleteMarkers][][].{Key:Key,VersionId:VersionId}' --output json > v.json`, then for each entry
   `aws --endpoint-url $S3 s3api delete-object --bucket roracle-pg-backup --key <Key> --version-id <VersionId> --bypass-governance-retention` (loop with `jq -c '.[]'`; repeat until the listing is empty; same for `roracle-es-snapshots`). Then `aws --endpoint-url $S3 s3api delete-bucket --bucket <name>`.
   **COMPLIANCE mode cannot be shortened or bypassed by anyone**, not even the admin: the data stays until each version's retention expires (`retain-until` date: `aws s3api head-object --version-id`). Wait it out; the buckets (and therefore the object store) cannot be deleted before.
4. **Remove the identities and claims.** `kubectl -n roracle delete objectbucketclaim --all`; `kubectl -n rook-ceph delete cephobjectstoreuser --all` (Rook refuses while buckets still exist: a stuck delete means step 3 is incomplete: `kubectl -n rook-ceph describe` shows the reason).
5. **Destroy.** Switch the release with `terraform state mv` as in "Destroy cautions", then `terraform destroy -var-file=... -var protect_data=false`. Rook deletes the `CephObjectStore` only when no buckets/users remain; if the destroy times out, fix step 3/4 and run it again. Pools are kept (`preservePoolsOnDelete`) and the StorageClass reclaim policy is `Retain`.
6. **Wipe the OSD disks before any reinstall.** Rook leaves Ceph metadata on the devices and `/var/lib/rook` on every node: on each node run `rm -rf /var/lib/rook`, and for each OSD device `sgdisk --zap-all /dev/<dev>; dd if=/dev/zero of=/dev/<dev> bs=1M count=100 oflag=direct,dsync` (or `wipefs -a`), plus `ls /dev/mapper/ceph-* | xargs -I% dmsetup remove %` and `rm -rf /dev/ceph-*` if LVM volumes were created. A reinstall on unwiped disks fails OSD prepare (the cluster id differs). Consider the Rook `cleanupPolicy` (set before deleting the CephCluster) for automated wiping.

### Live Rook-Ceph rehearsal (local only)

```sh
deploy/terraform/test/kind-e2e.sh ceph      # = KIND_E2E_PROFILE=ceph ... all;  KIND_E2E_KEEP=1 keeps the cluster on failure
```
**Local only**: it needs a privileged kind node that can create loop devices (`losetup`, `mknod`), ~16 GB RAM and ~8 GB disk (sparse files). Do not run it on shared or Docker-in-Docker runners (loop devices are host-global).
Three 8 GiB sparse files are created inside the kind node and attached as loop devices; each device and file is recorded in `test/.tools/loops.list`, and `down` (also the EXIT trap of `all`) detaches and deletes exactly those, never other loop devices.
Profile: `environments/kind-ceph.tfvars` + the generated `test/.tools/ceph-osd.tfvars` (1 mon, 1 mgr, 1 RGW, failure domain `osd`, EC 2+1 on the three OSDs, cert-manager self-signed TLS, backups on, `allowLoopDevices` and a single-host CRUSH `configOverride` as test-only chart values).
Timings (16 CPU host, warm Docker cache): up 70 s (incl. loop devices, mirroring ~1.5 GB of Ceph images), apply 305 s, verify 55 s, destroy 5 min (bounded, see below), whole run 745 s.

Verified end to end: CephCluster Ready (HEALTH_WARN only for single-mon disk space), CephObjectStore Ready, users Ready, bucket-setup Job completed; app S3 put/get/delete over HTTPS with the copied CA; `tmp-expire` lifecycle on the app bucket; S3 object lock (GOVERNANCE 30 d) on both backup buckets;
the app key gets `AccessDenied` on the backup buckets; the writers get `AccessDenied` on deleting a locked version and on `BypassGovernanceRetention` but may write and create delete markers; CNPG `Backup` (barman plugin) to RGW `completed`;
ES `_snapshot/roracle-s3/_verify`, the repository analysis API (`blob_count=20`), and an SLM `_execute` create followed by a snapshot delete; delete markers/noncurrent versions present afterwards.
Bugs found by it and fixed: RGW listens on 443/80 (the ingress policy and chart egress used 8443/8080); the `rook-ceph` default CRUSH rule needs `osd_crush_chooseleaf_type = 0` on a single host (test-only).
Findings: the very first repository analysis once failed with a spurious RGW `BucketAlreadyExists` (409) and passed on all later runs (the script retries once); the backend/barman checksum environment (`AWS_REQUEST_CHECKSUM_CALCULATION=when_required`) was set on the setup Job and CNPG backups worked without further changes.
Known gaps: `terraform destroy` does not finish (Rook blocks deleting the object store users/store while buckets exist: the script bounds it to 15 min and removes the cluster); single host and loop devices prove the logic, not durability or performance; a real multi-node Ceph install is still a first-install rehearsal for you.

