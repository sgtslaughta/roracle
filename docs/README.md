# rOracle documentation

rOracle is an air-gapped research-paper RAG service. Pick your audience, or jump to a task below.

## By audience

| Audience | Section |
|---|---|
| Researchers | [user/](user/README.md) |
| rOracle admins | [admin/](admin/README.md) |
| Operators / SRE | [enterprise/](enterprise/README.md) (ops), [reference/](reference/README.md) |
| Security / compliance | [enterprise/](enterprise/README.md) (security), [decisions/](decisions/README.md) |
| Integrators / agent developers | [reference/](reference/README.md), [agent integration](enterprise/agent-integration.md) |
| Developers | [developer/](developer/README.md), [architecture/](architecture/README.md), [adr/](adr/README.md) |

Terms are defined in the [glossary](glossary.md).

## Start here by task

- Install: [deployment guide](enterprise/deployment-guide.md)
- First login: [Keycloak setup](enterprise/keycloak-setup.md) (operator side), [getting started](user/getting-started.md)
- Upload a paper: [user guide](user/README.md)
- Integrate an agent: [agent integration](enterprise/agent-integration.md), [MCP tools](reference/mcp-tools.md)
- Upgrade: [deployment guide](enterprise/deployment-guide.md)
- Handle an incident: [operations runbook](enterprise/operations-runbook.md)

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
- [openapi.json](reference/openapi.json), [settings.md](reference/settings.md), [cli.md](reference/cli.md), [mcp-tools.md](reference/mcp-tools.md) (generated)

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

### Developer

- [Developer index](developer/README.md)

### Decisions

- [Decisions index](decisions/README.md)
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
