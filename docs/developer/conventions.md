# Conventions

Rules every change follows. Most are enforced by CI; the rest are review habits.

## Semantic commits

Commit subjects follow Conventional Commits: `type(scope): summary`, imperative, no trailing full stop. Types seen in this repository: `feat`, `fix`, `docs`, `build`, `ci`, `chore`, `test`, `refactor`, `perf`, `style`. Scope names the area (`docs(security)`, `fix(ingest)`). Documentation-only changes use `docs`; docs tooling uses `build(docs)` or `ci(docs)`. Do not commit directly to `main`: work on a branch, open a merge request, and let the pipeline pass first (ADR-0013 in the [ADR index](../adr/README.md)).

## One version, SemVer

`VERSION` at the repository root is the single product version (SemVer, `X.Y.Z`; git tags are `vX.Y.Z`). `scripts/check-version.sh` fails unless all five agree:

1. `VERSION`
2. `version` in `deploy/helm/roracle/Chart.yaml`
3. `appVersion` in `deploy/helm/roracle/Chart.yaml`
4. `version` in `backend/pyproject.toml`
5. `version` in `frontend/package.json`

Bump them together in one commit. CI runs the script in the `helm` job (and it runs when `VERSION`, `deploy/**` or the script changes). Container images are tagged with the commit SHA, and on a tag also with the tag and the contents of `VERSION`. At run time the backend reports `ROR_VERSION` if set, else the `VERSION` file, else the installed package metadata (`roracle/__init__.py`).

Docs-only changes and scripts that are not shipped do not bump it.

## File size: 500 lines

Keep every file under about 500 lines. The docs CI enforces it for Markdown (`scripts/docs/check.sh` fails on any `.md` over 500 lines under `docs/`, `deploy/` and the three READMEs). For code it is a standing rule: split by topic instead of growing a file. The backend shows the pattern: `models.py` has `models_labels.py`, `models_notes.py`, `models_writing.py` and `models_eval.py` beside it, and `search_index.py` has `search_index_acl.py` and `search_index_versions.py`.

## Lint and format

- Backend: `ruff check .` and `ruff format --check .` from `backend/`, line length 110 (`pyproject.toml`).
- Frontend: `tsc --noEmit` in strict mode (`pnpm lint`); no eslint is configured.
- Docs: `scripts/docs/check.sh` (see below).

Run the matching checks before every commit; the pipeline runs the same ones ([testing](testing.md)).

## Air-gap rules

rOracle runs with no internet. Code and docs must not fetch from the internet at run time: no CDN fonts or scripts (the web build is checked by `pnpm check:dist`, the docs site by `scripts/docs/check_site.py`). CI installs from mirrors set by `REGISTRY_MIRROR`, `UV_INDEX_URL` and `NPM_CONFIG_REGISTRY`. A new image, chart, package or model goes on the [air-gap mirror manifest](../enterprise/airgap-mirror-manifest.md).

## Writing and maintaining docs

- Truth is the code. Name settings, routes, roles, limits and metrics only after reading them in the code. Do not copy from the spec or from plans.
- Names: "rOracle" in prose, `roracle` in code, hosts and packages. Environment variables are written `ROR_<UPPER>`. Hosts are `example.internal`; never real hostnames or secrets.
- Routes are written as clients see them, with the `/api` prefix (`/api/documents/{doc_id}`). `docs/reference/openapi.json` omits the prefix because the web proxy and ingress add it. `/mcp`, `/metrics`, `/healthz` and `/readyz` are written as they are.
- Generated references (`docs/reference/openapi.json`, `settings.md`, `cli.md`, `mcp-tools.md`, `helm-values.md`) are rewritten by `cd backend && uv run python ../scripts/docs/gen_reference.py`. Never edit them by hand; CI fails on drift. Link to them instead of copying.
- Link terms to the [glossary](../glossary.md) instead of redefining them.
- Diagrams are Mermaid fenced blocks, not images. Use `flowchart` with subgraphs rather than C4 syntax.
- Every page is linked from its section `README.md` and from the [documentation index](../README.md). Links from `docs/` to files outside `docs/` are dead in the site, so write those paths in backticks.
- A mention the code check cannot find (for example a removed setting named in an upgrade note) goes in `scripts/docs/refs-allow.txt` with a reason.
- `scripts/docs/check.sh` runs: generated-reference drift, links and anchors, setting names, routes, CLI commands, MCP tool names, metrics, Terraform variables and Helm keys, Markdown lint, the 500-line cap, the script tests, and the site build with its air-gap guard. It is the CI job `docs`.

## ADRs

Architecture decisions live in `docs/adr/`, one file per decision, named `NNNN-short-title.md` with the next free number. Write one when a choice shapes the architecture, is costly to reverse, or has rejected alternatives someone will ask about again.

Format (see any existing ADR): a title `# ADR-NNNN: <decision>`, a `Status: Accepted (YYYY-MM-DD)` line, then `## Context`, `## Decision` and `## Consequences`. Add an `## Outcomes` or addendum section when later work changes the picture, and say in the status line when an ADR amends or supersedes another (ADR-0015 amends ADR-0005 and ADR-0007). Do not rewrite an accepted decision in place: amend it. Add the new ADR to the table in `docs/adr/README.md`. Technology comparisons with weights and ratings go in the [decisions section](../decisions/README.md), which cites ADRs rather than replacing them.
