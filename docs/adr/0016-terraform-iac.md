# ADR-0016: Terraform wraps the Helm chart; no cloud resources

Status: Accepted (2026-11-16)

## Context
Operators need a repeatable way to install the operator layer (CNPG, barman-cloud plugin, cert-manager, ECK, MinIO Operator, optional observability), Secrets and the rOracle release on a pre-existing, air-gapped Kubernetes cluster.

## Decision
- `deploy/terraform` drives `helm_release` of the existing chart `deploy/helm/roracle`; the chart stays the single source of truth for workloads and is still installable with plain `helm` (ADR-0013). Terraform adds ordering (operators, CRDs ready, app), typed inputs, and state.
- Providers: `kubernetes` and `helm` only, targeted by kubeconfig path/context. No cloud-provider resources: clusters, networks, DNS and registries are the platform team's.
- Values are assembled from typed variables with `yamlencode` as ordered YAML documents (Helm deep-merges), plus an optional extra values file that wins.
- Secrets: `secrets_mode=create` (from sensitive variables; values are in state) or `existing` (references Secrets managed elsewhere; nothing in state). Documented in the README.
- Air-gap chart repositories and versions are explicit PLACEHOLDERs. A `terraform_data` gate fails `apply` (postcondition) while any enabled placeholder remains unless `allow_placeholders=true`; `plan` still works for review and shows a `check` warning.
- Optional `modules/inference` (GPU OpenAI-spec server + TEI reranker) is disabled by default and placeholder-gated.

## Consequences
- Two ways to install (helm, terraform) must stay equivalent: Terraform only sets documented chart values, never templates the chart.
- Destroy removes namespaces and, with them, data-tier PVCs; the README lists the precautions (backups first, `prevent_destroy`-style review).
- State holds secrets in `create` mode: use an access-controlled backend.
