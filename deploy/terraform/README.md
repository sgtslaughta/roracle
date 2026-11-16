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
| inference | `modules/inference` | optional GPU OpenAI-spec server + TEI reranker (off by default) |

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
`terraform plan` works and prints a `check` warning plus output `placeholders_remaining`; `terraform apply` fails at the gate
(before anything is created) unless `allow_placeholders = true` (dry runs only).

| Name | Where | What to set |
|---|---|---|
| `mirror_registry` | `variables.tf` | image registry host/path; replaces `MIRROR.example.com` in `deploy/observability/*.values.yaml` and becomes `global.imageRegistry` |
| `charts.cnpg` / `barman` / `cert_manager` / `eck` / `minio_operator` | `variables.tf` `charts` | `repository` (e.g. `oci://registry/charts`), `version` pinned in the mirror; `chart` if renamed |
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
The chart has no confidential OIDC client secret (the web client is public); other model API keys can go into `secret_data.extra` (added to the app Secret and consumable via `models.extra_env` or the extra values file).

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

## State backend

Default is local state. `backend.tf` holds commented examples: S3-compatible (MinIO outside this cluster, `use_lockfile`) and the Kubernetes Secret backend.
State contains secrets in `create` mode: restrict access, encrypt at rest, never commit it. Do not host state on the tenant this stack deploys.
Switch with `terraform init -migrate-state`. `*.tfvars` (except `environments/*example*`) are git-ignored; `.terraform.lock.hcl` is committed.

## Upgrade

1. Take a Postgres backup and ES snapshot ([runbook](../../docs/enterprise/operations-runbook.md), section 2).
2. Mirror the new images/charts, bump `charts.*.version` / `images.*`. Operators first (`deploy_app=false`), then the app. CRD upgrades: Helm does not upgrade CRDs in a chart's `crds/`
   directory; follow each operator's upgrade notes.
3. `terraform plan` and read the helm diff before apply. Keep `helm_timeout_s` large: migrate runs as a hook.

## Destroy cautions

- `terraform destroy` removes the `roracle` release; the CNPG `Cluster`, ECK `Elasticsearch` and MinIO `Tenant` CRs go with it and the operators delete their PVCs per their own policy. Destroying the namespaces
  deletes **all** remaining PVCs (data tier). Back up (and replicate backup buckets off-cluster) first; the bundled backup buckets live on the same MinIO that would be destroyed.
- Safer teardown: `terraform state rm` the namespaces you want to keep, or `terraform destroy -target=module.app`.
- Operator namespaces are created by Terraform; destroying operators before their CRs are gone leaves orphaned finalizers. Always destroy app before operators (Terraform's order does this).
- In `existing` mode the Secrets are not Terraform-owned and survive destroy.

## Verification

`terraform fmt -check -recursive`, `terraform init -backend=false`, `terraform validate` (CI job `terraform`, image `TERRAFORM_IMAGE`).
