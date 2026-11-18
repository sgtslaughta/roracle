# MCP guide

rOracle serves a Model Context Protocol (MCP) server so agents can search, read, cite, annotate and draft with the permissions of the user who connected them. This page is the contract for integrators. Tool parameters, schemas and per-tool label gates are generated from the code in [MCP tool reference](mcp-tools.md); this page does not repeat them. Terms such as PAT, handoff and label are in the [glossary](../glossary.md). The HTTP side (token minting, rate-limit headers, error bodies) is in the [HTTP API guide](api.md) and [errors](errors.md).

## Endpoint and transport

- URL: `https://roracle.example.internal/mcp`.
- Transport: stateless Streamable HTTP with JSON responses (no SSE stream, no session id). Only `POST` is served: `GET /mcp` returns `405` with `Allow: POST`.
- The endpoint is bearer-only, so the SDK's DNS-rebinding guard is switched off. Every request needs `Authorization: Bearer <token>`.
- Request body cap: `ROR_MCP_MAX_UPLOAD_BYTES * 4 / 3 + 64 KiB` (default 25 MiB gives about 33.5 MB). A larger body is refused with `413` by the transport before any tool runs. The REST JSON size floor does not apply to `/mcp`.
- Discovery: `GET /.well-known/oauth-protected-resource` (RFC 9728) returns `resource` (the `/mcp` URL), `authorization_servers` (`ROR_OIDC_ISSUER`), `bearer_methods_supported: ["header"]` and `scopes_supported: ["read", "write"]`. A `401` from `/mcp` carries `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource"`. The base is `ROR_PUBLIC_BASE_URL` when set, otherwise the request's own origin, so set it behind a proxy and forward both paths.

Generic client configuration:

```json
{ "mcpServers": { "roracle": {
    "type": "http", "url": "https://roracle.example.internal/mcp",
    "headers": { "Authorization": "Bearer ${RORACLE_PAT}" } } } }
```

## Authentication

Two credential types are accepted, resolved by the same code as the REST API (see [API guide, authentication](api.md#authentication)).

| | OAuth access token (Keycloak JWT) | Personal access token (PAT) |
|---|---|---|
| For | Interactive clients | Headless agents and scripts |
| Client id | `azp` must be in `ROR_OIDC_ALLOWED_CLIENTS` (default `roracle-web`, `roracle-mcp`) | n/a |
| Scopes | Always `read` and `write` | `read`, or `read` and `write`, chosen at creation (default `read`) |
| Admin | Yes, if the user has the admin role | Never, even for an admin owner |
| Audit client name | The token's `azp` | `pat:<token name>` |

- Mint a PAT with `POST /api/me/tokens` using an interactive JWT (body `name`, `scopes`, `expires_in_days` 1-365, default 30). A PAT cannot mint PATs. The `ror_pat_...` secret is shown once. `ROR_PATS_ENABLED=false` rejects every PAT with `401`.
- A PAT stops working with `401` when its owner has not signed in within `ROR_PAT_MAX_IDENTITY_AGE_DAYS` (default 30). Its groups are the snapshot taken at creation, intersected with the groups stored at the owner's last JWT request (sign-in or app use); a group removal reaches the token only then.
- Failed authentication returns plain HTTP `401` (same body for every cause except the stale owner) and never reaches a tool.
- Every call runs as the token's user. Visibility, shares and groups apply exactly as in the web UI, and Postgres re-checks every document, version and stage behind search results (R19). Older versions of a visible document may still be returned.
- Scope: a tool marked `write` in the matrix below fails with the tool error `insufficient scope` for a read-only PAT.

## Scope and tool matrix

Every tool links to its generated entry (parameters, schema, gate text). `write` means the tool calls `_need_write`.

| Area | Tool | Scope |
|---|---|---|
| Library | [`search`](mcp-tools.md#search), [`get_document`](mcp-tools.md#get_document), [`get_passages`](mcp-tools.md#get_passages), [`list_collections`](mcp-tools.md#list_collections), [`export_bibtex`](mcp-tools.md#export_bibtex) | read |
| Answers | [`ask`](mcp-tools.md#ask) | read |
| Notes | [`list_annotations`](mcp-tools.md#list_annotations) | read |
| Notes (propose only) | [`propose_annotation`](mcp-tools.md#propose_annotation), [`reply_to_annotation`](mcp-tools.md#reply_to_annotation), [`propose_label`](mcp-tools.md#propose_label) | write |
| Upload | [`upload`](mcp-tools.md#upload) | write |
| Handoffs | [`load_handoff`](mcp-tools.md#load_handoff), [`get_handoff_part`](mcp-tools.md#get_handoff_part) | read |
| Handoffs | [`create_handoff`](mcp-tools.md#create_handoff), [`append_to_handoff`](mcp-tools.md#append_to_handoff) | write |
| Writing | [`save_draft`](mcp-tools.md#save_draft), [`validate_draft`](mcp-tools.md#validate_draft), [`check_citations`](mcp-tools.md#check_citations) | write |

Prompts and resources need no scope beyond a valid token: prompt `write_paper`, resource template `roracle://handoff/{code}`, and `roracle://writing/profiles`, `roracle://writing/profiles/{name}`, `roracle://writing/style-guide`, `roracle://writing/citation-rules` (listed in [Prompts and Resources](mcp-tools.md#prompts)).

`ask` also reports `route` (`fact`, `multihop` or `global`) when the query router is on (`ROR_ROUTER`), and `subqueries` when the agentic loop ran (`ROR_AGENTIC_RETRIEVAL`). Both fields are absent when the features are off or the question was not admitted.

## Label gates

Labels only restrict; they never widen what a caller can see. Tool output carries `label: {name, rank}` (no colour or gate flags) on search hits, `get_document`, `get_passages`, `ask` citations, `list_annotations`, `save_draft`, handoff `documents[]`, and as `max_label` on `list_collections`. Admin and user setup is in [admin labels](../admin/labels.md).

**Which callers are gated.** The label gates below key on the credential type, not on the client. They apply to **PAT** callers. An OAuth access token on `/mcp` is the interactive user and is not subject to the PAT gates in search, `get_document`, `get_passages` and `list_annotations`. Two rules hold for every caller: `ask` never sends `exclude_llm` text to the model, and handoffs hide `block_agents` documents from every loader. If your policy requires the agent gates, give agents PATs. Any JWT from a client in `ROR_OIDC_ALLOWED_CLIENTS` is accepted on `/mcp`.

| Label flag | Effect for a PAT caller |
|---|---|
| **block_agents** | The document, and any note whose effective label has it, is treated as non-existent: `404 not found` (same text as an invisible document), absent from `search`, `ask`, `get_*`, `list_annotations`, `export_bibtex`, handoffs and draft tools. Admin owners are not exempt, because a PAT is never admin. |
| **exclude_llm** | Metadata and label stay visible, text does not. `search` returns `passages: []` with `withheld: "<label>"`. `get_passages` returns `[withheld: <label>]` as the text. `get_document` omits the summary and adds `withheld`. Note bodies are withheld. `ask` never uses these documents as context, for any caller. `validate_draft` and `check_citations` refuse an `exclude_llm` draft (`403 withheld by label: <name>`). `ask` filters `exclude_llm` documents out before rerank, so they are never scored or sent to the LLM there. In `search`, the reranker may still score such passages inside the service; they are never sent to an LLM as context. |
| **block_export** | `export_bibtex` silently omits the document unless the caller owns it (or is an admin on an interactive JWT). |

Notes that reach an agent's LLM (in `search` hits and `ask`) pass the strict quality gate: the caller's own notes, or notes above `ROR_CHAT_NOTE_MIN_UPVOTES` (default 3) and `ROR_CHAT_NOTE_MIN_RATIO` (default 0.6). Notes are commentary, never citations.

`upload` takes a label **name**. The label must rank at least the library default unless the caller is an admin on an interactive JWT, so a PAT cannot choose a lower label. An unknown name returns a tool error listing the valid names. See [label gates for the REST routes](../admin/labels.md).

## Handoffs

A handoff is a context package. **Handoffs have no live mode.** Brief, goal, document ids, passages and any chat transcript are frozen when the handoff is created. Only appended notes grow afterwards. What a loader gets is re-evaluated on every load: audience, expiry, revocation, visibility and redaction are checked again each time.

- Create: `create_handoff` (kind `agent`, `created_via` `mcp`) or the UI. MCP creators choose audience `creator` (default) or `authenticated`. Audience `shared` (named users and groups) and chat or collection handoffs come from the UI or REST. Expiry is 1-90 days (MCP default 7).
- Code: 8 characters from a 31-symbol alphabet, shown `XXXX-XXXX`. `load_handoff` and `get_handoff_part` accept `XXXXXXXX`, `XXXX-XXXX`, `/h/XXXX-XXXX` (also as a full URL) and `roracle://handoff/XXXXXXXX`; anything else is `404`. The code is not a credential: the loader must be authenticated and in the audience.
- Load errors: not in the audience, unknown code or invalid form `404`; revoked or expired `410`.
- Redaction at load: the dropped and withheld documents are counted, never named. Documents the loader cannot see (ACL, R19 re-check), and documents whose label has `block_agents` **for every loader, human or agent**, are dropped and counted in `withheld`. `withheld` is a load-time count specific to the loader. `create_handoff` returns `dropped`, the creation-time count of requested `doc_ids` that were not kept (invisible, `block_agents` or nonexistent).
- Text of `exclude_llm` documents is withheld from PAT loaders: manifest `documents[]` items carry `label` and `text_withheld`, and `withheld_by_reason` splits `unavailable` from `label_text`.
- Transcript (`get_handoff_part` with `part: transcript`, 20 items per page): user messages are shown as written. Assistant replies are replaced by `[withheld: unavailable]` when they drew on a `block_agents` paper (any loader) or on a paper the loader cannot see (ACL, not label); those citations are reduced to `{n, available: false}`. Replies that drew on an `exclude_llm` paper are replaced by `[withheld: <label>]` for PAT loaders only, and the citations of such papers lose their text.
- Notes (`part: notes`, 20 per page, up to 200 per handoff, each at most 10,000 characters): **not label-gated**. Notes are free text written by the creator or by agents and are shown as written. Do not put content in a handoff note that the audience should not read. `append_to_handoff` needs a write token and a handoff the caller may load (`409 note limit reached` after 200). The manifest carries the last 20 notes.
- Chat and collection handoffs hide their source handles from non-creators. Manifests never include passages that come from notes.
- `url` is absolute when `ROR_PUBLIC_BASE_URL` is set, otherwise the path `/h/XXXX-XXXX`.
- Audit: `handoff.create`, `handoff.load` (also for each `get_handoff_part`, with `part` and `page`), `handoff.note`, `handoff.revoke`, `handoff.update`, plus the `mcp.<tool>` row.

User-facing handoff steps are in [handoffs and inbox](../user/handoffs-inbox.md).

## Proposals: propose only, a person approves

Agents never publish notes or change labels. The three proposal tools store a pending proposal and return `{proposal_id, status}`.

- `propose_annotation` (comment, finding, correction, question, relation) and `reply_to_annotation` need a readable target (the PAT agent gate applies). Agent note labels are never chosen: the note inherits the document label.
- `propose_label` names a label and a document, or a thread-root note with `annotation_id` (replies are refused with `422`). The agent's `reason` is advisory and not stored. The change is decided at approval, with the approver's own rights: an owner may raise, only an admin may lower, and the approver supplies any required reason.
- Default visibility is `private`. A visibility requested by the agent is stored with the proposal and the approver may edit it.
- A pending proposal is visible only to its owner. They approve, edit or reject it in the proposals inbox (`/api/proposals/{proposal_id}/approve`, `/api/proposals/{proposal_id}/reject`, `/api/proposals/bulk-approve`). These routes need an interactive JWT: a PAT gets `403 agents must propose`.
- Cap: 50 pending proposals per user and client name (`pat:<name>` for PATs). The 51st fails with `429 too many pending proposals` until some are decided.
- Audit: `mcp.propose_*`, then `proposal.approve` or `proposal.reject` with the client name.

User view: [agents and tokens](../user/agents-and-tokens.md).

## Writing prompt and skill bundle

- Prompt `write_paper(topic, profile="journal-article")` returns the paper-writing instructions, the profile's required sections and metadata, style guidance and template. An unknown profile is a `404 not found` tool error.
- The same text ships as a skill bundle, `GET /api/skills/roracle-writer.zip` (any authenticated caller; contains `roracle-writer/SKILL.md`, `references/style-guide.md`, `references/citation-rules.md` and one `profiles/<name>.yaml` per effective profile). Use it for agents that load skills instead of MCP prompts.
- Flow: `search` and `get_passages` to collect evidence, `save_draft`, `validate_draft`, `check_citations`, fix, then the owner submits the draft in the UI. No MCP tool submits. The REST route `POST /api/drafts/{doc_id}/submit` is not restricted to interactive callers, so a write token could call it: give write access only to agents you trust. Draft size is capped by `ROR_DRAFT_MAX_BYTES` (2 MiB) and a draft may use at most `ROR_WRITING_MAX_CITATIONS` distinct citation keys (200). Profiles, review and publishing are in [writing drafts](../user/writing-drafts.md) and [writing profiles](../admin/writing-profiles.md).

## Limits and the MCP reserve

| Limit | Value | Source |
|---|---|---|
| Rate limit | `ROR_RATE_LIMIT_PER_MINUTE`, default `0` (off). Per token (PAT id or user id) and per API replica; each authenticated `/mcp` request takes one token. Over the limit: HTTP `429` with `Retry-After`. | [API guide](api.md#rate-limits) |
| `upload` | Decoded file at most `ROR_MCP_MAX_UPLOAD_BYTES` (25 MiB), sent base64 in JSON. Larger files use `POST /api/documents` (`ROR_MAX_UPLOAD_BYTES`). Duplicate content returns `{"duplicate_of": <id>}`, not an error. Ingest is asynchronous: the paper is searchable only after ingest finishes. | `mcp/tools_write.py` |
| `ask` time budget | `ROR_GLOBAL_MCP_BUDGET_S` (default 45 s) bounds the global path inside one MCP call, because clients time out near 60 s. The agentic loop runs until `min(ROR_AGENTIC_TIMEOUT_S, ROR_GLOBAL_MCP_BUDGET_S - ROR_AGENTIC_ANSWER_RESERVE_S)`: with defaults 30 s and 45 s - 20 s, that is 25 s, leaving 20 s to write the answer. | `mcp/tools_write.py`, `config.py` |
| `search` | `limit` 1-50 (default 10), query at most 1000 characters | `mcp/server.py` |
| `get_passages` | 1-50 chunk ids of at most 100 characters, `context` 0-2 | `mcp/server.py` |
| `list_annotations` | 500 notes, oldest first | tool description |
| `export_bibtex` | 200 documents | tool description |
| `create_handoff` | `doc_ids` 500, `brief` 20,000, `goal` 2,000, `title` 200 characters | `mcp/tools_write.py` |
| Proposals | 50 pending per user and client | `notes/proposals.py` |
| Handoff notes | 200 per handoff | `handoff.py` |

Setting names and defaults are in [settings](settings.md).

## Audit

`mcp.<tool>` rows record the user, the client name, and a target: a SHA-256 prefix for free text (`search` query, `ask` question, note bodies), the filename for `upload`, the title for `create_handoff`, ids or handoff codes for the rest (truncated to 200 characters). Prompts and resources are audited the same way, as `mcp.<name>` where `<name>` is `write_paper`, `writing_profiles`, `writing_profile`, `writing_style_guide`, `writing_citation_rules` or `handoff_resource`.

- MCP calls never write `pat.use`. That row is a REST-only audit for successful PAT calls.
- A tool that raises an HTTP-style error (`insufficient scope`, `not found`, bad input) is still audited: the transaction is rolled back, then one `mcp.<tool>` row is written with `detail` `{"error": true}`.
- An unexpected exception is audited, best effort, with `{"error": "internal"}` and the SDK returns a generic tool error.
- Not audited as `mcp.<tool>`: requests rejected before a tool runs, namely authentication failures and `429` rate limits (HTTP level), bodies over the size cap (`413`), and calls whose arguments fail schema validation (wrong types or over-length strings).

Admin audit viewing is in [admin audit](../admin/audit.md).

## Error mapping

A tool error is a normal MCP result with `isError: true` and one text block. The text is the HTTP-style `detail`, **without** the status code. Structured details are flattened to `code (key=value, ...)`, lists to semicolon-joined messages. Match on text only as a hint.

| Text (examples) | Cause |
|---|---|
| `insufficient scope` | Read-only PAT called a `write` tool |
| `not found` | Unknown id, no access, `block_agents`, or a bad id form (all look the same) |
| `unknown label; valid labels: ...` | `upload` or `propose_label` with an unknown label name |
| `label is below the default; agents cannot choose it` | `upload` below the library default |
| `file too large for MCP upload; use POST /documents`, `invalid base64` | `upload` size or encoding |
| `too many pending proposals` | 50 pending proposals for this client |
| `handoff revoked`, `handoff expired`, `note limit reached` | Handoff state |
| `withheld by label: <name>` | Draft tool on an `exclude_llm` draft |
| `inference unavailable`, `search unavailable` | `ask` with the model or the search index down |
| `bad chunk id: ...`, `chunk_ids must hold 1..50 ids`, `query too long (max 1000)` | Input outside the documented limits |

Problems at HTTP level are plain statuses and never become tool results: `401` (credential), `405` (`GET /mcp`), `413` (body), `429` (rate limit, with `Retry-After`).

Argument schema violations (wrong types, over-length strings) are rejected by the SDK as a tool error before the tool runs. Fix the call; do not retry unchanged.

## Related

- [Agent integration how-to](../enterprise/agent-integration.md)
- [MCP tool reference](mcp-tools.md) (generated)
- [Architecture: access control](../architecture/access-control.md)
