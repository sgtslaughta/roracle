# ADR-0016: Terraform wraps the Helm chart; no cloud resources

Status: Accepted (2026-11-16)

## Context
Operators need a repeatable way to install the operator layer (CNPG, barman-cloud plugin, cert-manager, ECK, Rook-Ceph object storage or an external S3, optional observability), Secrets and the rOracle release on a pre-existing, air-gapped Kubernetes cluster.

## Decision
- `deploy/terraform` drives `helm_release` of the existing chart `deploy/helm/roracle`; the chart stays the single source of truth for workloads and is still installable with plain `helm` (ADR-0013). Terraform adds ordering (operators, CRDs ready, app), typed inputs, and state.
- Providers: `kubernetes` and `helm` only, targeted by kubeconfig path/context. No cloud-provider resources: clusters, networks, DNS and registries are the platform team's.
- Values are assembled from typed variables with `yamlencode` as ordered YAML documents (Helm deep-merges), plus an optional extra values file that wins.
- Secrets: `secrets_mode=create` (from sensitive variables; values are in state) or `existing` (references Secrets managed elsewhere; nothing in state). Documented in the README.
- Air-gap chart repositories/versions and operator image repositories/tags are explicit PLACEHOLDERs. Charts still holding placeholders are omitted from the plan (so `plan` works and shows a `check` warning); a `terraform_data` gate (replaced every run via `plantimestamp()`, so it never sticks in state) fails `apply` via a postcondition while any enabled placeholder remains, unless `allow_placeholders=true`.
- Optional `modules/inference` (GPU OpenAI-spec server + vLLM-score reranker; TEI selectable but not wire-compatible) is disabled by default and placeholder-gated; it ships a default-deny NetworkPolicy admitting only the rOracle api/worker pods, an enforced LLM API key and a hardened pod spec.
- Object storage (decision, 2026-11-16): `object_store = "rook-ceph" | "external"`. MinIO community images ended in Oct 2025 (source-only; `quay.io/minio/{minio,mc}` answer 401), so Terraform no longer installs the MinIO Operator or a tenant and always renders the chart with `minio.mode=external`. `rook-ceph` installs the rook-ceph operator and rook-ceph-cluster charts (RGW `CephObjectStore`, erasure-coded data pool optional, replicated metadata, explicit OSD devices, 3 mons), creates the three buckets (app, PG backups, ES snapshots) with ObjectBucketClaims, wires their credentials into the app Secrets and opens api/worker egress to RGW with a NetworkPolicy. `external` points at an S3 you operate (small installs, dev, kind). The chart's own `minio.mode=tenant` remains for plain-Helm users who bring their own MinIO images.
  Decision matrix input: Ceph needs >= 3 nodes with raw disks and more RAM per OSD than MinIO needs per drive, but is maintained upstream with published images and gives block/file for later reuse; `external` costs nothing in-cluster. Not exercised on live Ceph in CI/kind (no raw disks): validated with `terraform validate/plan` and `helm template` only.

## Consequences
- Two ways to install (helm, terraform) must stay equivalent: Terraform only sets documented chart values, never templates the chart.
- Uninstalling the release deletes the data-tier CRs and PVCs (the chart has no keep policy). `protect_data=true` (default) sets `prevent_destroy` on the release (two resources selected by the variable, since the lifecycle argument cannot be dynamic); the README gives the `state mv` procedure, StorageClass `Retain` and backup guidance.
- State holds secrets in `create` mode: use an access-controlled backend.
