# Decision matrix: platform and delivery

Areas 14 and 17-19 of the [decision matrix](decision-matrix.md). The method, weights and status words are defined there.
**Every rating is judgement** (1 worst, 5 best; for ops cost, 5 is cheapest to run). Rating cell: `A` accuracy (here:
correctness and repeatability) ×3, `S` security ×3, `G` air-gap fit ×3, `P` performance/scale ×2, `L` licence ×2,
`O` ops cost ×1. The maximum weighted total is 70. Where a criterion does not separate the options, every option gets
the same rating. Licence notes are general knowledge: confirm them with your legal team.

## 14. Backend shape

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| One Python image, three entrypoints (`api`, `worker`, `migrate`), MCP mounted in the API at `/mcp` | A4 S4 G5 P4 L5 O5 | 62 | **Chosen** | Two images to build, harden and scan (backend and web). API and worker scale independently through replicas (ADR-0009). One auth path for REST and MCP. | [0009](../adr/0009-one-backend-codebase.md) | Decided |
| Separate MCP service | A4 S4 G4 P4 L5 O3 | 57 | Not adopted | A third image and a second copy of the access code. | none | Revisit if MCP load needs separate scaling |
| Service per domain (ingest, search, chat, writing) | A4 S3 G3 P5 L5 O1 | 51 | Rejected | More images to mirror and scan, more network paths, and the ACL logic would be spread out. | [0009](../adr/0009-one-backend-codebase.md) (alternative not discussed in the ADR) | Decided |

## 17. Deployment and IaC

Terraform drives `helm_release` of the existing chart. The chart stays the single source of truth for workloads and
remains installable with plain Helm. There are no cloud resources ([ADR-0016](../adr/0016-terraform-iac.md),
[deployment with Terraform](../enterprise/deployment-terraform.md)). The user asked for an IaC phase with a kind
rehearsal (ledger `.worktrees/iac/.superpowers/sdd/iac/progress.md:8`, 2026-11-16 20:45). Instead of the small, medium
and large sizing profiles discussed there, the shipped Terraform has a boolean `ha` variable (default `false`;
`deploy/terraform/variables-app.tf:84-88`).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Terraform wrapping the Helm chart (plain Helm still supported) | A5 S4 G5 P3 L3 O4 | 58 | **Chosen** | Orders operators, CRDs and the app, and has typed inputs. A placeholder gate fails `apply` while mirror placeholders remain. S4: with `secrets_mode=create` the secret values are in Terraform state (ADR-0016). L3: recent Terraform releases are not under an open-source licence; OpenTofu is the open fork (compatibility with this code not tested). | [0016](../adr/0016-terraform-iac.md) | Decided. Rehearsed on kind ([kind rehearsal](../enterprise/deployment-kind-rehearsal.md)) |
| Helm only | A3 S5 G5 P3 L5 O3 | 58 | Kept as an equivalent path | No ordering of operators and CRDs, and no state. Nothing secret is stored outside the cluster. | [0013](../adr/0013-repo-ci-release.md), [0016](../adr/0016-terraform-iac.md) | Decided: supported and kept equivalent. Terraform may only set documented chart values (ADR-0016) |
| Argo CD or Flux (GitOps) | A4 S4 G3 P3 L5 O2 | 51 | Not adopted | Needs an in-cluster controller and a reachable Git mirror. It does not solve operator ordering better than Terraform. | none | Revisit if the platform team already runs GitOps |

Helm only ties with Terraform (58 each): the licence and secrets-in-state cost cancels out the ordering gain. That is
why the Helm path stays supported and is kept equivalent.

## 18. CI and release

GitLab CI, Conventional Commits, and one SemVer shared by `/VERSION`, the chart `version` and the chart `appVersion`,
checked by `scripts/check-version.sh` ([ADR-0013](../adr/0013-repo-ci-release.md)). The version is 0.2.0: removing the
MinIO tenant was a breaking change before 1.0, so it bumped the minor version (ledger
`.worktrees/iac/.superpowers/sdd/iac/progress.md:15`, 2026-11-16).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| GitLab CI, single SemVer, chart 0.2.0 | A4 S3 G5 P4 L4 O4 | 56 | **Chosen** | Dependencies resolve from mirrors through CI variables, and the images come from the mirror (`REGISTRY_MIRROR`). CI has an `eval` job and a scheduled `perf` budget gate ([eval harness](../developer/eval-harness.md), [perf suite](../developer/perf-suite.md)). S3: CI has no SBOM, vulnerability-scan or image-signing stage ([known limits](../enterprise/security/known-limits.md#platform-and-supply-chain)). | [0013](../adr/0013-repo-ci-release.md) | Decided. Revisit S when a scan and signing stage lands |
| GitHub Actions | A4 S3 G2 P4 L3 O4 | 45 | Not adopted | Air-gapped use needs GitHub Enterprise Server and mirrored actions. | [0013](../adr/0013-repo-ci-release.md) (alternative not discussed in the ADR) | Decided |
| Separate versions per component | A3 S3 G5 P4 L5 O2 | 53 | Not adopted | More compatibility rules to document and test. | [0013](../adr/0013-repo-ci-release.md) (alternative not discussed in the ADR) | Decided |

## 19. Observability

Optional separate releases with no subchart dependencies: kube-prometheus-stack, Loki and the OpenTelemetry collector,
with values in `deploy/observability/`. They are off by default in Terraform (`deploy/terraform/locals.tf:14-16`). The
no-subchart ruling is in ledger `.worktrees/p11-infra/.superpowers/sdd/p11a-production-infra/progress.md:5` (P11a,
2026-11-15, phase date): `helm dependency build` needs the network. See [observability](../enterprise/observability.md).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| kube-prometheus-stack + Loki + OpenTelemetry collector (reference values, optional) | A3 S3 G5 P4 L4 O3 | 52 | **Chosen** | Air-gap friendly, because no chart pulls dependencies. A3 and S3: the repo ships no alert rules and no api/worker `PodMonitor`, so nothing scrapes `/metrics` and no alert fires on 401 or 429 bursts until the operator adds them ([known limits](../enterprise/security/known-limits.md#quota-and-observability)). Loki is AGPL. | ADR: none, see ledger `.worktrees/p11-infra/.superpowers/sdd/p11a-production-infra/progress.md:5` (2026-11-15) | Provisional. Revisit (to decided) once a `PrometheusRule` and a `PodMonitor` ship |
| Elastic observability (reuse the search cluster) | A3 S3 G4 P3 L3 O3 | 45 | Not adopted | Mixes telemetry load into the search cluster and its licence questions. | none | Not adopted (matrix judgement only) |
| No reference stack (the site's own monitoring) | A2 S3 G5 P4 L5 O5 | 53 | Allowed | The site keeps its own tools. rOracle still exposes metrics and a dashboard, but no tested wiring. | none | Provisional: allowed today because every chart is optional. Revisit with row 1 |

The site's own stack ranks 1 point above the reference stack. That is consistent with the stack being optional, and it
is a reason to ship the missing alert rules and `PodMonitor`.
