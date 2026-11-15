"""Keycloak JWT validation, principal resolution and user upsert."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.config import Settings, get_settings
from roracle.db import get_session
from roracle.models import User

ADMIN_ROLE = "roracle-admin"
LEEWAY_S = 30
REFETCH_MIN_INTERVAL_S = 60


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    sub: str
    username: str
    groups: tuple[str, ...]
    is_admin: bool


def httpx_fetch(url: str) -> dict:
    r = httpx.get(url, timeout=5.0)
    r.raise_for_status()
    return r.json()


class JwksCache:
    """JWKS keyed by kid. Unknown kid: refetch once, at most once per 60 s."""

    def __init__(
        self,
        url: str,
        fetch: Callable[[str], dict] = httpx_fetch,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] | None = None
        self._last_refetch: float | None = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        keys = {}
        for jwk in self._fetch(self._url).get("keys", []):
            if jwk.get("kid") and jwk.get("kty") == "RSA":
                keys[jwk["kid"]] = jwt.PyJWK(jwk)
        self._keys = keys

    def key_for(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            if self._keys is None:
                self._load()
            if kid not in self._keys:
                now = self._clock()
                last = self._last_refetch
                if last is None or now - last >= REFETCH_MIN_INTERVAL_S:
                    self._last_refetch = now
                    self._load()
            if kid not in self._keys:
                raise KeyError(kid)
            return self._keys[kid]


@lru_cache
def get_jwks() -> JwksCache:
    return JwksCache(get_settings().oidc_jwks_url)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def _unauthorized() -> HTTPException:
    return HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})


def _str_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected list of strings")
    return tuple(value)


def _verify(token: str, jwks: JwksCache, settings: Settings) -> dict:
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise ValueError("bad header")
    key = jwks.key_for(header["kid"])
    return jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=settings.oidc_audience,
        issuer=settings.oidc_issuer,
        leeway=LEEWAY_S,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )


def current_principal(
    session: Annotated[Session, Depends(get_session)],
    jwks: Annotated[JwksCache, Depends(get_jwks)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    try:
        claims = _verify(token.strip(), jwks, settings)
        sub = claims["sub"]
        if not isinstance(sub, str) or not sub:
            raise ValueError("bad sub")
        username = claims.get("preferred_username") or sub
        email = claims.get("email")
        if not isinstance(username, str) or not (email is None or isinstance(email, str)):
            raise ValueError("bad claim")
        groups = _str_list(claims.get("groups", []))
        roles = _str_list((claims.get("realm_access") or {}).get("roles", []))
    except Exception:  # noqa: BLE001 - any validation/fetch failure -> uniform 401, no detail leaked
        raise _unauthorized() from None
    stmt = insert(User).values(sub=sub, username=username, email=email)
    stmt = stmt.on_conflict_do_update(
        index_elements=[User.sub],
        set_={"username": username, "email": func.coalesce(stmt.excluded.email, User.email)},
    ).returning(User.id)
    user_id = session.execute(stmt).scalar_one()
    return Principal(user_id, sub, username, groups, ADMIN_ROLE in roles)
