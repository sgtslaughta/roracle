"""Perf-environment tokens WITHOUT any production code path: a throw-away RSA key, its JWKS, and RS256 access tokens
for the synthetic users (`perf-sub-<i>`, group `g<i % 20>`, the same mix `synth_pg` seeds).

The API under test is started with `ROR_OIDC_JWKS_URL` pointing at a local server that serves the JWKS written here
and `ROR_OIDC_ISSUER` set to the issuer used for minting; it validates tokens exactly as in production (signature,
iss, aud, azp, typ). Nothing in the application can mint a token, so there is nothing to enable in production: a
production deployment only trusts the keys its own Keycloak publishes. The CLI refuses non-perf database names."""

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "perf-k1"


def mint(users: int, *, issuer: str, audience: str = "roracle", azp: str = "roracle-web", ttl_s: int = 86400):
    """(jwks, tokens): one RS256 access token per synthetic user."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwks = {"keys": [{**jwk, "kid": KID, "use": "sig", "alg": "RS256"}]}
    now = int(time.time())
    tokens = [
        jwt.encode(
            {
                "sub": f"perf-sub-{i}", "preferred_username": f"perf-user-{i}", "groups": [f"g{i % 20}"],
                "realm_access": {"roles": []}, "exp": now + ttl_s, "iss": issuer, "aud": audience,
                "typ": "Bearer", "azp": azp,
            },
            key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        for i in range(users)
    ]  # fmt: skip
    return jwks, tokens
