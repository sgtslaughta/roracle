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
| `charts.cnpg` / `barman` / `cert_manager` / `eck` / `minio_operator` | `variables.tf` `charts` | `repository` (e.g. `oci://registry/charts`), `version` pinned in the mirror; `chart` if renamed |
| `operator_images.<chart>` (`repository`, `tag`) | `variables.tf` | operator images: default `<mirror_registry>/<upstream path>` + tag `0.0.0-PLACEHOLDER`, pushed to the chart values (cnpg, barman, cert-manager, eck incl. `config.containerRegistry`, minio operator). Operands/sidecars differ per chart (barman is wired for chart 0.9.0: `image.registry` + `sidecarImage.registry`, tags `operator_images.barman.tag`/`.sidecar_tag`): verify the value keys against the mirrored chart and extend with `charts.<key>.values`. Alternative: containerd/CRI registry mirroring, then set these to the upstream names |
| `pull_secret` | `variables.tf` | mirror credentials; the Secret is created in the app, every operator and the inference namespace and referenced by the charts |
| `charts.kube_prometheus_stack` / `loki` / `otel_collector` | same (disabled by default) | as above, set `enabled = true`; Loki also needs `loki_s3_credentials` and its `s3.example.com` endpoint (override via `charts.loki.values`) |
| `app_chart.repository` / `.version` | `variables-app.tf` | only when installing the chart from an OCI mirror instead of the local path |
| `images.backend_digest` / `web_digest` (or tags) | `variables-app.tf` | the CI-pushed digests (empty tag = chart `appVersion`) |
| `inference.llm.image`, `inference.rerank.image`, `model_path` | `variables-app.tf` | only when `inference.enabled = true`; models pre-loaded on PVC `inference-models` |
| `ingress.host`, `keycloak.issuer`, `models.*`, `network.*` CIDRs | tfvars | site values (not gated; required by the chart) |

## Secrets: create vs existing

`secrets_mode = "create"` (default): Terraform builds the app Secret (`ROR_DATABASE_URL`, `ROR_ELASTIC_URL`, S3 and LLM keys), CNPG/ES basic-auth Secrets, MinIO config and
the three MinIO users from sensitive `secret_data`. The values **are stored in Terraform state**; pass them via `TF_VAR_secret_data` and protect the state.
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

Default is local state. `backend.tf` holds commented examples: S3-compatible (MinIO outside this cluster, `use_lockfile`) and the Kubernetes Secret backend.
State contains secrets in `create` mode: restrict access, encrypt at rest, never commit it. Do not host state on the tenant this stack deploys.
Switch with `terraform init -migrate-state`. The S3 `use_lockfile` example needs Terraform >= 1.10. `*.tfvars` (except `environments/*example*`) are git-ignored; `.terraform.lock.hcl` is committed.

## Upgrade

1. Take a Postgres backup and ES snapshot ([runbook](../../docs/enterprise/operations-runbook.md), section 2).
2. Mirror the new images/charts, bump `charts.*.version` / `images.*`. Operators first (`deploy_app=false`), then the app. CRD upgrades: Helm does not upgrade CRDs in a chart's `crds/`
   directory (chart specific: cnpg, cert-manager with `crds.enabled` and ECK ship CRDs as templates, which Helm does upgrade); follow each operator's upgrade notes.
3. `terraform plan` and read the helm diff before apply. Keep `helm_timeout_s` large: migrate runs as a hook.

## Destroy cautions

- Uninstalling the `roracle` release deletes the data-tier CRs: CNPG PVCs are garbage-collected with the `Cluster`, ECK deletes its PVCs
  (`volumeClaimDeletePolicy` default `DeleteOnScaledownAndClusterDeletion`), only MinIO StatefulSet PVCs survive until the namespace goes. The chart sets no
  `helm.sh/resource-policy: keep`. Whether the PersistentVolume (and disk) is then reclaimed follows the StorageClass `reclaimPolicy`: use `Retain` for production.
  **A targeted `terraform destroy -target=module.app` and `state rm` of the namespaces do NOT protect the data.**
- Guard: `protect_data = true` (default) puts `lifecycle { prevent_destroy = true }` on the release, so any plan that would destroy or replace it errors.
  `prevent_destroy` cannot take a variable, so the module holds two identical resources (`protected` / `unprotected`). To change the setting on a live install:
  `terraform state mv 'module.app[0].helm_release.protected[0]' 'module.app[0].helm_release.unprotected[0]'` (or the reverse), then apply with the new value.
  Disposable environments set `protect_data = false`.
  **Warning:** flipping `protect_data` with a plain apply destroys and re-creates (uninstalls, then installs) the release in either direction, deleting the data tier: always `state mv` first.
  A `moved` block maps the pre-0.1 address `helm_release.roracle` to `protected[0]`, so states from the first commit keep their release when `protect_data = true`.
- Before any deliberate teardown: backups and snapshots (and replicate the backup buckets off-cluster: bundled backups live on the same MinIO that would be destroyed), StorageClass `Retain`.
- Operator namespaces are created by Terraform; destroying operators before their CRs are gone leaves orphaned finalizers. Terraform destroys app before operators.
- In `existing` mode the Secrets are not Terraform-owned and survive destroy.

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
