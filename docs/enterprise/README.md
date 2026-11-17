# rOracle Enterprise Documentation

Operations and security documentation. Back to the [documentation index](../README.md); terms are in the [glossary](../glossary.md).

| Document | For | Covers |
|---|---|---|
| [Deployment guide](deployment-guide.md) | Platform engineers | Helm chart 0.2.0: topology, prerequisites, sizing, install, NetworkPolicy, configuration notes, HA, TLS |
| [Deploying with Terraform](deployment-terraform.md) | Platform engineers | Modules, tfvars profiles, placeholder gate, secrets modes, `protect_data`, Rook-Ceph or external S3 |
| [Kind rehearsal](deployment-kind-rehearsal.md) | Platform engineers | `kind-e2e.sh` rehearsal with a local mirror, live Ceph rehearsal, decommissioning the object store |
| [Upgrades](upgrades.md) | Platform engineers, on-call | Upgrade order, chart 0.1 to 0.2.0, Elasticsearch 8 to 9, Alembic migrations with risks, re-embed |
| [Security architecture](security.md) | Security reviewers / ISSO | Trust boundaries, authN/Z, untrusted content, data protection, audit, hardening |
| [Keycloak setup](keycloak-setup.md) | IAM administrators | X.509 flow, clients, mappers, roles, groups as org units |
| [Air-gap mirror manifest](airgap-mirror-manifest.md) | Mirror maintainers | Images, Python/npm packages, models, data files |
| [Operations runbook](operations-runbook.md) | On-call operators | Health signals, alerts, routine procedures, backup/restore, incidents |
| [Agent integration](agent-integration.md) | Agent / tooling teams | MCP endpoint, auth, tools, handoffs, proposals, writing skill |
| [Capacity planning](capacity-planning.md) | Platform engineers | Elasticsearch 9.5.6 sizing, vector index types, Postgres settings the performance budgets assume |

Related: [Helm values reference](../reference/helm-values.md), [performance harness, budgets and report](../perf/README.md) (`../perf/report-2026-11-17.md`), [connection budget](../perf/connection-budget.md), [evaluation reports and how they are produced](../eval/README.md).

Design sources: [spec](../spec.md), [ADRs](../adr/README.md), [implementation plans](../superpowers/plans/).
