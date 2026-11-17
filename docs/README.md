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
- First login: [Keycloak setup](enterprise/keycloak-setup.md) (operator side); user guide is planned
- Upload a paper: [user guide](user/README.md)
- Integrate an agent: [agent integration](enterprise/agent-integration.md), [MCP tools](reference/mcp-tools.md)
- Upgrade: [deployment guide](enterprise/deployment-guide.md)
- Handle an incident: [operations runbook](enterprise/operations-runbook.md)

## Sections

Later tasks append a link line under the matching heading when they add a page.

### User

- [User guide index](user/README.md)

### Admin

- [Admin guide index](admin/README.md)

### Reference

- [Reference index](reference/README.md)
- [openapi.json](reference/openapi.json), [settings.md](reference/settings.md), [cli.md](reference/cli.md), [mcp-tools.md](reference/mcp-tools.md) (generated)

### Architecture

- [Architecture index](architecture/README.md)

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
