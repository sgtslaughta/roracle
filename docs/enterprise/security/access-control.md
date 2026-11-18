# Security: authentication, authorization and labels

Part of the [security architecture](../security.md). The data flow behind these rules is in
[architecture: access control](../../architecture/access-control.md); this page states what is guaranteed and what is not.
Terms: [glossary](../../glossary.md). Decisions: ADR-0002, 0004, 0014, 0015.

## 2. Authentication

- Users present client certificates to the org proxy; Keycloak's X.509 authenticator maps the cert
  to a user and issues an RS256 JWT (<= 5 min access token lifetime recommended).
- rOracle accepts only RS256 access tokens (`typ` Bearer) and validates `iss`, `aud` (`ROR_OIDC_AUDIENCE`), `exp` and `sub`,
  the signature against the Keycloak JWKS, and that `azp` is one of `ROR_OIDC_ALLOWED_CLIENTS`. An unknown `kid` or a
  key set older than 10 minutes triggers a JWKS reload, at most once per 60 s (failed loads count), so key rotation
  works without refetch amplification. If reloads keep failing, cached keys serve for up to 1 hour and then the
  API fails closed (401).
- rOracle services **never** trust identity headers; only tokens.
- PATs (agents): `ror_pat_` + 32 random bytes, shown once, stored only as an unsalted SHA-256 hash (no pepper or
  other server secret is involved), mandatory expiry (default 30 days, max 365), scopes `read` / `write` / `manage` (each includes the ones before it), revocable,
  globally disableable (`ROR_PATS_ENABLED`). Token management requires an interactive JWT (a PAT cannot mint PATs).
  - A PAT is **never admin**, even for an admin owner.
  - `manage` gates sharing and ownership: `PATCH /api/documents/{id}` touching `visibility`, `org_unit`,
    `share_users`, `share_groups` or `coauthors`; `DELETE /api/documents/{id}`; `POST /api/documents/{id}/transfer`;
    `POST /api/drafts/{id}/submit`; `PUT /api/drafts/{id}/coauthors`; `PUT /api/chats/{id}/shares`; document upload (REST `POST /api/documents`, whose default visibility is `everyone`, and MCP `upload`) with any visibility other than `private`; and handoff create or update with an audience other
    than `creator` (REST and MCP). Without it: 403 `insufficient scope: manage`, checked before any document lookup.
    Sessions (JWT) hold it. MCP callers without a PAT never have it. Existing write tokens were backfilled with it by
    migration 0021; new `write` tokens do not get it.
  - Groups = snapshot at creation intersected with the groups stored on the owner's user row. That row is
    rewritten on every request the owner makes with a Keycloak JWT, so access can narrow but never widen after
    minting, and **a group removal reaches the PAT only after the owner's next JWT-authenticated request** (in
    practice the next sign-in or web session). Until then the PAT keeps the old group.
  - Owner sign-in bound: a PAT is rejected (401 "token owner session stale") unless its owner
    signed in via JWT within `ROR_PAT_MAX_IDENTITY_AGE_DAYS` (default 30). This bounds how long a
    disabled/removed Keycloak user keeps PAT access.
  - Admins revoke all of a user's PATs at once: `DELETE /api/admin/users/{id}/pats`.
  - `last_used_at` is updated at most once a minute and never blocks requests.
  - Use of a PAT is audited only partly: see [audit](audit.md#what-is-and-is-not-audited).

## 3. Authorization

- Visibility per document: `private`, `shared` (users/groups), `org_unit` (owning Keycloak group
  path and descendants), `everyone` (all authenticated users). Admins (`roracle-admin` realm role)
  see all; their admin-route calls are audited.
- One function defines visibility in SQL; the Elastic filter mirrors it (ADR-0002). Both have
  contract tests including negative ("must not see") cases.
- **R19 re-check.** Elasticsearch narrows candidates; Postgres re-checks visibility and version stage on
  every result that leaves the API. Non-owner non-admins see only `searchable` versions; older versions of a
  visible document are allowed.
- **No existence leaks**: duplicate detection reveals a match only when the user can already see it;
  search facets/counts are computed from ACL-filtered results; handoff loads redact undisclosed
  documents and say only how many were withheld; links to documents a user cannot see show a
  request-access page with no title or metadata.
- MCP tools run as the token's user; no tool bypasses the ACL path.
- **Co-authors of a published paper** keep read access whatever its visibility. At publish the draft's co-author
  *users* are written to `document_coauthors` (a dedicated grant, not share rows: shares stay inert unless
  `visibility == shared` and are never revived). Co-author *groups* grant nothing after publication. The grant is in
  `visible_documents`/`readable_documents` and in the Elastic filter (`coauthor_users`, kept in sync by
  `acl_sync`). The owner or an admin can drop co-authors with `PATCH /api/documents/{id}` `{coauthors: [ids to keep]}`
  (removal only; 422 for ids that are not current co-authors). `GET /api/documents/{id}` returns `share_users`,
  `share_groups` and `coauthors` to the owner/admin only.
- **Notes (annotations)**: readable only if the note is not `removed` (`hidden` only for its author, the
  document owner and admins), its document passes the document ACL (non-owner non-admins also need a
  `searchable` version), any relation target does too, and the reader is the author/an admin or the note
  is `everyone`, or `shared` with them or one of their groups. Replies inherit their root's visibility.
  Anything unreadable is a 404. Community notes attached to search hits and chat passages are
  `everyone` root notes on the document's latest searchable version only (score >= 0.4, < 3 reports,
  <= 2 per document, <= 6 per response). Notes stored with a chat answer are re-checked per reader on every
  read (fail closed; handoffs show them as unavailable).
- **Agents only propose**: every notes/proposal/evidence write REST route requires a Keycloak JWT
  (PATs get 403 `agents must propose`). PATs reach notes only through MCP `propose_annotation` and
  `reply_to_annotation`, which create pending proposals (default `private`; a requested visibility applies
  only after a human approves). At most 50 proposals per user and client can be pending (429 `too many pending proposals`).
  This is not a blanket "PATs cannot write": routes that depend on `current_principal` rather than
  `require_human` accept a write-scope PAT. In particular **a `manage`-scope PAT can submit its owner's drafts through the
  REST API** (`POST /api/drafts/{id}/submit`; a `write` PAT gets 403 `insufficient scope: manage`); MCP has no submit
  tool. The admin review step still applies where the writing profile requires one.
- **Handoffs are frozen, not live.** Contents (transcript, passages, document ids, brief, goal) are fixed at
  creation. Who may load one (audience: creator, shared users/groups, any authenticated user), its expiry and
  revocation, and what is redacted for the loader are re-evaluated on **every** load. Only the creator can
  create a chat handoff from their own chat.

## 13. Sensitivity labels

> rOracle sensitivity labels are handling markers enforced only inside rOracle. They are not a security classification system, do not implement clearance or need-to-know, and must not be relied on to protect classified, export-controlled or otherwise regulated data. Access is governed solely by visibility and shares; labels can only restrict.

Design: [ADR-0014](../../adr/0014-sensitivity-labels.md). Admins define up to 20 labels ordered by `rank`
(higher = more sensitive; admin page **Labels**, `/api/admin/labels*`). Every document, draft included, has one
label (default: the label flagged `is_default`, seeded as `General`); a note may carry its own label and its
**effective label** is `max(document, note)`; replies use their thread root's. Labels are per document, not
per version. Gates are monotonic: a gate set on a label is set on all higher-ranked labels, enforced on
every admin write (422 `gates must not decrease with rank`). Every label check is an extra AND on top of
visibility; labels never grant access.

### Gates

| Gate | Effect | Applies to |
|---|---|---|
| `block_agents` | Document and its notes behave as **not found** (404, absent from lists, search, facets, counts) | Every PAT principal, admin PATs included; also every handoff loader (human or agent) |
| `exclude_llm` | Content never enters an LLM prompt: chat/`ask` retrieval, community-note passages, citation judges, draft style/validate checks, ingest summary. PATs also get no passage text of it anywhere (metadata and label stay visible, text is replaced by `[withheld: <label>]` or emptied and flagged `withheld`) | Everyone for LLM context; PATs additionally for text |
| `block_export` | 403 `{"detail":"export blocked by label","label":"<name>"}` on download/export routes (below) | Everyone except the document owner (JWT or PAT) and admins on an interactive JWT. Admin PATs are not exempt (a PAT is never admin) |
| `reason_on_lower` | Lowering this label needs a 3-500 character reason | Admins lowering |

**Which callers are gated.** Every agent: PAT callers and every caller on `/mcp`, including an OAuth/Keycloak
access token (`Principal.is_agent`, `via != "jwt"`). On `/mcp` a JWT principal is rewritten to `via="mcp"` and is never
admin, so an admin's token there sees only what a non-admin agent would. The `block_agents` filter and the agent text
withholding apply to all of them; only web-app (REST, `via="jwt"`) users are exempt. The LLM gate (`exclude_llm` in
`ask`, chat and the ingest paths) and the handoff gates apply to every caller.

**Ruling (cross-encoder reranker):** `exclude_llm` text may be scored by the site-hosted cross-encoder reranker (`ROR_RERANK_URL`). It is non-generative and returns only scores, like the embedding model, so it is not an "LLM" for this gate. Keep the endpoint on a trusted network (loopback/mesh). It receives only passages that passed the Elasticsearch ACL filter for the caller, but rerank runs **before** the Postgres re-check, so during the `acl_sync` window after a revocation it can still score text the caller may no longer read (see [known limits](known-limits.md#retrieval-and-label-timing)).

Export routes under `block_export`: `GET /api/documents/{id}/versions/{n}/file` (attachment), the same
`file`/`pdf`/`docling` for non-owner PATs, `GET /api/export/bibtex` and MCP `export_bibtex` (gated documents are
silently omitted), `GET /api/evidence/{id}/file` (effective note label) and the draft PDF build. Each block
is audited `label.export_blocked` (detail `{route}`). `GET /api/chats/{id}/export.md` does not 403: it returns
200 with passage text and answers derived from gated documents replaced by `[withheld: <label>]`. A PAT
reading an `exclude_llm` document's `file`, `pdf` or `docling` gets 403 `withheld by label: <name>` first, and
`evidence.file` 403 `withheld by label`; so the `export blocked by label` body above is not the only 403 a PAT sees. Invisible stays 404, so naming the label leaks nothing.

### Deterrent, not DRM

- **`block_export` is a deterrent, not DRM.** It stops the download and export routes and leaves an audit trail
  for inline reading. It cannot stop copying, screenshots or printing of what a person is allowed to read.
- **The document owner (JWT or PAT) and admins on an interactive JWT are exempt from `block_export`** (the owner holds
  the original and admins can relabel). An admin's PAT is not exempt, because a PAT is never admin. Non-owner PATs are always treated as downloads: they get no inline reader view of a
  `block_export` document.
- Non-owner humans can still **read inline in the reader**; only download/export is blocked. In-app viewing
  transfers the content to the browser. Each inline
  read (`/file`, `/pdf`, `/docling`) by a non-owner non-admin is audited `label.view`, one row per user,
  document and UTC day. Markdown and plain-text documents are served inline as `text/plain` with a
  restrictive CSP sandbox (`GET .../file?inline=true`); PATs never get the inline variant.
- `exclude_llm` binds LLM *context*, not embeddings: chunks are still embedded, and summaries written before
  a label was raised are not deleted. Past chat answers are not redacted for their owner, but replaying an
  old turn into an LLM drops it.
- A raised label gates the document immediately in Postgres; Elasticsearch converges through the `acl_sync`
  job and the Postgres post-filter is authoritative meanwhile (brief over-fetch, never a leak to the caller).
- **Handoff transcripts of chats carry text verbatim, with limited redaction.** Assistant message text derived from
  `block_agents` sources is withheld (`[withheld: unavailable]`, the label name is not revealed) for **every** loader, humans included; text derived
  from `exclude_llm` sources is withheld for PAT loaders. Assistant replies that drew on (cited or retrieved) papers the loader cannot see (ACL, not label) are
  withheld as `[withheld: unavailable]`, and those citations are reduced to `{n, available: false}`
  (`handoff.py` `get_part`); the creator sees them only if they can still see every paper. Passages that came from notes are always dropped from
  handoff manifests. User messages, the handoff **brief, goal and notes are free text** written by people or
  agents and are **not label-gated**: whatever is typed there is stored and shown to every loader in the audience.
  Documents behind `block_agents` are dropped from a handoff for every loader (PAT or human, admins included).
- Drafts are text: a PAT (owner or co-author too) gets 403 `withheld by label` on `GET /api/drafts/{id}`,
  `GET /api/drafts/{id}/versions/{n}`, `GET /api/drafts/{id}/checks`, `POST /api/drafts/{id}/validate`,
  `POST /api/drafts/{id}/check-citations` (also MCP `validate_draft`, `check_citations`) and `POST /api/drafts/{id}/build`
  for an `exclude_llm` document, because their output quotes the draft. The drafts list (metadata and label)
  stays available. Writing is not gated: a PAT may still overwrite such a draft blind with `save_draft` /
  `PUT /api/drafts/{id}/content`.
- Stored per-user state is filtered for PATs too: the chat `viewer` (selection text, tabs) is never returned to a
  PAT, pinned/excluded ids and proposals/notifications (including unread counts) naming documents or notes hidden
  from agents are dropped, and note citations are gated by the note's own label. A PAT may still cause a label
  *raise* (never a lowering) by merging a higher-labelled near-duplicate upload into a document it can see
  (the target takes the upload's label); it is audited as `document.label` with `via: near_dup`.

### Who may change a label

| Target | Raise or keep | Lower |
|---|---|---|
| Document | Owner or admin | Admin only (403 `only admins can lower a label`); reason required when the current label has `reason_on_lower` |
| Note (thread root) | Author or admin | Admin only, same reason rule; never below the document label (422) |
| Upload / connector / draft submit | Upload may choose any label ranking at or above the default (lower: admin human; PATs, admin or not, never); connector docs get the connector label or the default; submit and near-duplicate `new_version` merge only raise (`max`) | n/a |

The two label write routes `PUT /api/documents/{id}/label` and `PUT /api/annotations/{id}/label` are human-only (PAT: 403 `agents must propose`); `/api/admin/labels*` are admin-only and never reachable with a PAT. Agents use MCP `propose_label`; a
human approves it and the **approver's** rights apply (an owner approving a lowering gets 403; an admin
supplies the reason). A label in use by any document, note or connector, or the default, cannot be deleted
(409 `label in use`). Someone else changing your document's or note's label sends you a `label.changed`
notification.

Label audit events: [audit events](../../reference/audit-events.md#labels).

### Elasticsearch projection and backfill

Chunks carry `label_id` (keyword) via `search_index.acl_fields`. Gate sets resolve at query time from the
current labels, so reorder and gate edits need no reindex; only a document's label change needs `acl_sync`.
Migration `0012` enqueues one `acl_sync` job per non-deleted document to backfill the field (see the
[runbook](../runbook-routine.md#sensitivity-labels)). `search_acl.acl_filter` adds `must_not terms label_id` for PAT principals.

## 14. Retrieval-quality surfaces and the LLM gate

All of these are OFF by default except the admin APIs and the quick search mode; none widens what a caller can read
(ADR-0015).

### LLM gate coverage (`exclude_llm` / `block_agents`)

No `exclude_llm` document text reaches a generative model through any path added here once its label is seen (a label raised during a notes job can still have one batch of at most `ROR_CONTEXT_CONCURRENCY` calls in flight; the result is discarded, never cached), and a PAT principal never gets `block_agents`
documents:

| Path | Where the gate sits |
|---|---|
| Contextual chunk notes | the document's label is re-read from Postgres before every batch of note calls; an `exclude_llm` document gets its heading path instead and is never sent; a batch in flight when the label is raised is discarded and not cached (`ingest/context.py`) |
| RAPTOR units | an `exclude_llm` document gets no units and no calls (`ingest/raptor.py`) |
| Router | the prompt carries the question only (<= 600 characters), no document text (`chat/router.py`) |
| Global map-reduce | candidates come from ES with the `exclude_llm` filter (plus the PAT `block_agents` filter), then Postgres re-checks visibility, gate (a), the latest searchable version and `gated_docs`; each map call sees one document's own units (`chat/global_answer.py`) |
| Agentic loop | every retrieval, the first and each sub-query, is `rank_for_chat` as the caller; the planner prompt holds the question and passages that came out of it, with delimiter tags neutralised (`_quote`); sub-queries are plain strings, scope and principal are fixed by the caller (`chat/agentic.py`) |
| Eval judge | refuses with `LabelGateError` if a gated document is in its input (`eval/judge.py`) |
| Eval question generation | samples only documents visible to the chosen non-admin principal and `llm_allowed`, re-checked per document (`eval/generate.py`) |
| Reranker | a cross-encoder service you operate, not a generative model. On library search it receives the passages the caller already sees, **including `exclude_llm` documents** (their text is withheld from PATs, not from the human who may read them); chat and MCP `ask` retrieve with the `exclude_llm` filter. Keep the reranker host inside the same trust boundary as the API |

### No cross-document artefacts

Notes, section/document units and map findings are per document and per version (`derived/{version_id}/...`), carry only their own document's ACL
and label fields in ES (so `acl_sync`, deletes and the Postgres re-check apply unchanged), and a citation always resolves to a real chunk of the
cited document (units expand to source passages; a global answer cites documents). Corpus-wide GraphRAG was rejected for exactly this reason.

### New endpoints and modes

- **`/api/admin/eval/*`** and **`POST /api/admin/retrieval/rebuild`** require a JWT admin (PATs are refused). Each successful request writes
  an `admin.<handler>` row, and the mutating ones also write their own action (`eval.*`, `retrieval.rebuild`): see
  [audit events](../../reference/audit-events.md). Gold queries, gold document ids, ranked results and judge text can reveal that private
  documents exist, so the eval API is admin-only; runs evaluate every named principal through the production path, and a run fails on any hit the principal may not see.
- **`GET /api/search?mode=quick`** (command palette): one Elasticsearch title search under the caller's ACL filter, then the Postgres visibility re-check;
  no embedding or LLM call; takes only `q` (filters, `after` or `page` > 1 are a `422`; `sort`/`size`/`notes` are ignored).
- Interactive embedding failures (`/api/search`, chat, MCP `ask`) are a prompt `503 inference unavailable`: no retries and no shared pause, so a throttled endpoint
  cannot stall other requests. Batch work (ingest, re-embed, rebuild) retries with backoff.
- **Admission caps** (global answers and agentic loops): one per user, a per-process maximum; excess requests take the plain path.

### Performance tooling (`roracle perf`)

The perf harness mints its own test tokens with a throw-away key and serves the matching JWKS from a loopback port of the suite process; the API under test is
configured to trust only that URL. **There is no token-minting code in the application and nothing to enable or disable in production**; a deployment trusts only
the keys its Keycloak publishes. The CLI refuses any database not named `roracle_perf...` and only touches `perf-*` Elasticsearch indices (the `--es-url` itself is
not guarded: never point it at production).
