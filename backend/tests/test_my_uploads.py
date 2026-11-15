from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from roracle.models import Document, DocumentVersion, Job

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def upload(client, db_session):
    """POST /documents; created_at of the new version is set by call order (distinct timestamps)."""
    n = {"i": 0}

    def up(headers, data, filename="paper.pdf"):
        r = client.post("/documents", headers=headers, files={"file": (filename, data, "application/pdf")})
        assert r.status_code == 201, r.text
        body = r.json()
        n["i"] += 1
        db_session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == body["version_id"])
            .values(created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=n["i"]))
        )
        db_session.flush()
        return body

    return up


def test_lists_only_my_uploads_newest_first(client, as_ana, as_bob, upload):
    a = upload(as_ana, b"a", "a.pdf")
    b = upload(as_ana, b"b", "b.pdf")
    upload(as_bob, b"c", "c.pdf")
    r = client.get("/me/uploads", headers=as_ana)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [i["filename"] for i in body["items"]] == ["b.pdf", "a.pdf"]
    top = body["items"][0]
    assert top["document_id"] == b["document_id"] and top["version_id"] == b["version_id"]
    assert top["status"] == "queued" and top["error"] is None and top["size"] == 1
    assert top["title"] == "b.pdf" and top["visibility"] == "everyone"
    assert top["uploaded_at"].startswith("2026-01-01T02:00:00")
    assert a["document_id"] == body["items"][1]["document_id"]


def test_status_reflects_latest_job(client, as_ana, upload, db_session):
    a = upload(as_ana, b"a")
    job = db_session.scalar(select(Job))
    job.status, job.last_error = "failed", "OCR timed out"
    db_session.flush()
    item = client.get("/me/uploads", headers=as_ana).json()["items"][0]
    assert item["version_id"] == a["version_id"]
    assert item["status"] == "failed" and item["error"] == "OCR timed out"


def test_deleted_excluded_and_paging(client, as_ana, upload, db_session):
    upload(as_ana, b"a", "a.pdf")
    upload(as_ana, b"b", "b.pdf")
    c = upload(as_ana, b"c", "c.pdf")
    db_session.execute(
        update(Document).where(Document.id == c["document_id"]).values(deleted_at=datetime.now(UTC))
    )
    db_session.flush()
    body = client.get("/me/uploads?limit=1&offset=1", headers=as_ana).json()
    assert body["total"] == 2 and [i["filename"] for i in body["items"]] == ["a.pdf"]


def test_limit_is_capped_at_200(client, as_ana):
    assert client.get("/me/uploads?limit=1000", headers=as_ana).status_code == 422
