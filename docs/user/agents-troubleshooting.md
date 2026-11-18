# Troubleshooting agents

What the errors an agent shows mean, and what to do about each one.

## 401 Unauthorized

The token is not accepted. The usual causes:

- The token is wrong, for example copied with a missing character.
- It has expired or you revoked it.
- You have not signed in to the rOracle web app for a while. By default tokens stop after 30 days without a sign-in, and the agent sees "token owner session stale, sign in again".
- Your admins switched access tokens off for the whole site.

Check **Settings, Access tokens** for the token's state. Sign in to the web app again, or create a new token.

## 403 Insufficient scope

The token is **Read only** and the tool needs write access. Create a token with **Read and write** and give it to the agent.

A 403 can also say "withheld by label". That means a draft the agent tried to validate, citation-check or build has the **Exclude from AI** label. No token can do that to such a draft.

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
