# rOracle Enterprise Documentation

Operations and security documentation. Back to the [documentation index](../README.md); terms are in the [glossary](../glossary.md).

| Document | For | Covers |
|---|---|---|
| [Deployment guide](deployment-guide.md) | Platform engineers | Topology, prerequisites, sizing, install, configuration, HA, upgrades |
| [Security architecture](security.md) | Security reviewers / ISSO | Trust boundaries, authN/Z, untrusted content, data protection, audit, hardening |
| [Keycloak setup](keycloak-setup.md) | IAM administrators | X.509 flow, clients, mappers, roles, groups as org units |
| [Air-gap mirror manifest](airgap-mirror-manifest.md) | Mirror maintainers | Images, Python/npm packages, models, data files |
| [Operations runbook](operations-runbook.md) | On-call operators | Health signals, alerts, routine procedures, backup/restore, incidents |
| [Agent integration](agent-integration.md) | Agent / tooling teams | MCP endpoint, auth, tools, handoffs, proposals, writing skill |
| [Capacity planning](capacity-planning.md) | Platform engineers | Elasticsearch 9.5.6 sizing, vector index types, Postgres settings the performance budgets assume |

Related: [performance harness, budgets and report](../perf/README.md) (`../perf/report-2026-11-17.md`), [connection budget](../perf/connection-budget.md), [evaluation reports and how they are produced](../eval/README.md).

Design sources: [spec](../spec.md), [ADRs](../adr/README.md), [implementation plans](../superpowers/plans/).
