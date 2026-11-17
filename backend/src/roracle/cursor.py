"""Opaque keyset cursors: `<timestamptz ISO>,<uuid>` (legacy browse, /me/uploads) and ES sort values (browse).
Opaque so a client cannot depend on, or mangle in a URL (`+` in the offset), the inner format."""

import base64
import binascii
import json
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


def make_sort_cursor(values: list) -> str:
    """ES `sort` values of the last consumed hit -> opaque cursor (urlsafe base64 of compact JSON)."""
    raw = json.dumps(values, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_sort_cursor(s: str) -> list:
    """Cursor -> ES `search_after` values; ValueError when malformed, not a list, or holding non-scalars."""
    try:
        out = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode())
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("malformed cursor") from e
    if (
        not isinstance(out, list)
        or not out
        or not all(isinstance(v, str | int | float) or v is None for v in out)
    ):
        raise ValueError("malformed cursor")
    return out
