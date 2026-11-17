# ADR-0004: Identity via Keycloak X.509 authenticator; scoped PATs

Status: Accepted (2026-11-15)

## Context

An org proxy terminates user mTLS and forwards the certificate. Agents may run headless.

## Decision

- Keycloak's X.509 client-certificate authenticator reads the cert from proxy headers and issues JWTs
  (RS256, `groups` claim). API and MCP validate JWTs against Keycloak JWKS (cached; refetch once on
  unknown `kid`). Trusted identity headers are never accepted by rOracle services.
- Personal access tokens: random 32-byte secrets, stored as SHA-256 hashes, scopes `read`/`write`,
  expiry required, revocable, globally disableable by admins. A PAT always acts as its user.
- Users are upserted from token claims on first request (`sub` is the stable key).

## Consequences

- One validation path for web, API and MCP. Local dev uses a Keycloak test realm without mTLS.

## Update (2026-11-17)

Details settled in code. A PAT is a `ror_pat_` token whose hash is a plain, unsalted SHA-256 (no pepper or other server secret), default expiry 30 days and at most 365 (`api/routes/tokens.py:26`). A PAT is never admin: admin routes refuse it with 403, even for an admin owner. Token management needs an interactive JWT.
