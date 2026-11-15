# Agent Integration (MCP)

rOracle exposes a hosted MCP server so agents (CLI coding agents, notebooks, custom frameworks) can
search, read, cite, annotate and write with the same permissions as their user. Delivered by
roadmap plans 7–9 (P7 is implemented; rows marked planned are not); this guide is the contract teams can build against.

## Endpoint and auth

- URL: `https://roracle.<domain>/mcp` (stateless Streamable HTTP, JSON responses, `POST` only).
- Discovery: `GET /.well-known/oauth-protected-resource` (RFC 9728); a 401 carries
  `WWW-Authenticate: Bearer resource_metadata="…"`. The reverse proxy must forward both paths.
- Auth, either:
  - **OAuth 2.1** (interactive clients): the metadata points at the Keycloak realm; client
    `roracle-mcp` (authorization code + PKCE).
  - **Personal access token** (headless): `POST /me/tokens` with an interactive JWT
    (`{"name", "scopes": ["read"|"write"], "expires_in_days"}`; a PAT cannot mint PATs). The token
    `ror_pat_…` is shown once. Send `Authorization: Bearer ror_pat_…`. Expiry 1-365 days (default 30).
    `ROR_PATS_ENABLED=false` disables PATs.
- Scopes: `read` PATs may call read tools and REST `GET` only; `write` is needed for `upload`,
  `create_handoff`, `append_to_handoff` (error `insufficient scope`).
- Every call runs as the token's user; ACLs apply exactly as in the web UI (Postgres re-checks every
  search hit). Calls are audited as `mcp.<tool>` with the client name (`pat:<name>`); failures carry
  `{"error": ...}`. Requests rejected by schema validation (bad argument types/lengths) are not audited.

Example client configuration (generic MCP JSON):
```json
{ "mcpServers": { "roracle": {
    "type": "http", "url": "https://roracle.example.org/mcp",
    "headers": { "Authorization": "Bearer ${RORACLE_PAT}" } } } }
```

## Tools

| Tool | Scope | Purpose |
|---|---|---|
| `search(query, collection?, limit?)` | read | Hybrid search (limit 1-50); documents with passages and `chunk_id`s |
| `get_document(doc_id)` | read | Metadata, versions, summary, tags, votes |
| `get_passages(chunk_ids, context?)` | read | Exact passages (<= 50 ids, `context` 0-2 neighbours), page + bbox |
| `list_collections()` | read | The user's collections |
| `ask(question, collection?)` | read | Cited answer from visible passages: `{answer, citations}` |
| `load_handoff(code)` | read | Handoff manifest (brief, docs, passages, notes, `parts`, `withheld`) |
| `get_handoff_part(code, part?, page?)` | read | `transcript` or `notes`, 20 items per page |
| `create_handoff(title, brief, doc_ids?, goal?, audience?, expires_in_days?)` | write | Hand work to a human or agent; returns `{code, display, url, uri, withheld}` |
| `append_to_handoff(code, note)` | write | Add a note (max 200 per handoff) |
| `upload(filename, content_base64, title?, visibility?)` | write | Add a document (default `private`); duplicate returns `{"duplicate_of": id}` |
| `export_bibtex`, `list_annotations`, `propose_annotation`, `reply_to_annotation` | - | planned (P8/P9) |
| `save_draft`, `validate_draft`, `check_citations` | - | planned (P8/P9) |

Resource template: `roracle://handoff/{code}` (same manifest and auth as `load_handoff`).
Prompts and `roracle://profiles|templates` resources: planned (P8/P9).

`upload` sends the file base64-encoded in the JSON body, so it is capped at the decoded size
`ROR_MCP_MAX_UPLOAD_BYTES` (default 25 MiB); larger files use `POST /documents` (REST limit
`ROR_MAX_UPLOAD_BYTES`). The `/mcp` request body is capped accordingly (413). Uploads enter the normal
ingest queue and become searchable only after ingest finishes.

## Handoffs

A handoff is a frozen bundle (brief, goal, documents, passages, optional chat transcript, notes) made
in the UI (**Continue in agent**) or by an agent (`create_handoff`, kind `agent`, created via `mcp`).

- Code: 8 characters from a 31-symbol alphabet (about 40 bits), shown `XXXX-XXXX`. Accepted forms:
  `XXXXXXXX`, `XXXX-XXXX`, `/h/XXXX-XXXX` (also as full URL) and `roracle://handoff/XXXXXXXX`.
- The code is not a secret on its own: loading needs authentication and audience membership
  (`creator`, `shared` users/groups, or `authenticated`; MCP creates `creator` or `authenticated`).
  Chat handoffs are owner-only.
- Expiry: 1-90 days (UI default 7). Not visible or not in audience: 404. Revoked or expired: 410.
- Redaction at read time: documents/passages the loader cannot see are dropped and only counted in
  `withheld`. Source handles of chats and collections are hidden from non-creators.
- Audit: `handoff.create|load|note|revoke|update`, plus the `mcp.<tool>` row.
- `url` is absolute when `ROR_PUBLIC_BASE_URL` is set, otherwise a path (`/h/XXXX-XXXX`).

## Proposals and evidence

Agents never publish directly. `propose_annotation` returns a proposal id; the user approves, edits or
rejects it in **Agent proposals**. Findings may carry evidence as an RO-Crate (`ro-crate-metadata.json`
+ files, ≤ 1 GB, or by reference with SHA-256). Required provenance: code repository + commit,
container image digest, inputs, parameters + seed, agent client + model, start/end time.
rOracle stores and displays evidence; it never executes it.

## Writing papers

Load the `write_paper` prompt (or the `roracle-writer` SKILL.md bundle from `/skills/roracle-writer.zip`).
The active writing profile defines required sections, citation style and checks. Workflow:
search → outline → draft sections → `save_draft` → `validate_draft` → `check_citations` → fix →
user submits in the UI. Citations must resolve to library documents and be supported by the cited
passage, otherwise submission is blocked.

## Limits

Per-user rate limits: planned (P11). Max 50 pending proposals per session: planned (P8).
