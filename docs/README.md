# rOracle documentation

rOracle is an air-gapped research-paper RAG service. Pick your audience, or jump to a task below.

## By audience

| Audience | Section |
|---|---|
| Researchers | [user/](user/README.md) |
| rOracle admins | [admin/](admin/README.md) |
| Operators / SRE | [enterprise/](enterprise/README.md) (ops), [reference/](reference/README.md) |
| Security / compliance | [security architecture](enterprise/security.md), [audit events](reference/audit-events.md), [decisions/](decisions/README.md) |
| Integrators / agent developers | [reference/](reference/README.md), [MCP guide](reference/mcp.md) |
| Developers | [developer/](developer/README.md), [architecture/](architecture/README.md), [adr/](adr/README.md) |

Terms are defined in the [glossary](glossary.md).

## Start here by task

- Install: [deployment guide](enterprise/deployment-guide.md), [Terraform](enterprise/deployment-terraform.md), [kind rehearsal](enterprise/deployment-kind-rehearsal.md)
- First login: [Keycloak setup](enterprise/keycloak-setup.md) (operator side), [getting started](user/getting-started.md)
- Upload a paper: [user guide](user/README.md)
- Integrate an agent: [agent integration how-to](enterprise/agent-integration.md), [MCP guide](reference/mcp.md), [MCP tools](reference/mcp-tools.md), [HTTP API guide](reference/api.md)
- Upgrade: [upgrades](enterprise/upgrades.md)
- Handle an incident: [operations runbook](enterprise/operations-runbook.md), [incidents](enterprise/runbook-incidents.md)
- Restore from backup: [backup and DR](enterprise/backup-dr.md)

## Sections

Later tasks append a link line under the matching heading when they add a page.

### User

- [User guide index](user/README.md)
- [Getting started](user/getting-started.md): sign-in, layout, theme, keyboard shortcuts
- [Library and search](user/library-search.md): queries, filters, sorting, votes
- [Reading a paper](user/reader.md): focus mode, references, cited-by, identifiers
- [Notes and annotations](user/notes-annotations.md): note kinds, anchoring, community notes
- [Sensitivity labels](user/labels.md): what each gate means, changing a label
- [Collections and sharing](user/collections-sharing.md): collections, visibility, sharing
- [Uploads](user/uploads.md): adding papers, duplicates, near-duplicates, titles
- [Chat](user/chat.md): questions, reader-aware context, citations, excludes and pins, sharing
- [Writing drafts](user/writing-drafts.md): profiles, checks, PDF build, AI disclosure, review, co-authors
- [Handoffs, inbox and notifications](user/handoffs-inbox.md): handoffs, agent proposals, notifications
- [Agents and tokens](user/agents-and-tokens.md): access tokens and connecting an MCP client

### Admin

- [Admin guide index](admin/README.md)
- [Users and groups](admin/users-groups.md): Keycloak, admin role, org units, directory, revoking tokens
- [Sensitivity labels](admin/labels.md): label set, gates, defaults, lowering, tags and drift repair
- [Connectors](admin/connectors.md): S3, folder and website sources, schedules, credentials, runs
- [Quotas and limits](admin/quotas-limits.md): upload size, per-user quota, rate limit, connector caps
- [Audit log](admin/audit.md): viewer, recorded events, export, retention
- [Evaluation](admin/eval.md): gold sets, runs, trend, compare gates, CI job
- [Index maintenance](admin/index-maintenance.md): re-embed, rebuild, shadow indexes, reparse references, jobs
- [Runtime settings](admin/runtime-settings.md): settings an admin can change at runtime, precedence, audit
- [Writing profiles](admin/writing-profiles.md): YAML schema and validation

### Reference

- [Reference index](reference/README.md)
- [HTTP API guide](reference/api.md): auth, pagination, rate limits, SSE, uploads, curl examples
- [Errors](reference/errors.md): error bodies and status codes
- [MCP guide](reference/mcp.md): endpoint, auth, scopes, label gates, handoffs, proposals, limits, audit, errors
- [Metrics](reference/metrics.md): every Prometheus series
- [Jobs](reference/jobs.md): job kinds, payloads, retries, idempotency
- [Audit events](reference/audit-events.md): every audit action, actor, target, fields
- [openapi.json](reference/openapi.json), [settings.md](reference/settings.md), [cli.md](reference/cli.md), [mcp-tools.md](reference/mcp-tools.md), [helm-values.md](reference/helm-values.md) (generated)

### Architecture

- [Architecture index](architecture/README.md)
- [Architecture overview](architecture/overview.md): context, containers, components
- [Ingest data flow](architecture/ingest.md): upload and connector stages, one sequence diagram each
- [Retrieval and chat data flow](architecture/retrieval-chat.md): search, chat modes, citations, `exclude_llm`
- [Access control](architecture/access-control.md): identity, visibility, ACL filter, R19 re-check, label gates
- [Jobs and queue](architecture/jobs-queue.md): Postgres queue, leases, retries, job kinds, drain
- [Search index lifecycle](architecture/search-index-lifecycle.md): aliases, re-embed, rebuild, cache, ES 8 to 9
- [Storage](architecture/storage.md): object layout, blob GC, tmp lifecycle, Ceph or S3

### Enterprise (operations and security)

- [Enterprise index](enterprise/README.md)
- [Deployment guide](enterprise/deployment-guide.md): Helm chart 0.2.0 install, prerequisites, values, NetworkPolicy, TLS
- [Deploying with Terraform](enterprise/deployment-terraform.md): modules, tfvars profiles, placeholder gate, `protect_data`, Rook-Ceph or external S3
- [Kind rehearsal](enterprise/deployment-kind-rehearsal.md): local rehearsal scripts and decommissioning the Ceph object store
- [Upgrades](enterprise/upgrades.md): upgrade order, chart 0.1 to 0.2.0, Elasticsearch 8 to 9, migrations, re-embed
- [Operations runbook](enterprise/operations-runbook.md): index of the runbooks
- [Health, alerts and admin API](enterprise/runbook-health-alerts.md): probes, signals, suggested alerts, admin API
- [Routine procedures](enterprise/runbook-routine.md): scaling, retries, re-embed, maintenance, labels, evaluation
- [Incidents and job triage](enterprise/runbook-incidents.md): playbooks, leases, drain
- [Backup and DR](enterprise/backup-dr.md): CNPG backup and PITR, object lock, snapshot or rebuild, RPO and RTO, drill
- [Observability](enterprise/observability.md): metrics, dashboard, Prometheus, Loki, OpenTelemetry, alert rules
- [Capacity planning](enterprise/capacity-planning.md): Elasticsearch with BBQ, Postgres, connections, Ceph, inference
- [Air-gap mirror manifest](enterprise/airgap-mirror-manifest.md): every image, chart, package, model and dataset
- [Security architecture](enterprise/security.md): index of the security pages
- [Threat model](enterprise/security/threat-model.md): STRIDE per boundary, untrusted content, prompt injection, connectors
- [Security: access control](enterprise/security/access-control.md): authN, PATs, authZ, labels, guarantees and non-guarantees
- [Security: audit](enterprise/security/audit.md): what the audit log guarantees and misses
- [Security: secrets and TLS](enterprise/security/secrets-tls.md): Terraform secrets, PAT hashing, TLS rotation
- [Security: network](enterprise/security/network.md): NetworkPolicies, web headers, pod baseline
- [Security: data retention](enterprise/security/data-retention.md): deletion cascade, blob GC, tmp, handoff expiry
- [Security: known limits](enterprise/security/known-limits.md): behaviours reviewers should know

### Developer

- [Developer index](developer/README.md)
- [Repository layout](developer/repo-layout.md): backend modules, frontend structure, deploy, scripts
- [Local development](developer/local-dev.md): dev stack, Keycloak dev realm, inference box, image builds
- [Testing](developer/testing.md): unit and integration split, integration lock, ES 9.5.6, frontend tests, Helm checks
- [Eval harness](developer/eval-harness.md): `roracle eval`, fixture, CI job
- [Perf suite](developer/perf-suite.md): `roracle perf`, scratch-only guards, budgets, CI job
- [Conventions](developer/conventions.md): semantic commits, single SemVer, 500-line rule, docs rules, ADRs
- [Subagent-driven development](developer/sdd-workflow.md): optional plan and writer/reviewer workflow

### Decisions

- [Decisions index](decisions/README.md)
- [Decision matrix](decisions/decision-matrix.md): options, weighted ratings, status and revisit triggers for 20 areas; ADR gaps
- [ADRs](adr/README.md)

### Other

- [Specification](spec.md)
- [Performance harness](perf/README.md)
- [Evaluation](eval/README.md)

## Conventions

- Generated: everything in `reference/` except hand-written pages. Regenerate with `cd backend && uv run python ../scripts/docs/gen_reference.py`.
- Hand-written: all other pages. Facts come from code, not from the spec.
- Check everything with `scripts/docs/check.sh` (links, setting names, routes, CLI, MCP tools, metrics, 500-line cap).
- Names: "rOracle" in prose, `roracle` in code; env vars as `ROR_<UPPER>`; hosts as `example.internal`.
- Diagrams are Mermaid blocks.
