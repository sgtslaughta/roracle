# Keycloak Setup for rOracle

Goal: users authenticate with their client certificate (mTLS at the org proxy); Keycloak issues
RS256 JWTs that rOracle validates. Agents use OAuth (MCP clients) or rOracle PATs.
Dev equivalent (no mTLS): `deploy/dev/keycloak/realm-roracle.json`.

## 1. Realm

| Setting | Value |
|---|---|
| Realm | your existing realm (dev: `roracle`) |
| Access token lifespan | 5 min |
| SSO session idle / max | per site policy (e.g. 30 min / 10 h) |
| Signature algorithm | RS256; rotate keys per policy (rOracle refetches JWKS on unknown `kid`) |

## 2. X.509 client-certificate authentication

1. Org proxy terminates mTLS and forwards the client certificate to Keycloak in a header
   (e.g. `ssl-client-cert`, URL-encoded PEM). Keycloak must accept that header **only from the proxy**
   (network policy + Keycloak `spi-x509cert-lookup-provider=nginx|haproxy|apache|envoy` with the
   proxy's header names configured).
2. Authentication → Flows → duplicate *browser* → add **X509/Validate Username Form** before the
   username/password step (or make it the only step):
   - User identity source: `Match SubjectDN using regular expression` or `Subject's Alternative Name
     Email` (site choice), mapped to username or email attribute.
   - Enable CRL/OCSP checking per site PKI policy; set `Bypass identity confirmation` = on.
3. Bind the new flow as the realm browser flow.

## 3. Client `roracle-web` (SPA)

| Setting | Value |
|---|---|
| Client type | OpenID Connect, public client |
| Standard flow | on, PKCE `S256` required |
| Valid redirect URIs | `https://roracle.<domain>/*` |
| Web origins | `https://roracle.<domain>` |
| Direct access grants | off |

Client scopes / mappers on `roracle-web`:
- **Audience** mapper: included audience `roracle` (rOracle checks `aud`).
- **Group Membership** mapper: claim `groups`, *Full group path* **on**, add to access token.
- Realm roles are included in `realm_access.roles` (default `roles` scope).

## 4. Client `roracle-mcp` (agent OAuth)

OpenID Connect public client for MCP clients that support OAuth 2.1 authorization code + PKCE.
Same audience and group mappers. Redirect URIs per supported agent clients (loopback
`http://127.0.0.1/*` for desktop CLIs). rOracle's `/mcp` publishes protected-resource metadata
pointing at this realm.

## 5. Roles and groups

- Realm role `roracle-admin` → rOracle admin section and audit viewer.
- Groups model **org units** (e.g. `/Physics/Computational`). A document's `org_unit` visibility
  covers the owning group and its descendants. Put each user in their primary org group first
  (rOracle uses the first group as the default owning unit for uploads).

## 6. Verify

```bash
# with a token from the realm (dev: password grant is enabled for the test user only)
curl -s -H "Authorization: Bearer $TOKEN" https://roracle.<domain>/me
# → {"id": "...", "username": "...", "groups": ["/Physics/Computational"], "is_admin": false}
```
Expected failures: wrong audience, expired token or unknown key → `401 invalid token`.

## 7. Disabling users and personal access tokens

Disabling or removing a user in Keycloak stops their JWTs, but their rOracle PATs keep working until
the owner's last sign-in is older than `ROR_PAT_MAX_IDENTITY_AGE_DAYS` (default 30). To cut access
immediately, an admin calls `DELETE /admin/users/{user_id}/pats` (revokes all of that user's PATs).

