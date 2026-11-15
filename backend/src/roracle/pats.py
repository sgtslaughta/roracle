"""Personal access tokens: opaque `ror_pat_...` bearer secrets, stored only as sha256."""

import hashlib
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from roracle.auth import Principal, principal_for_user
from roracle.config import Settings
from roracle.models import PersonalAccessToken, User

PREFIX = "ror_pat_"
_TOUCH = text(
    "UPDATE personal_access_tokens SET last_used_at = now() WHERE id = :id"
    " AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')"
    " AND id IN (SELECT id FROM personal_access_tokens WHERE id = :id FOR UPDATE SKIP LOCKED)"
)


class StaleIdentity(ValueError):
    """Token is valid but its owner has not signed in recently enough."""


def generate() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(
    session: Session, user_id: UUID, name: str, scopes: Iterable[str], expires_in_days: int
) -> tuple[PersonalAccessToken, str]:
    token = generate()
    owner = session.get(User, user_id)
    row = PersonalAccessToken(
        user_id=user_id,
        name=name,
        prefix=token[:12],
        token_hash=hash_token(token),
        scopes=sorted(set(scopes)),
        groups=list(owner.groups) if owner else [],
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    session.add(row)
    session.flush()
    return row, token


def principal_from_pat(session: Session, settings: Settings, token: str) -> Principal:
    if not settings.pats_enabled:
        raise ValueError("pats disabled")
    row = session.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.token_hash == hash_token(token))
    )
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise ValueError("invalid token")
    try:
        base = principal_for_user(session, row.user_id)
    except LookupError:
        raise ValueError("user missing") from None
    owner = session.get(User, row.user_id)
    limit = timedelta(days=settings.pat_max_identity_age_days)
    if owner is None or owner.last_login_at is None or owner.last_login_at < now - limit:
        raise StaleIdentity("stale")
    session.execute(_TOUCH, {"id": row.id})  # throttled, never waits on a row lock
    current = set(base.groups)
    return Principal(
        base.user_id,
        base.sub,
        base.username,
        tuple(g for g in row.groups if g in current),  # snapshot intersect current
        False,  # admin needs an interactive JWT
        scopes=frozenset(row.scopes),
        client=f"pat:{row.name}",
        via="pat",
    )
