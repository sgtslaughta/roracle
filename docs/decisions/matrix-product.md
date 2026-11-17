# Decision matrix: access, security and product

Areas 10-12, 15 and 16 of the [decision matrix](decision-matrix.md). The method, weights and status words are defined
there. **Every rating is judgement** (1 worst, 5 best; for ops cost, 5 is cheapest to run). Rating cell: `A` accuracy
(here: correctness of the behaviour) ×3, `S` security ×3, `G` air-gap fit ×3, `P` performance/scale ×2, `L` licence ×2,
`O` ops cost ×1. The maximum weighted total is 70. What each control does and does not guarantee is in
[security: access control](../enterprise/security/access-control.md) and
[known limits](../enterprise/security/known-limits.md). This page does not restate those guarantees. Licence notes are general knowledge, not something this repository
checks: confirm them with your legal team.

## 10. ACL enforcement

Every Elasticsearch query carries an ACL filter that the API builds. Hits are only candidates: Postgres re-checks
visibility, version stage and the label gates for each one (R19; older versions of a visible document are allowed).
See [ADR-0002](../adr/0002-elastic-basic-app-acl.md), [access control](../architecture/access-control.md) and the
[glossary](../glossary.md#access).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| App-built ES filter + Postgres R19 re-check | A5 S4 G5 P4 L5 O4 | 64 | **Chosen** | One query builder owns access control and has "no leak" tests. SciFact runs recorded 0 ACL violations over 1,200 query-principal pairs ([eval README](../eval/README.md)). Filtered `/search` p95 334 ms at 5 rps per process ([perf report](../perf/report-2026-11-17.md)). S4, not S5: the reranker sees candidate text before R19 runs, including text revoked within the `acl_sync` window ([known limits](../enterprise/security/known-limits.md#retrieval-and-label-timing)). | [0002](../adr/0002-elastic-basic-app-acl.md) | Decided |
| Elasticsearch document-level security | A5 S4 G4 P4 L1 O3 | 52 | Rejected | Needs a paid Elastic tier (ADR-0002) and a licence key in the air gap. It needs per-user role mapping, and R19 plus the label gates would still be required. | [0002](../adr/0002-elastic-basic-app-acl.md) | Revisit only together with area 2 (OpenSearch includes DLS) |
| Per-tenant or per-group indices | A4 S3 G5 P2 L5 O1 | 51 | Rejected | Shares and org-unit visibility cut across tenants, so documents would be duplicated across indices. Index count grows with the number of groups. | [0002](../adr/0002-elastic-basic-app-acl.md) (alternative not discussed in the ADR) | Decided |

## 11. Labels model

Admin-ordered sensitivity labels with four monotonic gates: `exclude_llm`, `block_agents`, `block_export` and
`reason_on_lower`. Labels only restrict: they are an extra AND on top of visibility, never OR-ed into it
([ADR-0014](../adr/0014-sensitivity-labels.md), [glossary](../glossary.md#labels)).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Restrict-only gates on ranked labels | A4 S4 G5 P5 L5 O4 | 63 | **Chosen** | Gates resolve at query time from the per-request policy, so gate edits need no reindex (ADR-0014 R6). `block_agents` hides the document from PAT callers, admins included, and handoffs drop such documents for every loader. Limits: OAuth callers on `/mcp` are not PAT-gated, and `block_export` is a deterrent ([known limits](../enterprise/security/known-limits.md#labels-and-handoffs)). | [0014](../adr/0014-sensitivity-labels.md) | Decided |
| Clearance-based model (user clearance against label level) | A4 S3 G5 P3 L5 O2 | 54 | Rejected | It is a second access-control system next to visibility and shares. It implies guarantees that rOracle does not make: labels are handling markers, not classification (ADR-0014 R14). Clearance changes would touch every query path. | [0014](../adr/0014-sensitivity-labels.md) | Decided |

`block_export` sub-decision: humans may still read inline in the reader, and each inline read by a non-owner is audited
as `label.view`. Only download and export are blocked: a deterrent, not DRM. User decisions recorded in ledger
`.worktrees/labels/.superpowers/sdd/labels/progress.md:7,9` (2026-11-16), now in ADR-0014 R9.

## 12. Identity

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| mTLS at the org proxy → Keycloak X.509 authenticator → JWT; scoped PATs for agents | A5 S4 G5 P5 L5 O3 | 65 | **Chosen** | One validation path for web, API and MCP. PATs are stored as SHA-256 hashes, with `read` or `write` scope and a required expiry (ADR-0004). S4 because a write-scope PAT acts with its owner's full rights, and group removal reaches a PAT only at the owner's next JWT request ([known limits](../enterprise/security/known-limits.md#agents-and-the-mcp-mount)). | [0004](../adr/0004-identity.md) | Decided |
| Keycloak OAuth tokens for agents (no PATs) | A5 S3 G5 P5 L5 O3 | 62 | Not adopted | The `block_agents` and `exclude_llm` agent gates key on the PAT credential. An OAuth agent would see what its user sees in the web app ([known limits](../enterprise/security/known-limits.md#agents-and-the-mcp-mount)). | [0004](../adr/0004-identity.md) | Decided |
| Trusted identity headers from the proxy | A5 S1 G5 P5 L5 O5 | 58 | Rejected | Anyone who reaches the service can spoof the header. rOracle never accepts trusted identity headers (ADR-0004). | [0004](../adr/0004-identity.md) | Decided |

## 15. Agent loop, handoffs and paper writing

Handoffs have no live mode. Contents are frozen at creation, and who may load a handoff and what is redacted are
re-evaluated on every load (`backend/src/roracle/handoff.py:1`; ruling in the P12 ledger
`.superpowers/sdd/2026-11-17-p12-docs/progress.md:45` of the docs worktree, 2026-11-17, phase date). Agents never publish: they
propose, and a human approves ([ADR-0010](../adr/0010-agent-loop.md)).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Frozen handoffs, re-evaluated on load; propose-then-approve | A4 S4 G5 P5 L5 O4 | 63 | **Chosen** | Accountability stays with people. Expiry defaults to 7 days, at most 90 (`backend/src/roracle/handoff.py:118`). S4: the brief, goal, notes and user messages in a handoff are free text and are not label-gated ([known limits](../enterprise/security/known-limits.md#labels-and-handoffs)). | [0010](../adr/0010-agent-loop.md), [0014](../adr/0014-sensitivity-labels.md) R10 | Decided. The frozen-contents rule has no ADR: ADR: none, see ledger `.superpowers/sdd/2026-11-17-p12-docs/progress.md:45` (2026-11-17) |
| Live handoffs (contents resolved at load time) | A5 S3 G5 P4 L5 O4 | 61 | Rejected | Fresher, but a handoff could silently grow beyond what its creator reviewed. | [0010](../adr/0010-agent-loop.md) (alternative not discussed in the ADR); ruling: ADR: none, see ledger `.superpowers/sdd/2026-11-17-p12-docs/progress.md:45` (2026-11-17, phase date) | Decided |
| Agents write notes directly | A3 S2 G5 P5 L5 O5 | 55 | Rejected | Agent output could enter the corpus and retrieval without a human (ADR-0010). | [0010](../adr/0010-agent-loop.md) | Decided |

Paper writing ([ADR-0011](../adr/0011-paper-writing.md)): profiles, citation checks and sandboxed builds.

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Sandboxed `latex-builder` behind a `Builder` interface | A4 S5 G3 P4 L5 O2 | 56 | **Chosen** (interface) | No shell-escape, no network, 120 s and 2 GB limits (ADR-0011). The TeX Live image (about 5 GB) must be mirrored. **Not shipped:** `format: latex` builds return 501 (`backend/src/roracle/writing/build.py:61`, [latex builder](../enterprise/latex-builder.md)). | [0011](../adr/0011-paper-writing.md) | Provisional. Revisit when a site needs LaTeX journal output |
| Gotenberg PDF build of drafts (separate `draftgotenberg` parser) | A3 S4 G5 P4 L5 O4 | 58 | Shipped path | Reuses an image already mirrored. No LaTeX templates. | ADR: none, see ledger `.worktrees/p11b-hardening/.superpowers/sdd/p11b-hardening/progress.md:43` (2026-11-15, phase date) | Provisional: the shipped draft-PDF path (`ROR_DRAFT_GOTENBERG_URL`). Revisit when a LaTeX builder ships |
| GitLab-runner build backend | A4 S3 G4 P3 L4 O3 | 50 | Deferred | Named in ADR-0011 as a possible later backend. Shared runners weaken isolation. | [0011](../adr/0011-paper-writing.md) | Revisit with row 1 |

The shipped Gotenberg path ranks 2 points above the LaTeX interface because the LaTeX builder is unshipped and heavy to
mirror. Both stay, for different outputs.

## 16. Frontend

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| React + Vite + TypeScript, shadcn/ui, TanStack Query and Router, PDF.js, KaTeX | A4 S4 G5 P4 L5 O4 | 61 | **Chosen** | Air-gapped builds need only the npm mirror. Fonts and icons are bundled, with no runtime CDN (ADR-0012). The shadcn components are vendored in the repo. | [0012](../adr/0012-frontend.md) | Decided |
| Angular + Angular Material | A4 S4 G5 P4 L5 O3 | 60 | Not adopted | Comparable. The design tokens and vendored components already target shadcn. | [0012](../adr/0012-frontend.md) (alternative not discussed in the ADR) | Decided |
| Server-rendered pages (templates + HTMX) | A3 S4 G5 P4 L5 O4 | 58 | Not adopted | Harder for the PDF reader, the SSE chat and the annotation anchoring. | [0012](../adr/0012-frontend.md) (alternative not discussed in the ADR) | Decided |

The criteria barely tell these apart (a spread of 3 points). The frontend choice rests on the ADR, not on this table.
