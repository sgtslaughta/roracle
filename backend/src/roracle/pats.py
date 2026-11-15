"""Personal access tokens: opaque `ror_pat_...` bearer secrets, stored only as sha256."""

import hashlib
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.auth import Principal, principal_for_user
from roracle.config import Settings
from roracle.models import PersonalAccessToken

PREFIX = "ror_pat_"


def generate() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(
    session: Session, user_id: UUID, name: str, scopes: Iterable[str], expires_in_days: int
) -> tuple[PersonalAccessToken, str]:
    token = generate()
    row = PersonalAccessToken(
        user_id=user_id,
        name=name,
        prefix=token[:12],
        token_hash=hash_token(token),
        scopes=sorted(set(scopes)),
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
    row.last_used_at = now
    session.flush()
    return Principal(
        base.user_id,
        base.sub,
        base.username,
        base.groups,
        base.is_admin,
        scopes=frozenset(row.scopes),
        client=f"pat:{row.name}",
        via="pat",
    )
