# ADR-0013: Monorepo, GitLab CI, Conventional Commits, single SemVer

Status: Accepted (2026-11-15)

## Decision
- Layout: `backend/` (uv, pytest, ruff), `frontend/` (pnpm, Vitest, Playwright), `deploy/` (compose
  dev stack, Helm chart), `docs/` (spec, ADRs, plans), `scripts/`.
- One product version in `/VERSION` (SemVer), starting at `0.1.0`; tags `vX.Y.Z`.
- Conventional Commits; never commit directly to `main`; GitLab CI (lint, test, build) must pass
  before merge. Dependencies resolve from internal mirrors via CI variables
  (`UV_INDEX_URL`, `NPM_CONFIG_REGISTRY`, image registry prefix).
- Source files stay under ~500 lines.
