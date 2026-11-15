from datetime import UTC, datetime

import pytest
from conftest import bearer
from ingest_helpers import make_version
from sqlalchemy import select

from roracle.api.routes.documents import get_blob_store
from roracle.api.routes.library import get_es
from roracle.models import (
    AuditLog,
    Connector,
    Document,
    DocumentVersion,
    Job,
    Tag,
    User,
)


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


def job(db_session, kind="ingest", status="failed", payload=None):
    j = Job(kind=kind, status=status, payload=payload or {}, attempts=5, last_error="boom")
    db_session.add(j)
    db_session.commit()
    return j


def test_users_and_groups(client, adm, as_ana, db_session, user, store):
    client.get("/me", headers=as_ana)  # creates ana
    make_version(db_session, store, user)
    r = client.get("/admin/users?q=resear", headers=adm).json()
    assert r["total"] == 1
    it = r["items"][0]
    assert it["username"] == "researcher" and it["documents"] == 1 and it["bytes"] == 1
    assert set(it) == {
        "id", "username", "email", "groups", "is_admin", "created_at", "last_login_at", "documents", "bytes"
    }  # fmt: skip
    assert client.get("/admin/users", headers=adm).json()["total"] >= 2
    groups = {g["group"]: g["members"] for g in client.get("/admin/groups", headers=adm).json()}
    assert groups["/Mathematics/Spectral Theory"] == 1


def test_jobs_list_filter_page_and_retry(client, adm, db_session, user, store):
    v = make_version(db_session, store, user)
    v.ingest_attempts = 5
    j1 = job(db_session, payload={"version_id": str(v.id)})
    j2 = job(db_session, kind="acl_sync", payload={"doc_id": "x"})
    ok = job(db_session, status="done")
    r = client.get("/admin/jobs?limit=2", headers=adm).json()
    assert [i["id"] for i in r["items"]] == [ok.id, j2.id] and r["next_before_id"] == j2.id
    r = client.get(f"/admin/jobs?status=failed&kind=ingest&before_id={ok.id}", headers=adm).json()
    assert r["next_before_id"] is None
    assert r["items"][0]["filename"] == "p.pdf" and r["items"][0]["document_id"] == str(v.document_id)
    assert client.post(f"/admin/jobs/{ok.id}/retry", headers=adm).status_code == 409
    assert client.post("/admin/jobs/9999/retry", headers=adm).status_code == 404
    out = client.post(f"/admin/jobs/{j1.id}/retry", headers=adm).json()
    assert out["status"] == "queued" and out["attempts"] == 0 and out["last_error"] is None
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v.id).ingest_attempts == 0
    assert db_session.get(Job, j2.id).status == "failed"
    assert client.post("/admin/jobs/retry-failed", json={"kind": "acl_sync"}, headers=adm).json() == {
        "retried": 1
    }
    assert client.post("/admin/jobs/retry-failed", headers=adm).json() == {"retried": 0}
    s = client.get("/admin/jobs/summary", headers=adm).json()
    assert s["jobs"]["ingest"] == {"queued": 1, "done": 1} and s["jobs"]["acl_sync"] == {"queued": 1}
    assert s["versions"] == {"uploaded": 1}


def test_review_queue(client, adm, db_session, user, store):
    c = Connector(name="c", kind="fs", owner_id=user.id, visibility="private")
    db_session.add(c)
    db_session.flush()
    orig = make_version(db_session, store, user, title="Original")
    new = make_version(db_session, store, user, connector_id=c.id, name="n.pdf")
    other = make_version(db_session, store, user, name="o.pdf")
    new.stage, other.stage, new.near_dup_of = "review", "review", orig.id
    db_session.commit()
    r = client.get("/admin/review", headers=adm).json()
    assert {i["filename"] for i in r["items"]} == {"n.pdf", "o.pdf"} and r["total"] == 2
    r = client.get("/admin/review?source=connector", headers=adm).json()["items"]
    assert len(r) == 1 and r[0]["connector_id"] == str(c.id) and r[0]["owner"] == "researcher"
    assert r[0]["match"] == {"document_id": str(orig.document_id), "title": "Original"}
    r = client.get("/admin/review?source=upload", headers=adm).json()["items"]
    assert [i["filename"] for i in r] == ["o.pdf"] and r[0]["match"] is None


def test_tag_merge(client, adm, db_session, user, store):
    a, b, c = (make_version(db_session, store, user).document_id for _ in range(3))
    db_session.add_all([Tag(document_id=a, tag="ml"), Tag(document_id=a, tag="machine-learning")])
    db_session.add_all([Tag(document_id=b, tag="ml"), Tag(document_id=c, tag="other")])
    db_session.commit()
    tags = client.get("/admin/tags", headers=adm).json()
    assert tags[0] == {"tag": "ml", "documents": 2}
    r = client.post(
        "/admin/tags/merge", json={"sources": ["ML", "ml "], "target": "Machine Learning"}, headers=adm
    )
    assert r.json() == {"documents": 2}
    db_session.expire_all()
    got = {(t.document_id, t.tag) for t in db_session.scalars(select(Tag))}
    assert got == {(a, "machine-learning"), (b, "machine-learning"), (c, "other")}
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert {j.payload["doc_id"] for j in jobs} == {str(a), str(b)}
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "admin.tags_merge")).one()
    assert row.detail == {"sources": ["ml"], "target": "machine-learning", "documents": 2}
    for body in ({"sources": [], "target": "x"}, {"sources": ["!!"], "target": "x"}):
        assert client.post("/admin/tags/merge", json=body, headers=adm).status_code == 422


def test_audit_filters_and_paging(client, adm, db_session, user):
    for i in range(3):
        db_session.add(AuditLog(user_id=user.id, action=f"doc.view{i}", target="t"))
    db_session.add(AuditLog(action="other_x"))
    db_session.commit()
    r = client.get("/admin/audit?action=doc.&limit=2", headers=adm).json()
    assert [i["action"] for i in r["items"]] == ["doc.view2", "doc.view1"]
    assert r["items"][0]["username"] == "researcher" and r["next_before_id"] == r["items"][1]["id"]
    r2 = client.get(f"/admin/audit?action=doc.&limit=2&before_id={r['next_before_id']}", headers=adm).json()
    assert [i["action"] for i in r2["items"]] == ["doc.view0"] and r2["next_before_id"] is None
    r = client.get(f"/admin/audit?user_id={user.id}&action=doc._", headers=adm).json()
    assert r["items"] == []  # `_` is literal, not a wildcard
    assert client.get("/admin/audit?limit=500", headers=adm).status_code == 422


def test_stats(client, adm, db_session, user, store):
    make_version(db_session, store, user)
    d = make_version(db_session, store, user).document_id
    db_session.get(Document, d).deleted_at = datetime.now(UTC)
    db_session.commit()
    r = client.get("/admin/stats", headers=adm).json()
    assert r["documents"] == {"live": 1, "deleted": 1}
    assert r["versions"] == {"uploaded": 2} and r["blobs"]["count"] == 2
    assert r["users"] == db_session.query(User).count() and r["connectors"] == 0 and r["chats"] == 0
    assert r["top_uploaders"] == [
        {"user_id": str(user.id), "username": "researcher", "documents": 1, "bytes": 1}
    ]


class OkES:
    def ping(self):
        return True


class BadES:
    def ping(self):
        raise ConnectionError("down")


class BadClient:
    def head_bucket(self, **kw):
        raise KeyError("nope")


class BadStore:
    client, bucket = BadClient(), "b"


def test_health_ok_and_degraded(client, adm, app, store, db_session):
    app.dependency_overrides[get_es] = lambda: OkES()
    app.dependency_overrides[get_blob_store] = lambda: store
    job(db_session, kind="connector_tick", status="queued")
    r = client.get("/admin/health", headers=adm).json()
    assert r["status"] == "ok" and r["checks"] == {"db": "ok", "elastic": "ok", "storage": "ok"}
    assert r["queue"]["queued"] == 1 and r["queue"]["failed"] == 0
    assert r["queue"]["oldest_queued_s"] >= 0 and r["scheduler"] == {"tick_queued": True}
    app.dependency_overrides[get_es] = lambda: BadES()
    app.dependency_overrides[get_blob_store] = lambda: BadStore()
    r = client.get("/admin/health", headers=adm).json()
    assert r["status"] == "degraded"
    assert r["checks"]["elastic"] == "error: ConnectionError" and r["checks"]["storage"] == "error: KeyError"
