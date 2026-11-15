# Agent Integration (MCP)

rOracle exposes a hosted MCP server so agents (CLI coding agents, notebooks, custom frameworks) can
search, read, cite, annotate and write with the same permissions as their user. Delivered by
roadmap plans 7–9; this guide is the contract teams can build against.

## Endpoint and auth

- URL: `https://roracle.<domain>/mcp` (Streamable HTTP transport).
- Auth, either:
  - **OAuth 2.1** (interactive clients): the server advertises protected-resource metadata pointing at
    the Keycloak realm; client `roracle-mcp` (authorization code + PKCE).
  - **Personal access token** (headless): create in rOracle → Settings → Access tokens; send
    `Authorization: Bearer ror_pat_…`. Scopes: `read` (default) or `read write`. Expiry required.
- Every call runs as the token's user; ACLs apply exactly as in the web UI. Calls are audited with
  the client name.

Example client configuration (generic MCP JSON):
```json
{ "mcpServers": { "roracle": {
    "type": "http", "url": "https://roracle.example.org/mcp",
    "headers": { "Authorization": "Bearer ${RORACLE_PAT}" } } } }
```

## Tools

| Tool | Scope | Purpose |
|---|---|---|
| `search(query, filters?, collection?, limit?)` | read | Hybrid search; returns documents with passages and citation handles |
| `get_document(doc_id, version?)` | read | Metadata, versions, summary, tags, votes, references |
| `get_passages(chunk_ids, context?)` | read | Passage text with neighbouring chunks, page + bbox |
| `list_collections()` | read | User's collections |
| `ask(question, collection?)` | read | Full cited RAG answer (if enabled by admins) |
| `export_bibtex(doc_ids)` | read | BibTeX with stable rOracle keys |
| `load_handoff(id)`, `get_handoff_part(id, part, page?)` | read | Continue a researcher's UI session |
| `create_handoff(kind, title, brief, doc_ids?)`, `append_to_handoff(id, note)` | write | Hand work back to a human |
| `list_annotations(doc_id, type?)` | read | Notes, findings, corrections, relations |
| `propose_annotation(doc_id, anchor, type, text, relation?, evidence?)` | write | Creates a **proposal**; a human approves it in the UI |
| `reply_to_annotation(note_id, text)` | write | Proposal for a reply |
| `upload(file, title?, visibility?)` | write | Add a document |
| `save_draft`, `validate_draft`, `check_citations` | write/read | Paper writing (see below) |

Prompts: `write_paper(profile?, topic?)`. Resources: `roracle://profiles/{name}`,
`roracle://templates/{name}`, `roracle://handoff/{id}`.

## Handoffs

A researcher clicks **Continue in agent** and copies a prompt like
`Using the rOracle MCP server, load handoff 7KQ2-XM9P and continue: …`. The handoff id is not a
secret: loading still requires the agent's own authentication, and documents the agent's user cannot
see are withheld (the manifest says how many). Expired or revoked handoffs return an error.

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

Per-user rate limits apply (HTTP 429 with `Retry-After`). Max 50 pending proposals per session.
