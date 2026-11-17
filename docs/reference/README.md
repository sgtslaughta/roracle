# Reference

Reference for integrators and operators. Generated pages are rewritten by `scripts/docs/gen_reference.py`; do not edit them by hand.

## Hand-written

- [HTTP API guide](api.md): authentication, tokens, pagination, rate limits, streaming, uploads, versioning, endpoint map and curl examples
- [Errors](errors.md): error body shapes, status codes and input rejections
- [MCP guide](mcp.md): endpoint, PAT and OAuth auth, scope and tool matrix, label gates, handoffs, proposals, limits, audit and error mapping

## Generated

- [openapi.json](openapi.json): HTTP API schema, the source of truth for request and response shapes
- [settings.md](settings.md): `ROR_*` settings
- [cli.md](cli.md): `roracle` command line
- [mcp-tools.md](mcp-tools.md): MCP tools
- [helm-values.md](helm-values.md): Helm chart values and defaults

## Planned

Jobs, metrics, audit events.
