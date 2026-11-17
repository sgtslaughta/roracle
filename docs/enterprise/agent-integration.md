# Agent integration how-to

How to connect an agent (CLI coding agent, notebook, custom framework) to rOracle's MCP server. This page is the short path. The contract, the tool matrix, label gates, limits, audit and error mapping are in the [MCP guide](../reference/mcp.md). Parameters and schemas are in the [MCP tool reference](../reference/mcp-tools.md). Terms are in the [glossary](../glossary.md).

## Before you start

Operators check these once:

- The reverse proxy forwards `/mcp` and `/.well-known/oauth-protected-resource` to the API. A `401` from `/mcp` points clients at the second path.
- `ROR_PUBLIC_BASE_URL` is set to the external origin, so discovery metadata and handoff URLs are absolute.
- `ROR_PATS_ENABLED` is on (the default) if agents use personal access tokens.
- Decide on a per-token rate limit. `ROR_RATE_LIMIT_PER_MINUTE` defaults to `0`, which means no limit.
- For uploads through MCP, the body cap follows `ROR_MCP_MAX_UPLOAD_BYTES` (default 25 MiB decoded). Raise the proxy body limit to match.

Setting names and defaults: [settings](../reference/settings.md). Proxy and ingress setup: [deployment guide](deployment-guide.md).

## Connect an agent

1. A person signs in to the web UI and creates a token under **Settings, Access tokens** (steps in [agents and tokens](../user/agents-and-tokens.md)). Headless callers can use `POST /api/me/tokens` with an interactive JWT, see the [API guide](../reference/api.md#authentication). Choose `read` unless the agent must upload, propose, write drafts or create handoffs. The secret `ror_pat_...` is shown once.
2. Give the agent the endpoint and the token:

```json
{ "mcpServers": { "roracle": {
    "type": "http", "url": "https://roracle.example.internal/mcp",
    "headers": { "Authorization": "Bearer ${RORACLE_PAT}" } } } }
```

3. Check the connection: list the tools, then call `list_collections` or `search`. A `401` means the token is wrong, expired, revoked or its owner has not signed in within `ROR_PAT_MAX_IDENTITY_AGE_DAYS` (default 30).

Interactive clients can use OAuth instead. They read the discovery metadata and sign in through the identity provider. The client id must be in `ROR_OIDC_ALLOWED_CLIENTS` (default `roracle-web`, `roracle-mcp`). Identity provider setup: [Keycloak setup](keycloak-setup.md).

A token acts as its user, never as an admin, and sees only what that user may see. See [authentication](../reference/mcp.md#authentication).

## Recipes

### Research assistant (read token)

- `search` for documents, then `get_passages` with the returned `chunk_id`s for exact text, pages and boxes.
- `ask` for a cited answer. Citations carry `doc_id`, `chunk_id`, `page` and `quote`.
- `export_bibtex` for the references you used.
- Treat notes that come back with hits as commentary, never as citations.

### Review assistant (write token)

- `list_annotations`, then `propose_annotation`, `reply_to_annotation` or `propose_label`.
- Nothing is published. A person approves, edits or rejects each proposal in the UI. At most 50 can be pending per user and client, so stop and ask the user to review when `too many pending proposals` appears. See [proposals](../reference/mcp.md#proposals-propose-only-a-person-approves).

### Paper-writing agent (write token)

- Load the `write_paper` prompt, or install the skill bundle from `GET /api/skills/roracle-writer.zip`.
- Loop: `save_draft`, `validate_draft`, `check_citations`, fix, repeat. Blocking findings and unresolved or unsupported citations must be fixed.
- The owner submits in the UI. No MCP tool submits, but the REST submit route accepts a write token, so grant write only to trusted agents. See [writing prompt and skill bundle](../reference/mcp.md#writing-prompt-and-skill-bundle).

### Hand work to a person or another agent (write token)

- `create_handoff` with a title, brief and `doc_ids`. Share the returned `display` code or `url`.
- The receiver calls `load_handoff`, then `get_handoff_part` for the transcript or notes. Contents are frozen at creation and redacted for each loader on every load. See [handoffs](../reference/mcp.md#handoffs).
- Users handle handoffs in [handoffs and inbox](../user/handoffs-inbox.md).

### Upload a paper (write token)

- `upload` sends base64 in JSON, visibility `private` by default. A duplicate returns `{"duplicate_of": <id>}`. The paper is searchable only after ingest finishes. Larger files use `POST /api/documents`. See [limits](../reference/mcp.md#limits-and-the-mcp-reserve).

## Sensitivity labels

Admins label documents and notes (see [security, section 13](security.md#13-sensitivity-labels) and [admin labels](../admin/labels.md)). Labels only restrict. For agent integrators:

- Tool output carries the label name and rank, so an agent can tell users what it handled.
- For PAT callers, `block_agents` documents do not exist (`not found`) and `exclude_llm` documents return metadata with `withheld: "<label>"` and no text. Handoffs hide `block_agents` documents from every loader.
- Handoff notes are not label-gated. Do not write content into a note that its audience should not read.
- Details and exact per-tool behaviour: [label gates](../reference/mcp.md#label-gates) and the Label gates line of each tool in the [tool reference](../reference/mcp-tools.md).

## Troubleshooting

| Symptom | Check |
|---|---|
| `401` on every call | Token pasted wrong, revoked or expired. Owner signed in recently. `ROR_PATS_ENABLED`. Proxy forwards the `Authorization` header |
| `405` | Client is sending `GET /mcp`. Only `POST` is served |
| `insufficient scope` | The token is read-only and the tool is a write tool |
| `429` | `ROR_RATE_LIMIT_PER_MINUTE` is set. Wait `Retry-After` seconds |
| `413` | Request body over the cap. Use `POST /api/documents` for big files |
| `not found` for a known paper | No access, or the label has `block_agents` for agents. The answers look the same on purpose |
| Empty `passages` with `withheld` | The label has `exclude_llm`: text is not given to agents |
| `ask` slow or short | See the time budget in [limits](../reference/mcp.md#limits-and-the-mcp-reserve) |

Every tool call is audited as `mcp.<tool>`. What is and is not recorded: [audit](../reference/mcp.md#audit). Operators read the trail as in [admin audit](../admin/audit.md).
