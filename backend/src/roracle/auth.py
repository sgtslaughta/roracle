"""Keycloak JWT validation, principal resolution and user upsert."""

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Annotated, Literal
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from roracle import ratelimit, runtime
from roracle.audit import audit
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import User

ADMIN_ROLE = "roracle-admin"
LEEWAY_S = 30
REFETCH_MIN_INTERVAL_S = 60
KEYS_MAX_AGE_S = 600
KEYS_HARD_MAX_AGE_S = 3600
STALE_DETAIL = "token owner session stale \u2014 sign in again"


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    sub: str
    username: str
    groups: tuple[str, ...]
    is_admin: bool
    scopes: frozenset[str] = frozenset({"read", "write", "manage"})
    client: str = ""
    via: Literal["jwt", "pat", "mcp"] = "jwt"
    pat_id: str = ""

    @property
    def is_agent(self) -> bool:
        return self.via != "jwt"


MANAGE = "manage"


def require_scope(session: Session, p: Principal, scope: str) -> None:
    """403 unless the principal holds `scope`. Call before any lookup so nothing leaks."""
    if scope not in p.scopes:
        audit(session, p, "auth.scope_denied", detail={"need": scope, "via": p.via}, durable=True)
        raise HTTPException(403, "insufficient scope" if scope == "write" else f"insufficient scope: {scope}")


def as_agent(p: Principal) -> Principal:
    """Every non-PAT caller reaching a tool through /mcp is an agent: never admin, never `manage`."""
    if p.via == "pat":
        return p
    return replace(p, via="mcp", is_admin=False, scopes=p.scopes - {"manage"})


def httpx_fetch(url: str) -> dict:
    r = httpx.get(url, timeout=5.0)
    r.raise_for_status()
    return r.json()


class JwksCache:
    """JWKS keyed by kid.

    Unknown kid or stale cache: reload, at most once per 60 s (failed loads count). Keys older than
    10 min are reloaded; if reloading keeps failing the old keys serve until 1 h, then fail closed.
    """

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
        self._loaded_at = 0.0
        self._last_attempt: float | None = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in self._fetch(self._url).get("keys", []):
            if not isinstance(jwk, dict) or not jwk.get("kid") or jwk.get("kty") != "RSA":
                continue
            if jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
                continue  # e.g. Keycloak's RSA-OAEP encryption key
            try:
                keys[jwk["kid"]] = jwt.PyJWK(jwk)
            except (jwt.PyJWTError, ValueError, KeyError, TypeError):
                continue
        self._keys = keys
        self._loaded_at = self._clock()

    def _lookup(self, kid: str) -> jwt.PyJWK | None:
        keys = self._keys  # dict is replaced atomically, never mutated
        if keys is None or self._clock() - self._loaded_at >= KEYS_HARD_MAX_AGE_S:
            return None
        return keys.get(kid)

    def key_for(self, kid: str) -> jwt.PyJWK:
        fresh = self._clock() - self._loaded_at < KEYS_MAX_AGE_S
        if fresh and (key := self._lookup(kid)):
            return key
        with self._lock:
            now = self._clock()
            fresh = now - self._loaded_at < KEYS_MAX_AGE_S
            if fresh and (key := self._lookup(kid)):
                return key
            initial = self._keys is None
            throttled = self._last_attempt is not None and now - self._last_attempt < REFETCH_MIN_INTERVAL_S
            reload = (initial or not fresh or self._lookup(kid) is None) and not throttled
            if reload:
                if not initial:
                    self._last_attempt = now
                try:
                    self._load()
                except Exception:
                    self._last_attempt = now
                    if self._lookup(kid) is None:
                        raise
                else:
                    if initial and self._lookup(kid) is None:  # initial load + one refetch
                        self._last_attempt = now
                        with contextlib.suppress(Exception):  # keep the keys just loaded
                            self._load()
            if (key := self._lookup(kid)) is None:
                raise KeyError(kid)
            return key


def get_jwks(request: Request) -> JwksCache:
    return request.app.state.jwks


def get_app_settings(request: Request, session: Annotated[Session, Depends(get_session)]) -> Settings:
    return runtime.effective(session, request.app.state.settings)


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
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=settings.oidc_audience,
        issuer=settings.oidc_issuer,
        leeway=LEEWAY_S,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
    if claims.get("typ") != "Bearer" or claims.get("azp") not in settings.oidc_allowed_clients:
        raise ValueError("not an access token for an allowed client")
    return claims


def resolve_principal(
    session: Session,
    jwks: JwksCache,
    settings: Settings,
    authorization: str | None,
    session_factory: sessionmaker | None = None,
) -> Principal:
    """Bearer PAT (`ror_pat_`) or Keycloak JWT -> Principal. Every failure is the same 401."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    from roracle import pats  # lazy: pats imports this module

    if token.startswith(pats.PREFIX):
        try:
            return pats.principal_from_pat(session, settings, token, session_factory)
        except pats.StaleIdentity:
            raise HTTPException(401, STALE_DETAIL, headers={"WWW-Authenticate": "Bearer"}) from None
        except ValueError:
            raise _unauthorized() from None
    return _jwt_principal(session, jwks, settings, token)


def current_principal(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    jwks: Annotated[JwksCache, Depends(get_jwks)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    p = resolve_principal(session, jwks, settings, authorization, request.app.state.session_factory)
    target = f"{request.method} {request.url.path}"[:200]
    try:
        ratelimit.check(p, settings.rate_limit_per_minute)
    except HTTPException:
        if ratelimit.audit_due(ratelimit.principal_key(p)):
            audit(session, p, "auth.rate_limited", target, {"via": p.via}, durable=True)
        raise
    if "write" not in p.scopes and request.method not in ("GET", "HEAD", "OPTIONS"):
        audit(session, p, "auth.scope_denied", target, {"need": "write", "via": p.via}, durable=True)
        raise HTTPException(403, "insufficient scope")
    if p.is_agent:
        audit(session, p, "pat.use", target, durable=True)
    return p


def require_jwt(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if p.via != "jwt":
        raise HTTPException(403, "personal access tokens cannot manage tokens")
    return p


def require_human(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if p.via != "jwt":
        raise HTTPException(403, "agents must propose")
    return p


def _jwt_principal(session: Session, jwks: JwksCache, settings: Settings, token: str) -> Principal:
    try:
        claims = _verify(token, jwks, settings)
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
    stmt = insert(User).values(
        sub=sub,
        username=username,
        email=email,
        groups=list(groups),
        is_admin=ADMIN_ROLE in roles,
        last_login_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[User.sub],
        set_={
            "username": username,
            "email": func.coalesce(stmt.excluded.email, User.email),
            "groups": stmt.excluded.groups,
            "is_admin": stmt.excluded.is_admin,
            "last_login_at": func.now(),
        },
    ).returning(User.id)
    user_id = session.execute(stmt).scalar_one()
    azp = str(claims.get("azp", ""))
    p = Principal(user_id, sub, username, groups, ADMIN_ROLE in roles, client=azp)
    return as_agent(p) if azp in settings.oidc_agent_clients else p


def principal_for_user(session: Session, user_id: UUID) -> Principal:
    """Rebuild a Principal from the groups/admin flag stored at the user's last request (worker use)."""
    u = session.get(User, user_id)
    if u is None:
        raise LookupError(f"user {user_id} not found")
    return Principal(u.id, u.sub, u.username, tuple(u.groups), u.is_admin)


def agent_marker(p: Principal) -> dict | None:
    """What an ingest job needs to rebuild an agent uploader's view: its groups (a PAT's snapshot). None for humans."""
    return {"groups": list(p.groups)} if p.is_agent else None


def principal_for_upload(session: Session, user_id: UUID, agent: dict | None) -> Principal:
    """The uploader's view for ingest-time checks: an agent upload (`agent_marker`) keeps the agent gate, is never
    admin and never sees more groups than the user holds now."""
    p = principal_for_user(session, user_id)
    if not agent:
        return p
    groups = tuple(g for g in agent.get("groups", ()) if g in p.groups)
    return replace(p, groups=groups, is_admin=False, via="mcp", scopes=p.scopes - {MANAGE})
