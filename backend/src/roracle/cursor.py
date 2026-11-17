"""Keyset cursors `<timestamptz ISO>,<uuid>` shared by the browse list and /me/uploads."""

from datetime import datetime
from uuid import UUID


def parse_cursor(after: str) -> tuple[datetime, UUID]:
    """`<created_at ISO>,<id>` -> (created_at, id); ValueError when malformed."""
    ts, _, ident = after.rpartition(",")
    at = datetime.fromisoformat(ts)
    if at.tzinfo is None:
        raise ValueError("cursor needs a timezone")
    return at, UUID(ident)


def make_cursor(at: datetime, ident: UUID) -> str:
    return f"{at.isoformat()},{ident}"
