from datetime import UTC, datetime

import pytest
from conftest import bearer
from ingest_helpers import make_version
from sqlalchemy import select

from roracle.api.routes.documents import get_blob_store
from roracle.api.routes.library import get_es
from roracle.models import AuditLog, Connector, Document, DocumentVersion, Job


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


def _job(db_session, kind, status, payload):
    j = Job(kind=kind, status=status, payload=payload, attempts=5, last_error="boom")
    db_session.add(j)
    db_session.commit()
    return j


def _conn(db_session, user):
    c = Connector(name="c", kind="fs", owner_id=user.id, visibility="everyone")
    db_session.add(c)
    db_session.commit()
    return c


def test_admin_pat_forbidden(client, adm):
    pat = client.post("/me/tokens", json={"name": "x", "scopes": ["read"]}, headers=adm).json()
    assert client.get("/admin/stats", headers=bearer(pat["token"])).status_code == 403


def test_one_audit_row_per_admin_get(client, adm, db_session):
    assert client.get("/admin/groups", headers=adm).status_code == 200
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "admin.list_groups")).all()
    assert len(rows) == 1


def test_retry_connector_run_conflicts_with_active_run(client, adm, db_session, user):
    c = _conn(db_session, user)
    pl = {"connector_id": str(c.id)}
    failed = _job(db_session, "connector_run", "failed", pl)
    active = _job(db_session, "connector_run", "running", pl)
    assert client.post(f"/admin/jobs/{failed.id}/retry", headers=adm).status_code == 409
    assert client.post("/admin/jobs/retry-failed", headers=adm).json() == {"retried": 0}
    active.status = "done"
    db_session.commit()
    assert client.post(f"/admin/jobs/{failed.id}/retry", headers=adm).status_code == 200


def test_retry_failed_resets_ingest_attempts(client, adm, db_session, user, store):
    v = make_version(db_session, store, user)
    v.ingest_attempts = 5
    _job(db_session, "ingest", "failed", {"version_id": str(v.id)})
    assert client.post("/admin/jobs/retry-failed", headers=adm).json() == {"retried": 1}
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v.id).ingest_attempts == 0


def test_review_all_paging_and_soft_delete(client, adm, db_session, user, store):
    vs = [make_version(db_session, store, user, name=f"{i}.pdf") for i in range(3)]
    for v in vs:
        v.stage = "review"
    db_session.get(Document, vs[2].document_id).deleted_at = datetime.now(UTC)
    db_session.commit()
    r = client.get("/admin/review?source=all&limit=1&page=2", headers=adm).json()
    assert r["total"] == 2 and [i["filename"] for i in r["items"]] == ["1.pdf"]


def test_rejected_versions_excluded_from_bytes(client, adm, db_session, user, store):
    make_version(db_session, store, user)
    v2 = make_version(db_session, store, user, name="r.pdf")
    v2.stage = "rejected"
    db_session.commit()
    top = client.get("/admin/stats", headers=adm).json()["top_uploaders"]
    assert top[0]["bytes"] == 1
    users = client.get("/admin/users", headers=adm).json()["items"]
    assert next(u for u in users if u["username"] == "researcher")["bytes"] == 1


def test_health_db_failure_degraded(client, adm, app, db_session, monkeypatch):
    class OkES:
        def ping(self):
            return True

    class Store:
        class client:
            @staticmethod
            def head_bucket(**kw):
                return {}

        bucket = "b"

    app.dependency_overrides[get_es] = lambda: OkES()
    app.dependency_overrides[get_blob_store] = lambda: Store()
    from roracle.api.routes import admin_ops

    def bad(fn):
        return "error: OperationalError"

    monkeypatch.setattr(
        admin_ops, "_check", lambda fn: bad(fn) if "SELECT" in repr(fn.__code__.co_consts) else "ok"
    )
    monkeypatch.setattr(admin_ops, "func", type("F", (), {"count": staticmethod(lambda *a: 1 / 0)}))
    r = client.get("/admin/health", headers=adm)
    assert r.status_code == 200
    assert r.json()["status"] == "degraded" and r.json()["queue"] is None and r.json()["scheduler"] is None
