# rOracle Enterprise Documentation

Operations and security documentation. Back to the [documentation index](../README.md); terms are in the [glossary](../glossary.md).

| Document | For | Covers |
|---|---|---|
| [Deployment guide](deployment-guide.md) | Platform engineers | Helm chart 0.2.0: topology, prerequisites, sizing, install, NetworkPolicy, configuration notes, HA, TLS |
| [Deploying with Terraform](deployment-terraform.md) | Platform engineers | Modules, tfvars profiles, placeholder gate, secrets modes, `protect_data`, Rook-Ceph or external S3 |
| [Kind rehearsal](deployment-kind-rehearsal.md) | Platform engineers | `kind-e2e.sh` rehearsal with a local mirror, live Ceph rehearsal, decommissioning the object store |
| [Upgrades](upgrades.md) | Platform engineers, on-call | Upgrade order, chart 0.1 to 0.2.0, Elasticsearch 8 to 9, Alembic migrations with risks, re-embed |
| [Security architecture](security.md) | Security reviewers / ISSO | Index of the security pages: trust boundaries, authN/Z, untrusted content, data protection, audit, hardening |
| [Threat model](security/threat-model.md) | Security reviewers / ISSO | STRIDE per boundary, untrusted content, prompt injection, rate limits, connectors and SSRF |
| [Security: access control](security/access-control.md) | Security reviewers / ISSO | Authentication, PATs, authorization, labels and gates, guarantees and non-guarantees |
| [Security: audit](security/audit.md) | Security reviewers / ISSO | What the audit log guarantees and does not record |
| [Security: secrets and TLS](security/secrets-tls.md) | Security reviewers, platform engineers | Terraform secrets, PAT hashing, TLS trust and rotation, ClamAV fail-closed |
| [Security: network](security/network.md) | Security reviewers, platform engineers | Every NetworkPolicy, web headers, pod baseline |
| [Security: data retention](security/data-retention.md) | Security reviewers, operators | Deletion cascade, blob GC, tmp lifecycle, handoff expiry, audit retention |
| [Security: known limits](security/known-limits.md) | Security reviewers / ISSO | Behaviours to know before relying on a control |
| [Keycloak setup](keycloak-setup.md) | IAM administrators | X.509 flow, clients, mappers, roles, groups as org units |
| [Air-gap mirror manifest](airgap-mirror-manifest.md) | Mirror maintainers | Every image (checked against `deploy/**` in CI), operator images, charts, packages, models, SciFact |
| [Operations runbook](operations-runbook.md) | On-call operators | Index of the runbooks below and where the old sections went |
| [Health signals, alerts and admin API](runbook-health-alerts.md) | On-call operators | Probes, queue and job signals, suggested alerts, performance gate, admin API |
| [Routine procedures](runbook-routine.md) | On-call operators | Scaling, retries, re-embed, maintenance, labels, evaluation, reference reparse, secrets |
| [Incidents and job triage](runbook-incidents.md) | On-call operators | Playbooks, job leases and drain, agentic retrieval limits |
| [Backup and disaster recovery](backup-dr.md) | Operators, SRE | CNPG backups and PITR, object-lock buckets, snapshot or rebuild, RPO and RTO, restore drill |
| [Observability](observability.md) | Operators, SRE | Metrics, kube-prometheus-stack, Loki, OpenTelemetry collector, dashboard, alert rules |
| [Agent integration](agent-integration.md) | Agent / tooling teams | MCP endpoint, auth, tools, handoffs, proposals, writing skill |
| [Capacity planning](capacity-planning.md) | Platform engineers | Elasticsearch 9.5.6 sizing with BBQ, Postgres, connection budget, replicas, Ceph RGW, inference |

Related: [Helm values reference](../reference/helm-values.md), [metrics](../reference/metrics.md), [job kinds](../reference/jobs.md), [performance harness, budgets and report](../perf/README.md) (`../perf/report-2026-11-17.md`), [connection budget](../perf/connection-budget.md), [evaluation reports and how they are produced](../eval/README.md).

Design sources: [spec](../spec.md), [ADRs](../adr/README.md), [implementation plans](../superpowers/plans/).
