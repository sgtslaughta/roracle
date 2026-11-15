"""Seed helpers for search/document API tests."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from roracle.models import Blob, Document, DocumentVersion, User


def user_id(db, client, headers, sub) -> uuid.UUID:
    client.get("/me", headers=headers)  # first request creates the user row
    return db.scalar(select(User.id).where(User.sub == sub))


def seed(db, owner_id, *, title="Doc", visibility="everyone", org_unit="/", year=2020, authors=("Ann",),
         filename="p.pdf", stage="searchable", hours=0, venue="NeurIPS", data=None, content_type="application/pdf",
         versions=1):  # fmt: skip
    data = data or uuid.uuid4().bytes
    sha = hashlib.sha256(data).hexdigest()
    if db.get(Blob, sha) is None:
        db.add(Blob(sha256=sha, size=len(data)))
        db.flush()
    d = Document(owner_id=owner_id, title=title, org_unit=org_unit, visibility=visibility)
    db.add(d)
    db.flush()
    out = []
    for n in range(1, versions + 1):
        v = DocumentVersion(
            document_id=d.id, version=n, blob_sha256=sha, filename=filename, content_type=content_type,
            size=len(data), stage=stage, is_latest=n == versions, page_count=3, summary="sum",
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=hours + n),
            meta={"authors": list(authors), "year": year, "venue": venue},
        )  # fmt: skip
        db.add(v)
        out.append(v)
    db.flush()
    return d, out[-1]


class FakeES:
    def __init__(self, hits=()):
        self.hits, self.calls = list(hits), []

    def search(self, **kw):
        self.calls.append(kw)
        return {"hits": {"hits": self.hits}}
