"""R16/R5 leak regressions (polish T1): dup check, near-dup, handoff, set-title, vote, notifications."""

import uuid

import pytest
from api_seed import seed
from sqlalchemy import update

from roracle import notify
from roracle.auth import Principal
from roracle.ingest.neardup import find_near_duplicate, minhash
from roracle.models import Blob, Document, DocumentVersion, User

TEXT = " ".join(f"word{i}" for i in range(200))


def _up(client, headers, data):
    return client.post("/documents", headers=headers, files={"file": ("paper.pdf", data, "application/pdf")})


@pytest.mark.usefixtures("_blob_store_override")
def test_dup_check_ignores_rejected_doc(client, as_ana, as_bob, db_session):
    first = _up(client, as_ana, b"%PDF-malware").json()
    db_session.execute(update(DocumentVersion).values(stage="rejected"))
    db_session.flush()
    assert client.get(f"/documents/{first['document_id']}", headers=as_bob).status_code == 404
    assert _up(client, as_bob, b"%PDF-malware").status_code == 201


@pytest.mark.usefixtures("_blob_store_override")
def test_dup_check_ignores_in_ingest_doc(client, as_ana, as_bob):
    _up(client, as_ana, b"%PDF-x2")
    assert _up(client, as_bob, b"%PDF-x2").status_code == 201


def _u(db, s):
    u = User(id=uuid.uuid4(), sub=s, username=s, groups=[])
    db.add(u)
    db.flush()
    return u


def _d(db, owner, stage, title):
    sha = uuid.uuid4().hex * 2
    db.add(Blob(sha256=sha, size=1))
    d = Document(owner_id=owner.id, title=title, org_unit="/", visibility="everyone")
    db.add(d)
    db.flush()
    v = DocumentVersion(
        document_id=d.id, version=1, blob_sha256=sha, filename="f", content_type="x", size=1,
        stage=stage, minhash=minhash(TEXT),
    )  # fmt: skip
    db.add(v)
    db.flush()
    return d, v


def test_neardup_skips_unsearchable_foreign_doc(db_session):
    ana, bob = _u(db_session, "ana"), _u(db_session, "bob")
    _, hers = _d(db_session, ana, "needs_title", "Ana unpublished")
    _, mine = _d(db_session, bob, "summarized", "Bob")
    p = Principal(bob.id, bob.sub, bob.username, (), False)
    assert find_near_duplicate(db_session, p, mine, 0.85, 0.97) is None
    hers.stage = "searchable"
    db_session.flush()
    assert find_near_duplicate(db_session, p, mine, 0.85, 0.97).id == hers.id


def test_r16_gaps(client, as_ana, as_bob, ana, bob, db_session):
    d, v = seed(db_session, ana, title="Ana in-ingest", stage="needs_title")
    pub, _ = seed(db_session, ana, title="Public")
    db_session.commit()
    r = client.post("/handoffs", headers=as_bob, json={"kind": "document", "source_id": str(d.id)})
    assert r.status_code == 404 or r.json()["doc_ids"] == []
    r = client.put(f"/documents/{d.id}/versions/{v.id}/title", headers=as_bob, json={"title": "x"})
    assert r.status_code == 404
    body = {"value": -1, "reason": "superseded"}
    hidden = client.put(
        f"/documents/{pub.id}/vote", headers=as_bob, json={**body, "superseded_by": str(d.id)}
    )
    nonexist = client.put(
        f"/documents/{pub.id}/vote", headers=as_bob, json={**body, "superseded_by": str(uuid.uuid4())}
    )
    assert (hidden.status_code, hidden.json()) == (nonexist.status_code, nonexist.json())


def test_neardup_notification_title_resolved_at_read(client, as_ana, as_bob, ana, bob, db_session):
    d, _ = seed(db_session, ana, title="Ana secret", stage="needs_title")
    notify.emit(db_session, bob, "upload.near_dup", {"match_document_id": str(d.id), "match_title": "stale"})
    db_session.commit()
    item = client.get("/me/notifications", headers=as_bob).json()["items"][0]
    assert item["payload"]["match_title"] is None
    db_session.execute(
        update(DocumentVersion).where(DocumentVersion.document_id == d.id).values(stage="searchable")
    )
    db_session.commit()
    item = client.get("/me/notifications", headers=as_bob).json()["items"][0]
    assert item["payload"]["match_title"] == "Ana secret"


@pytest.mark.usefixtures("_blob_store_override")
def test_dup_check_per_version_not_doc_wide(client, as_ana, as_bob, ana, db_session):
    d, _ = seed(db_session, ana, title="Ana", versions=1)
    v2 = DocumentVersion(
        document_id=d.id, version=2, blob_sha256=_sha(b"%PDF-v2"), filename="f", content_type="x",
        size=1, stage="needs_title", is_latest=False,
    )  # fmt: skip
    db_session.add(Blob(sha256=v2.blob_sha256, size=1))
    db_session.flush()
    db_session.add(v2)
    db_session.commit()
    r = _up(client, as_bob, b"%PDF-v2")
    assert r.status_code == 201, r.text


def _sha(b):
    import hashlib

    return hashlib.sha256(b).hexdigest()


def test_neardup_notification_nulls_unreadable_id(client, as_ana, as_bob, ana, bob, db_session):
    d, _ = seed(db_session, ana, title="Ana secret", stage="needs_title")
    notify.emit(db_session, bob, "upload.near_dup", {"match_document_id": str(d.id)})
    db_session.commit()
    p = client.get("/me/notifications", headers=as_bob).json()["items"][0]["payload"]
    assert p["match_document_id"] is None and p["match_title"] is None
    db_session.execute(
        update(DocumentVersion).where(DocumentVersion.document_id == d.id).values(stage="searchable")
    )
    db_session.commit()
    p = client.get("/me/notifications", headers=as_bob).json()["items"][0]["payload"]
    assert p["match_document_id"] == str(d.id)
