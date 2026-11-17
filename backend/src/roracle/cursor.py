"""Opaque keyset cursors (urlsafe base64 of `<timestamptz ISO>,<uuid>`) shared by the browse list and /me/uploads.
Opaque so a client cannot depend on, or mangle in a URL (`+` in the offset), the inner format."""

import base64
import binascii
from datetime import datetime
from uuid import UUID


def parse_cursor(after: str) -> tuple[datetime, UUID]:
    """Cursor -> (created_at, id); ValueError when malformed."""
    try:
        raw = base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise ValueError("malformed cursor") from e
    ts, _, ident = raw.rpartition(",")
    at = datetime.fromisoformat(ts)
    if at.tzinfo is None:
        raise ValueError("cursor needs a timezone")
    return at, UUID(ident)


def make_cursor(at: datetime, ident: UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()},{ident}".encode()).decode().rstrip("=")
