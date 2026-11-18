# Troubleshooting agents

What the errors an agent shows mean, and what to do about each one.

## 401 Unauthorized

The token is not accepted. The usual causes:

- The token is wrong, for example copied with a missing character.
- It has expired or you revoked it.
- You have not signed in to the rOracle web app for a while. By default tokens stop after 30 days without a sign-in, and the agent sees "token owner session stale — sign in again".
- Your admins switched access tokens off for the whole site.

Check **Settings, Access tokens** for the token's state. Sign in to the web app again, or create a new token.

## Insufficient scope (403)

The token is **Read only** and the tool needs write access. The agent shows the message text ("insufficient scope") rather than an HTTP code, because MCP tool errors reach it as text. Only 401 and 429 arrive as HTTP status codes at the MCP endpoint; calls to the web API do return 403 and 404. Create a token with **Read and write** and give it to the agent.

A call can also fail as "withheld by label: <label name>". That happens when the agent reads, validates, citation-checks or builds a draft, or downloads a paper's file or evidence, and the draft or paper has the **Exclude from AI** label. No token can do that for such an item.

!!! tip "Least access first"
    Start with a read only token. Make a read and write token only when the agent needs to write.

## 429 Too many requests

The agent is calling too fast. The reply carries a `Retry-After` header that says how many seconds to wait. Slow the agent down or let it retry after that time.

## Paper not found

The agent asked for a paper it cannot see. Either it does not exist, you cannot see it, or its label is **Block agents**, which hides it from every token, admins included. The agent acts as you, so a paper you cannot open is not available to it either.

## Related

- [Set up MCP, step by step](agents-mcp-setup.md)
- [Agents and tokens](agents-and-tokens.md)
- [Sensitivity labels](labels.md)
