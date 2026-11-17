# rOracle operations runbook

Audience: on-call operators and SRE. Back to the [enterprise index](README.md). This page is the index: the procedures live in
the pages below. Commands assume namespace `roracle`, Helm release `roracle`, CNPG cluster `roracle-pg` and ECK cluster `roracle`
(service `roracle-es-http`); adjust to your names.

## Where to start

| You are... | Go to |
|---|---|
| Paged by an alert, or checking the system | [Health signals, alerts and the admin API](runbook-health-alerts.md) |
| Looking at a stuck queue, slow search, bad upload, malware or data-exposure report | [Incidents and job triage](runbook-incidents.md) |
| Doing planned work: scaling, retries, re-embed, maintenance, labels, eval, rotating secrets | [Routine procedures](runbook-routine.md) |
| Restoring Postgres, Elasticsearch or the object store, or running the drill | [Backup and disaster recovery](backup-dr.md) |
| Wiring dashboards, scrape jobs, logs and traces | [Observability](observability.md) |
| Sizing Elasticsearch, Postgres, Ceph, inference and the connection budget | [Capacity planning](capacity-planning.md) |
| Upgrading | [Upgrades](upgrades.md) |
| Mirroring images, charts, packages and models | [Air-gap mirror manifest](airgap-mirror-manifest.md) |

References: [metrics](../reference/metrics.md), [job kinds](../reference/jobs.md), [Helm values](../reference/helm-values.md),
[settings](../reference/settings.md), [CLI](../reference/cli.md). Mechanics: [jobs and queue](../architecture/jobs-queue.md),
[search index lifecycle](../architecture/search-index-lifecycle.md), [storage](../architecture/storage.md).

## Contents of each page

### Health signals, alerts and the admin API ([runbook-health-alerts.md](runbook-health-alerts.md))

1. Health at a glance: probes, queue, jobs, HTTP, reranker, near-duplicate lookups, Elastic, Postgres; which metrics do and do not exist
2. Suggested alerts (the chart ships a dashboard but no alert rules)
3. Performance gate and budgets
4. Admin API table for operators

### Routine procedures ([runbook-routine.md](runbook-routine.md))

- Scale workers; retry failed jobs; evidence scans
- Change the embedding model with zero downtime; re-embed at scale; re-embed after a prompt change (pre-production only)
- Delete or transfer a document; maintenance (retention purge, job pruning, blob GC, tag reconcile); rebuild the index; clean up `tmp/`
- Rotate secrets; sensitivity labels (audit queries); evaluation (eval CLI, reference set, trend)
- Reference identifiers (`roracle reparse-refs`); derived index fields and shadow indices
- Release upgrade and rollback pointers

### Incidents and job triage ([runbook-incidents.md](runbook-incidents.md))

1. Playbooks: uploads stuck, search slow, chat errors, reranker, data exposure, malware, queue, store losses
2. Job leases, drain and worker shutdown
3. Router, global answers and agentic retrieval
4. After an incident

### Backup and disaster recovery ([backup-dr.md](backup-dr.md))

1. What is backed up and how
2. Elasticsearch: snapshot or rebuild
3. Object-lock backup buckets
4. RPO and RTO
5. Postgres point-in-time recovery
6. Elasticsearch snapshot restore
7. Object-store loss
8. Quarterly restore drill

## Where the old sections went

Earlier versions of this runbook were one page with numbered sections. Links to those sections resolve as follows.

| Old section | Now |
|---|---|
| 1. Health at a glance, Suggested alerts | [runbook-health-alerts.md](runbook-health-alerts.md#1-health-at-a-glance) |
| 2. Routine procedures | [runbook-routine.md](runbook-routine.md) |
| 2. Release upgrade | [runbook-routine.md](runbook-routine.md#release-upgrade-and-rollback) and [upgrades.md](upgrades.md) |
| 2. Sensitivity labels | [runbook-routine.md](runbook-routine.md#sensitivity-labels) |
| 2. Job leases, drain and worker shutdown | [runbook-incidents.md](runbook-incidents.md#2-job-leases-drain-and-worker-shutdown) |
| 3. Backup and restore (3.1 to 3.4) | [backup-dr.md](backup-dr.md) (sections 5, 6, 7, 8) |
| 4. Incident playbooks | [runbook-incidents.md](runbook-incidents.md#1-playbooks) |
| 5. Admin API | [runbook-health-alerts.md](runbook-health-alerts.md#4-admin-api) |

## Conventions

- Routes are written as the client sees them, with the `/api` prefix; `/healthz`, `/readyz`, `/metrics` and `/mcp` are served without it.
- Admin routes need an admin JWT; personal access tokens are refused with 403, and every admin call is audited.
- Environment variables are `ROR_<UPPER>` ([settings reference](../reference/settings.md)); chart keys are in the [Helm values reference](../reference/helm-values.md).
