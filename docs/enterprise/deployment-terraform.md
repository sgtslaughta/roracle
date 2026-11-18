# Deploying with Terraform

Audience: operators and SRE. Terraform in `deploy/terraform` wraps the Helm chart described in the [deployment guide](deployment-guide.md) (chart 0.2.0): namespaces, pull secret, operator charts, Secrets, optional Rook-Ceph object storage, optional GPU inference and the `roracle` release. Design reasons are in [ADR-0016](../adr/0016-terraform-iac.md). Terms are in the [glossary](../glossary.md).

It targets a **pre-existing cluster** through a kubeconfig (`kubeconfig_path`, `kubeconfig_context`) and manages no cloud resources. Providers: `hashicorp/kubernetes ~> 3.0`, `hashicorp/helm ~> 3.0`; Terraform `>= 1.6`. The module layout is in `deploy/terraform/README.md`.

## 1. Modules

| Layer | Module | What |
|---|---|---|
| gate | `terraform_data.placeholder_gate` | fails `apply` while placeholders remain |
| platform | `modules/platform` | app namespace, optional ResourceQuota and LimitRange (`quota`), image pull Secret (`pull_secret`) |
| operators | `modules/operators` | one `helm_release` per operator (toggle with `charts.<key>.enabled`: cnpg, barman, cert_manager, eck, rook_ceph, rook_ceph_cluster, kube_prometheus_stack, loki, otel_collector), operator namespaces, CRD readiness check |
| ceph | `modules/ceph` | with `object_store = "rook-ceph"`: ObjectBucketClaims, RGW TLS, locked backup buckets, S3 Secrets |
| inference | `modules/inference` | optional GPU OpenAI-spec server and reranker (off by default, section 6) |
| secrets | `modules/secrets` | every Secret the chart references (`secrets_mode = "create"`) |
| app | `modules/app` | `helm_release` of the chart; values come from typed variables, `extra_values_file` is merged last and wins |

## 2. Prerequisites

- Terraform >= 1.6, `kubectl` access (also on the runner when `object_store = "rook-ceph"`), a StorageClass, an ingress controller (default namespace `ingress-nginx`).
- The mirror is populated ([mirror manifest](airgap-mirror-manifest.md)): images, operator charts (OCI recommended) and Terraform providers. On a connected host run `terraform providers mirror ./mirror`; on the workstation or CI set a CLI config (`TF_CLI_CONFIG_FILE`) with a `provider_installation { filesystem_mirror { path = "..." } }` block.
- OCI charts: `helm registry login <mirror>` before plan or apply (the helm provider reuses the local Helm registry credentials).
- A Keycloak realm and client ([Keycloak setup](keycloak-setup.md)) and a TLS Secret for the ingress ([security](security.md)).
- Prometheus-operator CRDs while `postgres.monitoring` stays on (enable `kube_prometheus_stack` or install them elsewhere).

## 3. tfvars profiles and sizing

**ECK pin warning.** All four profiles below pin ECK 2.16.0 (chart and image), but the chart's default Elasticsearch 9.5.6 needs an ECK 3.x release that lists 9.5. Raise the `charts.eck` and `operator_images.eck` pins to such a release before mirroring and installing (see [upgrades](upgrades.md)).

Copy an example to a git-ignored file (`*.tfvars` is ignored except `environments/*example*`). Secrets go in through `TF_VAR_secret_data` and `TF_VAR_pull_secret`, never into the file.

| File | Purpose | Key settings |
|---|---|---|
| `environments/example.tfvars` | Dev or single-node profile | `ha = false`, `object_store = "external"`, `protect_data = false`, storage 20Gi PG and 50Gi ES |
| `environments/prod.example.tfvars` | Production profile | `ha = true`, `secrets_mode = "existing"`, `object_store = "rook-ceph"`, `protect_data = true`, observability charts on, storage 500Gi PG and 1Ti ES |
| `environments/kind.tfvars` (+ `kind-values.yaml`) | Local kind rehearsal | registry `localhost:5055`, external S3 stand-in, backups off ([kind rehearsal](deployment-kind-rehearsal.md)) |
| `environments/kind-ceph.tfvars` | Local live Rook-Ceph rehearsal | loop-device OSDs, generated `ceph-osd.tfvars` |

There are no named small, medium or large presets. Size comes from two levers:

- `ha`: `false` renders the dev profile (single api, worker, web, Postgres and ES node, no HPA or PDB, no topology spread, reduced parser replicas and requests); `true` keeps the chart defaults (3 Postgres instances, 3 ES nodes, HPA, PDB, topology spread). Override replicas with `replicas.{api,worker,web}`, volumes with `storage.{postgres,elasticsearch,class}`.
- `quota`: optional ResourceQuota for the app namespace. The examples use `requests_cpu = "24"`, `requests_memory = "64Gi"`, `limits_memory = "96Gi"` for dev and `"120"`, `"400Gi"`, `"480Gi"` (60 PVCs, 6Ti) for production. Size it above the whole bundled data tier.

- `pod_security`: Pod Security Admission labels on the app namespace, `{ enforce = "", warn = "restricted", audit = "restricted" }` by default. Each level is `""` (no label), `privileged`, `baseline` or `restricted`. The default enforces nothing, so no workload can break; `warn` and `audit` report violations. Set `enforce = "restricted"` once the audit log is clean.

For the capacity targets behind the production numbers see the sizing table in the [deployment guide](deployment-guide.md) (section 3) and [capacity planning](capacity-planning.md). Anything not exposed as a variable goes in `extra_values_file = "my-values.yaml"`; the full key list is the [Helm values reference](../reference/helm-values.md).

## 4. Placeholders and the gate

Defaults are repository `oci://MIRROR.example.com/charts`, version `0.0.0-PLACEHOLDER` and registry `MIRROR.example.com`. A chart whose repository or version is still a placeholder is left out of the plan, so `terraform plan` works for review, with a `check` warning and the output `placeholders_remaining`. `terraform apply` fails at the gate, before anything is created, while any placeholder of an enabled component remains. `allow_placeholders = true` is for dry runs only: the placeholder charts are simply not installed.

The gate is replaced on every run (`plantimestamp()`), so a failed apply never leaves a sticky failing gate in state. Because of that, plans are never empty and `-detailed-exitcode` returns 2; this is deliberate. Example tfvars need a reachable mirror at plan time because their charts are real.

| Name | Where | What to set |
|---|---|---|
| `mirror_registry` | `variables.tf` | image registry host/path; replaces `MIRROR.example.com` in `deploy/observability/*.values.yaml` and becomes `global.imageRegistry` |
| `charts.<key>` | `variables.tf` | `repository` (for example `oci://registry/charts`), `version` pinned in the mirror, `chart` if renamed |
| `operator_images.<chart>` | `variables.tf` | `repository` and `tag` of operator images, pushed into the chart values (cnpg, barman, cert-manager, eck, rook-ceph operator). Barman is wired for chart 0.9.0 (`image.registry` and `sidecarImage.registry`, tags `tag` and `sidecar_tag`). Verify the value keys against the mirrored chart and extend with `charts.<key>.values`. Alternative: registry mirroring in the container runtime, then use the upstream names |
| `pull_secret` | `variables.tf` | mirror credentials; created in the app, every operator and the inference namespace |
| `charts.kube_prometheus_stack`, `loki`, `otel_collector` | `variables.tf` | disabled by default; set `enabled = true`. Loki also needs `loki_s3_credentials` and its S3 endpoint (override through `charts.loki.values`) |
| `app_chart.repository`, `.version` | `variables-app.tf` | only when installing the chart from an OCI mirror instead of the local path |
| `images.backend_digest`, `web_digest` (or tags) | `variables-app.tf` | CI-pushed digests (empty tag = chart `appVersion`) |
| `inference.llm.image`, `inference.rerank.image`, `model_path` | `variables-app.tf` | only when `inference.enabled = true` |
| `ingress.host`, `keycloak.issuer`, `models.*`, `network.*` | tfvars | site values, not gated, required by the chart |

## 5. Secrets: create or existing

`secrets_mode = "create"` (default): Terraform builds the app Secret (`ROR_DATABASE_URL`, `ROR_ELASTIC_URL`, S3 and LLM keys) and the CNPG and ES basic-auth Secrets from the sensitive `secret_data`. S3 keys come from `secret_data.s3_*` (external) or from the Ceph claim Secrets (rook-ceph). **The values are stored in Terraform state.** Pass them with `TF_VAR_secret_data` and protect the state.

`secrets_mode = "existing"`: nothing is created and no secret value touches state. The Secrets named in `secret_names` must exist in the app namespace first (External Secrets, Vault, `kubectl`); the layout is in [deployment guide](deployment-guide.md), section 4.2. Recommended for production.

Notes: the chart has no confidential OIDC client secret (the web client is public). Other keys can go in `secret_data.extra` and be consumed through `api.extraEnv` and `worker.extraEnv` `valueFrom.secretKeyRef` in the extra values file. In create mode `secret_data.llm_api_key` is required while `models.llm_key_used = true`. For external S3 with backups on, separate backup and snapshot identities are checked.

## 6. Plan, apply and order

```sh
cd deploy/terraform
terraform init                                   # providers from the mirror
cp environments/example.tfvars my.tfvars                                  # edit: git-ignored
terraform plan  -var-file=my.tfvars -var allow_placeholders=true   # review
terraform apply -var-file=my.tfvars                                 # placeholders must be replaced
```

Dependency chain in one apply: placeholder gate, platform (namespaces, quota, pull secret), operators (barman plugin after CNPG and cert-manager), CRD readiness (deferred to apply), Secrets and inference, then `helm_release.roracle`. The release uses `wait` and `wait_for_jobs`; hooks wait up to `helm_timeout_s` (default 1800 s). The chart's worker waits for the schema (bounded to 25 minutes), so the post-install migrate hook does not deadlock against `--wait`.

Staged alternative, also for first installs where operator CRDs are slow: apply with `-var deploy_app=false`, then re-apply with `deploy_app=true`. The data source deferral also shows an in-place update of the CA Secret on every plan.

Generated database and Elasticsearch URLs assume the release name decides the chart fullname (`app_fullname` in `app-values.tf`): do not set `nameOverride` or `fullnameOverride` in `extra_values_file`.

### Optional inference module

`inference = { enabled = true, ... }` deploys LLM and reranker Deployments (GPU, `Recreate`, models read-only from PVC `inference-models`, which must be ReadOnlyMany or ReadWriteMany if pods land on different nodes; images are gated placeholders). A default-deny NetworkPolicy admits only the rOracle `api` and `worker` pods on port 8000 (plus DNS egress); the app chart egress is opened to that namespace through `networkPolicy.egressCIDRs.llm`. The LLM server enforces the API key from Secret `inference-keys` (`secret_data.llm_api_key`).

Reranker engine `vllm` (default, `--task score`) serves Cohere-style `/v1/rerank`, the backend default (`ROR_RERANK_API=cohere`). Engine `tei` serves `/rerank` with `texts`; the backend supports it with `ROR_RERANK_API=tei` (chart value `rerank.api`), which this stack does not set, so add `rerank: {api: tei}` to `extra_values_file`. `rerank.api_key_enabled` and `secret_data.rerank_api_key` protect the server; the backend sends `ROR_RERANK_API_KEY` as a Bearer token only when the chart's `secrets.rerankApiKeyEnabled` is true, which Terraform also does not set. Pods run non-root with a read-only root filesystem, seccomp `RuntimeDefault` and no service-account token. Verify that the mirrored image works as the configured `run_as_user` (default 10001).

## 7. State backend

Default is local state. `backend.tf` holds commented examples: an S3-compatible backend outside this cluster (`use_lockfile`, needs Terraform >= 1.10) and the Kubernetes Secret backend. State contains secrets in `create` mode: restrict access, encrypt at rest, never commit it, and do not host it on the object store this stack deploys. Switch with `terraform init -migrate-state`. `.terraform.lock.hcl` is committed.

## 8. Protecting data: `protect_data`

Uninstalling the `roracle` release deletes the data-tier custom resources: CNPG PVCs are garbage-collected with the `Cluster`, and ECK deletes its PVCs (`volumeClaimDeletePolicy` default `DeleteOnScaledownAndClusterDeletion`). The Ceph OSDs and buckets live outside the release (`preservePoolsOnDelete`, bucket class `Retain`). The chart sets no `helm.sh/resource-policy: keep`. Whether the volume is then reclaimed follows the StorageClass `reclaimPolicy`: use `Retain` in production. **A targeted `terraform destroy -target=module.app` plus `state rm` of the namespaces does not protect the data.**

- `protect_data = true` (default) puts `prevent_destroy` on the release, so any plan that would destroy or replace it errors. The module holds two identical resources (`protected` and `unprotected`) because `prevent_destroy` cannot take a variable. Disposable environments set `protect_data = false`.
- To change the setting on a live install, move the state first, then apply with the new value: `terraform state mv 'module.app[0].helm_release.protected[0]' 'module.app[0].helm_release.unprotected[0]'` (or the reverse). **Flipping the variable with a plain apply destroys and re-creates the release in either direction, deleting the data tier.** A `moved` block maps the older address `helm_release.roracle` to `protected[0]`.
- Before any deliberate teardown take backups and snapshots, replicate the backup buckets off-cluster, and set the StorageClass to `Retain`. Terraform destroys the app before the operators; destroying operators before their custom resources are gone leaves orphaned finalizers.
- In `existing` mode the Secrets are not Terraform-owned and survive destroy.

### Failed apply (tainted release)

A failed install (a hook failed, a timeout) leaves the release **tainted** in state (`atomic = false`). Fix the cause, then `terraform untaint 'module.app[0].helm_release.protected[0]'` (or `.unprotected[0]`) and apply again; Helm upgrades the failed release. **Never apply a tainted release of the unprotected variant that holds data**: Terraform replaces it, which uninstalls the release and deletes the data tier. With `protect_data = true` the next plan errors on `prevent_destroy` instead.

## 9. Object storage: Rook-Ceph or external S3

The chart has no bundled object store (MinIO community images ended in October 2025, see the [mirror manifest](airgap-mirror-manifest.md)); `ROR_S3_ENDPOINT` is required.

### External S3 (`object_store = "external"`, the default)

For small installs and dev. Set `s3.endpoint` (and `s3.ca_secret` for a private CA) and `secret_data.s3_access_key` and `secret_data.s3_secret_key`. Buckets `s3.bucket`, `pg_backup_bucket` and `es_snapshot_bucket` must exist and be configured as in section 10. While `backups_enabled` is on (default) the apply requires **separate identities** in `secret_data.s3_pg_backup` and `s3_es_snapshot` with access keys different from the app key; `backups_enabled = { postgres = false, elasticsearch = false }` turns them off.

### Rook-Ceph (`object_store = "rook-ceph"`)

Production example: `environments/prod.example.tfvars`. The operator layer installs `rook-ceph` and `rook-ceph-cluster` (placeholder-gated charts and images `docker.io/rook/ceph:v1.18.0`, `quay.io/ceph/ceph:v19.2.3`; CSI, toolbox and monitoring are off). `modules/ceph` claims the three buckets with ObjectBucketClaims (one Ceph user per bucket, so identities are separate), waits with `kubectl wait` until they are Bound, and creates the Secrets `roracle-s3`, `roracle-pg-backup-s3` and `roracle-es-s3` from the generated credentials **in every `secrets_mode`**. Do not create those three yourself (the create fails with "already exists"); in `existing` mode create the others. The credentials are in Terraform state.

- **TLS is required** (gated). `ceph.rgw.cert_manager_issuer = "self-signed-ca"` (internal CA chain) or a ClusterIssuer name makes Terraform issue the RGW certificate and copy the CA into the app namespace (`roracle-s3-ca`, wired as trust for the app, CNPG and ES). Or bring `ceph.rgw.tls_secret` (kubernetes.io/tls, in the Ceph namespace) plus `s3.ca_secret`. `ceph.rgw.allow_plain_http = true` is for test clusters only.
- **NetworkPolicy**: RGW ingress is limited to the app and Ceph namespaces; egress from api, worker, migrate and the CNPG and ES pods is the chart's `networkPolicy.s3` set to the RGW pods.
- **Variables**: `ceph.device_filter` or `ceph.nodes` (OSD devices; never all devices, empty is a gated placeholder), `mon_count` (3), `data_pool` (erasure `data_chunks` + `coding_chunks`, default 2+1, or replicated), `metadata_replicas` (3), `rgw.instances`, `resources`.
- **Prerequisites**: at least 3 nodes with raw, unformatted disks, about 4-6 GiB RAM per OSD plus 1-2 GiB per mon and 2-4 GiB per RGW, a low-latency network, at least 3 failure domains for erasure 2+1. With fewer nodes or no spare disks use `external`.
- **Backup buckets are locked and writer-only.** A Job (backend image from the mirror, boto3, admin user `roracle-admin`; keys read from Rook's Secret) creates the PG backup and ES snapshot buckets with S3 object lock (`ceph.object_lock`: GOVERNANCE, 30 days by default; keep it at or above the chart retention), a lifecycle rule and a bucket policy. The per-bucket writers may put, get, list and delete (marker) but are denied version deletion, lock bypass and lock, lifecycle and policy changes; the app identity has no grant. The app bucket has a 1-day `tmp/` expiry and multipart-abort lifecycle. Run the pre-GA check that barman-cloud and the ES S3 repository work against locked RGW buckets.
- Backups still live in the same Ceph cluster: replicate them off-cluster (RGW multisite or a scheduled `rclone`) or point `s3.*_bucket` at an external S3 elsewhere.
- **TLS rotation.** The self-signed CA is valid 10 years, the RGW certificate 1 year (renewed 30 days before). Rook may not reload a changed certificate Secret: after a leaf renewal run `kubectl -n rook-ceph rollout restart deploy -l app=rook-ceph-rgw`. The CA copy in the app namespace is a Terraform snapshot: after a CA renewal re-run `terraform apply`, then restart api and worker. A third-party ClusterIssuer whose Secret has no `ca.crt` is refused (use `tls_secret` and `s3.ca_secret`).
- **Destroy**: `rook-ceph-cluster` has no `protect_data` guard. Rook blocks deleting the object store while buckets exist, so a destroy hangs rather than wiping data. Teardown steps: [kind rehearsal, decommissioning](deployment-kind-rehearsal.md).

The local rehearsal found that RGW listens on ports 443 and 80 (the ingress policy and chart egress had assumed 8443 and 8080, since fixed), and that the first repository analysis may fail once with a spurious RGW `BucketAlreadyExists` (409), which the script retries ([kind rehearsal](deployment-kind-rehearsal.md)). The real-Ceph path is validated only by `terraform validate`, `plan`, `helm template` and the local loop-device rehearsal; treat the first real install as a rehearsal.

## 10. External S3 commands (object lock, lifecycle, policy)

The chart creates no users, retention or lifecycle: with `object_store = "external"` these are operator duties. Replace `$S3`, the buckets and the admin profile. The admin identity owns the buckets; the writers are the identities in `secret_data.s3_pg_backup` and `s3_es_snapshot`. Retention must be at or above the chart retention (30 days).

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
# (exact statements: deploy/terraform/modules/ceph/bucket-setup.py; adapt the Principal to your IAM), then:
aws --endpoint-url $S3 s3api put-bucket-policy --bucket roracle-pg-backup --policy file://pg-policy.json
```

The api sets the `tmp/` lifecycle itself: at start it calls `ensure_tmp_lifecycle`, which adds the rule `roracle-tmp-expire` (expire `tmp/` after 1 day, abort incomplete multipart uploads after 1 day) to the app bucket when no `tmp/` rule exists. It never fails the start: on an S3 that denies the call it logs a warning and the rule stays your duty. Set it by hand then:

```sh
aws --endpoint-url $S3 s3api put-bucket-lifecycle-configuration --bucket roracle --lifecycle-configuration \
  '{"Rules":[{"ID":"tmp-expire","Status":"Enabled","Filter":{"Prefix":"tmp/"},"Expiration":{"Days":1}},{"ID":"abort-multipart","Status":"Enabled","Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]}'
```

## 11. Terraform upgrades and checks

Upgrading a release through Terraform follows the order in [upgrades](upgrades.md): take the Postgres backup and ES snapshot first, mirror the new images and charts, bump `charts.*.version` and `images.*`, apply operators first (`deploy_app=false`), then the app. Helm does not upgrade CRDs held in a chart's `crds/` directory (cnpg, cert-manager with `crds.enabled` and ECK ship CRDs as templates, which Helm does upgrade); follow each operator's notes. Read `terraform plan` (the Helm diff) before apply and keep `helm_timeout_s` large, because migrate runs as a hook.

CI verifies `terraform fmt -check -recursive`, `terraform init -backend=false` and `terraform validate` (job `terraform`).
