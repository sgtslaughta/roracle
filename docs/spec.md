# rOracle — Product & System Specification

Status: agreed 2026-11-15 (design interview rounds 1–9, prototype v15).
Name: **rOracle** in prose/UI; `roracle` in code, packages, hosts, URIs.
Decisions with lasting consequences are recorded in [`docs/adr/`](adr/).
Clickable prototype: https://claude.ai/artifact/23jPeygSNRunuQBE6qGXHt

## 1. Purpose

Enterprise RAG-as-a-service for academic/research papers and documentation, deployed on an
**air-gapped** network. Researchers search, read, chat with, annotate and write papers; agents do
the same through a hosted MCP server, always with their user's permissions.

Environment facts: robust package and container-image mirrors; OpenAI-spec inference endpoint with
many chat models, embedding models (EmbeddingGemma-300m and/or e5-mistral-7b-instruct), a
reranker, and the granite-docling model; Keycloak available; GitLab CI available.

Scale target: ~500 concurrent users, ~1M documents (~50M chunks), ~10k documents/day ingest.
Latency targets (p95): search < 500 ms; chat time-to-first-token < 2 s; upload → searchable
< 2 min for a 20-page PDF.

## 2. Architecture (ADR-0001, 0003, 0009)

| Component | Role |
|---|---|
| Postgres (CloudNativePG) | Source of truth: users, documents, versions, ACLs, tags, votes, collections, chats, notes, proposals, drafts, connectors, settings, audit log, job queue |
| MinIO (S3 API) | Raw files, derived PDFs, evidence packages; content-addressed by SHA-256 |
| Elasticsearch (ECK, Basic) | Chunks, vectors, notes, evidence metadata. Rebuildable from Postgres + MinIO |
| `backend` image | One Python codebase, three entrypoints: `api` (FastAPI: REST, SSE, MCP at `/mcp`), `worker` (jobs), `migrate` (Alembic) |
| `web` image | nginx serving the React SPA |
| Sidecar services | Docling, GROBID, Gotenberg, ClamAV, `latex-builder`; all from internal registry |
| Valkey (optional) | Cache / rate limits only, behind a flag |

## 3. Identity & access (ADR-0004, 0002)

- User mTLS cert → org proxy → Keycloak X.509 authenticator → JWT (with `groups`). API and MCP accept
  **only** Keycloak JWTs or PATs. Trusted headers never accepted directly.
- **PATs**: per-user, scopes `read` / `write`, expiring, revocable; admins can disable PATs globally.
- **Org units** = Keycloak group tree. Each document has one owning org unit (default: uploader's
  primary group), independent of visibility.
- **Visibility**: `private` | `shared` (named users and/or groups) | `org_unit` (owning unit and
  children) | `everyone` (default for uploads; connectors set their own default).
- Owner or admin may edit, version, share, delete; everyone else read-only. Admins transfer ownership;
  disabled Keycloak users' documents appear as "orphaned" for bulk transfer.
- Every Elastic query gets an ACL filter built by the API. The API is the only path to the index.
- Audit log: every search, retrieval, download, handoff load, approval.

## 4. Ingestion (ADR-0005, 0006, 0008)

Sources: user upload (≤ 500 MB, admin-configurable), connectors (S3, filesystem mount, HTTP crawl;
pluggable interface, schedules, run logs).

Pipeline stages (each a job; user sees them in **My uploads**: Scanned → Parsed → Chunked → Embedded → Searchable):
1. **Scan** with ClamAV (can be turned off when there is no signature mirror).
2. **Exact dedup**: SHA-256. Same bytes → store blob once (refcount), reuse chunks. Reveal the match
   **only** if the uploader can already see a document with that blob ("You already have access to
   this; add anyway?"). Never leak the existence of documents the user cannot see.
3. **Convert** office formats to PDF (Gotenberg). **Parse** with Docling (layout, OCR, tables,
   per-item page + bbox) and **GROBID** (title, authors, DOI, venue, references).
4. **Summarise** document with an LLM (1–2 sentences + abstract-level summary).
5. **Near-dup check**: MinHash of text + summary embedding against documents the user can see.
   Above threshold (admin-tunable) prompt: "Very close to X. Is this the same document?" →
   new version of X / separate document / cancel. Connector ingests go to an admin review queue.
6. **Chunk** with Docling `HybridChunker` (structure-aware, ~400–500 tokens, sized with the embedding
   model's tokenizer, no overlap).
7. **Embed** each chunk with context prefix (title, section path, document summary). Record the
   embedding model name on every chunk.
8. **Index** into Elastic (index per embedding model behind an alias; switching model = background
   re-embed into new index, alias flip).
9. **Link references**: resolve GROBID references to library documents (DOI exact, then fuzzy
   title + year) → `doc_citations`. Backfill job links dangling references when new documents arrive.

**Versioning**: search/chat use the latest version by default; older versions viewable and searchable
on request; visibility belongs to the document. Soft delete → restorable 30 days (configurable) → purge;
blobs purged when refcount reaches zero; citations to purged documents show "source removed".

## 5. Retrieval & chat (ADR-0007)

- Hybrid search: BM25 + kNN, fused in the app with RRF, reranked (top 50 → 8–10 for chat).
- Results grouped per document with highlighted passages; document-level summary embedding used for
  broad "papers about X" queries and near-dup only.
- Votes: one per user per document; downvote reasons (outdated, wrong/misleading, low quality,
  superseded + link). Ranking boost = smoothed vote ratio (up+1)/(n+2) mapped to **±15% cap**. "Outdated" reasons
  reset when a new version arrives.
- Facets (from current results only): collection, tags, org unit, author, year, file type, venue.
  Sorts: relevance, newest, most useful (`votes` sort key = net up−down), title. Cursor pagination (`search_after` + PIT).
- Tags: shared, normalised (lowercase, hyphens), autocomplete; any reader adds, owner/admin removes,
  admins merge synonyms. Collections scope search, chat and MCP.
- Chat: streaming SSE, mandatory citations (doc, version, page, chunk), hover excerpt + page crop,
  click opens reader at bbox; LaTeX/tables/code render; pin/exclude sources; "passages the model
  read" drawer; model chosen from admin allowlist; history per user; read-only share links;
  Markdown export; answer thumbs visible to admins only.
- **Reader-aware**: each turn sends viewer state (open docs ≤ 3 tabs, version, page, selection);
  retrieval searches open docs first, then corpus. Users can drop a doc from context.
- Community notes (public annotations) are indexed separately and cited with a distinct badge;
  toggle "include community notes" (default on); low-scoring/reported notes excluded.

## 6. Reader & links

- Office formats converted to PDF at ingest; PDF.js renders; HTML/Markdown sanitized; KaTeX math;
  code highlighting; original always downloadable.
- Tabs: Document / References / Cited by / Notes. In-text reference markers link to library docs.
- **Focus mode** button in the reader toolbar cycles the pane width: current (resizable split) →
  ¾ of the screen → full screen. `Esc` steps back one level; the chosen level is remembered per user.
- Stable links: `/d/{docId}` (latest), `/d/{docId}/v/{n}`, `#p={page}&c={chunkId}`; search state
  in the URL. Links carry no permissions; no access → "Request access from owner" (in-app notice).

## 7. Agentic features (ADR-0010)

**Handoff** — "Continue in agent" on chat, reader, search, collection, draft.
- Snapshot for chats; live reference + membership snapshot for collections.
- `load_handoff` returns manifest: kind, title, goal, LLM brief (editable at creation), doc ids,
  cited passages; full transcript via `get_handoff_part` (paged).
- Link `https://<host>/h/XXXX-XXXX` = resource `roracle://handoff/XXXXXXXX` (≈ 40-bit (8 chars, 31-symbol alphabet), unambiguous
  alphabet). **Not a bearer token**: agent authenticates; undisclosed documents redacted
  ("3 sources withheld").
- Who can load: creator (default), named users/groups, or all authenticated users. Expiry default
  7 days, max 90; revocable; every load audited. `append_to_handoff`, `create_handoff` (agent → human).

**Annotations** — types: comment, finding, correction, question, relation (extends / contradicts /
replicates / supersedes, linking two docs). Anchored by quote + context + page bbox; re-anchored on
new versions, else flagged "may not apply to version N". Visibility private / users+groups /
everyone; reader needs access to both doc and note. Default: agent notes private, human notes
everyone. Threads, votes (smoothed ratio), reports; doc owner may hide; admins remove (audited).

**Agent proposals** — agents only *propose* (`propose_annotation`); user approves / edits / rejects /
bulk-approves in the UI. Approved notes are attributed to the user with "agent-assisted" badge.
Optional auto-approve for private notes. Max 50 pending per session.

**Evidence** — RO-Crate package attached to a finding: code ref (repo + commit), environment
(image digest), inputs, parameters + seed, agent client + model, start/end, outputs. Stance
supports / refutes / inconclusive on an anchored claim. Reproductions tracked (reproduced ×n / failed
×n). ≤ 1 GB stored (ClamAV-scanned), larger by URI + hash. Never executed; notebooks rendered
statically. Refuting evidence is shown, not scored. rOracle stores; it does not run compute.

**MCP tools**: `search`, `get_document`, `get_passages`, `list_collections`, `ask` (optional),
`upload` (write scope), `load_handoff`, `get_handoff_part`, `create_handoff`, `append_to_handoff`,
`list_annotations`, `propose_annotation`, `reply_to_annotation`, `export_bibtex`, `save_draft`,
`validate_draft`, `check_citations`. MCP prompt `write_paper`; resources for templates/profiles.

## 8. Paper writing (ADR-0011)

- Delivered as MCP prompt `write_paper` + resources (templates, style guide, citation rules) +
  `/skills/roracle-writer.zip` SKILL.md bundle generated from the same source.
- Drafts = private documents in `draft` state; `save_draft` creates a version; co-authors via sharing;
  UI renders read-only with notes. No collaborative editor.
- **Writing profiles** (admin YAML, versioned, bound to org unit / doc type): template, required
  sections, limits, citation style, figure rules, required metadata, prose style guidance. Default
  set: dissertation, journal article, conference paper, technical report, grant proposal, literature
  review, preregistration. Checks: structure, notation-before-use, theorem/proof structure,
  statistics reporting, reproducibility & data availability, ethics, limitations, alt text,
  cross-refs, acronyms, SI units, library overlap, citation support, AI disclosure, build.
  Deterministic checks may block; LLM style checks warn; admins choose per rule.
- `check_citations`: every citation resolves to a library doc **and** the cited passage supports the
  sentence; unresolved/unsupported citations block submission until fixed or acknowledged.
- Build: Markdown → pandoc → HTML (KaTeX) → Gotenberg PDF; LaTeX via in-cluster `latex-builder`
  (TeX Live, no shell-escape, no network, 120 s / 2 GB) behind an interface allowing a GitLab-runner
  backend.
- Submit → optional org-unit review → normal ingest → chosen visibility. AI-assistance disclosure
  required and displayed.

## 9. Admin

Users/groups view (read-only from Keycloak); connectors CRUD + schedules + logs; ingest/failed-job
dashboard + retry; near-dup review queue; model allowlist + defaults; embedding model switch +
re-embed; retrieval tuning (top-k, rerank depth, dup threshold); tag merge; writing profiles;
quotas, file-size limit, rate limits, retention; PAT toggle; audit log viewer; storage/usage stats;
health.

## 10. Notifications

In-app only. Users: their uploads (status, failures, near-dup decisions), access requests, agent
proposals, replies, notes on their docs, agent-shared handoffs. Admins: ops notices.

## 11. UI (ADR-0012)

React + Vite + TypeScript, shadcn/ui (Radix + Tailwind), Lucide icons, TanStack Query/Router;
static build served by nginx. Layout: top bar (global search, ⌘K, theme toggle, notifications),
collapsible sidebar (Library, Ask, My uploads, Agent proposals, Drafts, Collections, Recent chats),
workspace, resizable reader pane. Keyboard: ⌘K, `/`, `j`/`k`, Enter, Esc. Signature element:
sidenote citation margin in chat.

Design tokens (light = *Peach skyline*, dark = *Wildflower meadow* on warm slate):

| Token | Light | Dark |
|---|---|---|
| bg / surface / chrome / tray | `#FFFFFF` / `#FFFFFF` / `#FFF9F3` / `#EAF0F6` | `#343A42` / `#434A54` / `#2A2F36` / `#2D3239` |
| ink / muted / border | `#1F2E3D` / `#5A6B7D` / `#DCE5EE` | `#F4F2EA` / `#C9C6B8` / `#535B67` |
| primary (actions) | `#496580` (fg white) | `#FDB813` (fg `#26281C`) |
| highlight (cited passages) | `#BADDFF` / ink `#1C3550` | `#3F6A80` / ink `#F4FBFF` |
| accent / good / bad | peach `#FFDBBB`, `#1F7A6B`, `#C0563B` | sky `#82C8E5`, lime `#7CFC00`, `#FFB4A0` |

Fonts: **Newsreader** (reading, headings) + **Manrope** (UI), STIX Two for math; self-hosted.
Depth: chrome < bg < raised cards (top highlight + layered shadow) ; recessed wells; lists = sunken
tray holding raised cards. Texture: marching-squares contour lines (top-right of workspace,
bottom-left of sidebar), colour from `--contour` token, generated at build time with a fixed seed.
Never near-black in dark mode. Logo: `frontend/public/logo.png` (+ favicon, touch icon).

## 12. Operations (ADR-0013)

Helm chart; Kubernetes with ECK + CloudNativePG + MinIO; ≥ 3 replicas for stateful HA.
OpenTelemetry → Prometheus/Grafana/Loki (bundled, optional). Backups: Postgres PITR RPO ≤ 5 min,
Elastic snapshots (RPO ≤ 24 h, rebuildable), MinIO replication; RTO ≤ 4 h. STIG-hardened images.
Docker Compose dev stack (Keycloak test realm, Postgres, Elastic, MinIO, mock OpenAI server, no
mTLS). Monorepo; GitLab CI must pass before merge; Conventional Commits; one SemVer for the product
(`VERSION` file).

## 13. Roadmap (one implementation plan per line, each shippable on its own)

| # | Plan | Delivers |
|---|---|---|
| 1 | Foundation | Repo, CI, settings, DB + migrations, Keycloak JWT auth, blob store, upload + exact dedup, job queue + worker, My-uploads API |
| 2 | Ingestion | ClamAV, Gotenberg, Docling, GROBID, summary, near-dup prompt, chunk, embed, index, reference linking |
| 3 | Search & access | Elastic hybrid search, ACL filters, facets, votes, tags, collections, versions, links |
| 4 | Chat | RAG with citations, reader-aware context, SSE, chat history/sharing/export |
| 5 | Web shell & library | React/shadcn app, tokens/fonts/depth/contours, auth, library search, reader (PDF.js, focus mode) |
| 6 | Web chat & uploads | Ask view with sidenotes, My uploads, near-dup prompt UI |
| 7 | MCP & handoff | MCP server, PATs, handoff create/load/redact, agent write-back |
| 8 | Notes & evidence | Annotations, proposals inbox, RO-Crate evidence, community notes in retrieval |
| 9 | Writing | Profiles, drafts, validate/check_citations, pandoc + latex-builder, submit/review |
| 10 | Connectors & admin | S3/fs/HTTP connectors, admin section, notifications |
| 11 | Production | Helm, HA, observability, backups, hardening, load tests against latency targets |
| 12 | Documentation | Comprehensive pass after all features: user guide, admin guide, API + MCP reference, architecture overview, enterprise docs audit, developer guide; every doc verified against code |
| 14 | Infrastructure as code | Terraform onto existing Kubernetes wrapping the Helm charts; placeholder-gated air-gap mirrors; sizing profiles; kind apply rehearsal in CI-able script |

Plan 14 runs after 13 and before 12 (Documentation), which stays last so it can document the IaC.
