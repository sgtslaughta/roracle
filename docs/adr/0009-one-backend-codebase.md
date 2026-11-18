# ADR-0009: One Python backend image, three entrypoints

Status: Accepted (2026-11-15)

## Decision

`backend/` is one Python package (`roracle`) built into one image, started as `api` (FastAPI:
REST, SSE chat, MCP mounted at `/mcp`), `worker` (job loop) or `migrate` (Alembic upgrade).
The `web` image is nginx with the built SPA. Docling, GROBID, Gotenberg, ClamAV and latex-builder
are off-the-shelf services from the internal registry.

## Consequences

- Two images to build, harden and scan. API and worker scale independently via replicas.

Update (2026-11-17): latex-builder is a contract only and is not shipped (see the ADR-0011 update); the other services above are the off-the-shelf ones.
