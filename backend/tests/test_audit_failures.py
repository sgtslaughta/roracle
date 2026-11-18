"""Failed and denied requests keep their audit rows (P15 task 5)."""

import logging
from uuid import uuid4

import pytest
from api_seed import seed
from conftest import bearer
from fastapi.testclient import TestClient
from mcp_helpers import rpc
from sqlalchemy import select

from roracle import audit as audit_mod
from roracle import pats, ratelimit
from roracle.auth import get_app_settings
from roracle.db import get_session
from roracle.metrics import AUDIT_WRITE_FAILURES
from roracle.models import AuditLog

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture(autouse=True)
def _fresh():
    ratelimit.LIMITER._b.clear()
    ratelimit._audited.clear()
    yield
    ratelimit.LIMITER._b.clear()
    ratelimit._audited.clear()


@pytest.fixture
def real(app):
    """Real get_session: commit on success, rollback + replay on failure."""
    app.dependency_overrides.pop(get_session)
    return app


def limit(app, settings, n):
    s = settings.model_copy(update={"rate_limit_per_minute": n})
    app.dependency_overrides[get_app_settings] = lambda: s


def new_pat(db_session, uid, scopes):
    _, token = pats.create(db_session, uid, "t", scopes, 30)
    db_session.commit()
    return token


def rows(db_session, action):
    db_session.rollback()
    return list(db_session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)))


def test_failed_request_keeps_pat_use(real, client, db_session, ana):
    h = bearer(new_pat(db_session, ana, ["read", "write"]))
    r = client.post("/handoffs", json={"kind": "bogus"}, headers=h)
    assert r.status_code == 422
    (row,) = rows(db_session, "pat.use")
    assert row.detail["failed"] == 422 and row.target == "POST /handoffs"
    assert row.user_id is None and row.detail["user_id"] == str(ana)


def test_handler_error_keeps_pat_use(real, client, db_session, ana):
    h = bearer(new_pat(db_session, ana, ["read"]))
    r = client.get(f"/documents/{uuid4()}", headers=h)
    assert r.status_code == 404
    assert rows(db_session, "pat.use")[0].detail["failed"] == 404


def test_successful_request_no_duplicate(real, client, db_session, ana):
    h = bearer(new_pat(db_session, ana, ["read"]))
    assert client.get("/me", headers=h).status_code == 200
    (row,) = rows(db_session, "pat.use")
    assert "failed" not in row.detail and row.user_id == ana


def test_rate_limited_audited_once_per_minute(real, client, db_session, settings, as_ana):
    limit(real, settings, 1)
    codes = [client.get("/me", headers=as_ana).status_code for _ in range(3)]
    assert codes == [200, 429, 429]
    (row,) = rows(db_session, "auth.rate_limited")
    assert row.target == "GET /me" and row.detail["via"] == "jwt" and row.detail["failed"] == 429


def test_scope_denied_audited(real, client, db_session, ana, as_ana):
    doc = seed(db_session, ana, visibility="private")[0]
    read = bearer(new_pat(db_session, ana, ["read"]))
    assert client.post("/handoffs", json={"kind": "agent"}, headers=read).status_code == 403
    d = rows(db_session, "auth.scope_denied")[0]
    assert d.detail["need"] == "write" and d.detail["via"] == "pat" and d.target == "POST /handoffs"
    write = bearer(new_pat(db_session, ana, ["read", "write"]))
    r = client.delete(f"/documents/{doc.id}", headers=write)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"
    m = rows(db_session, "auth.scope_denied")[-1]
    assert m.detail["need"] == "manage" and m.detail["failed"] == 403


def test_admin_denied_still_written(real, client, db_session, as_bob):
    assert client.get("/admin/audit", headers=as_bob).status_code == 403
    (row,) = rows(db_session, "admin.denied")
    assert row.target == "/admin/audit" and row.detail["username"] == "bob"


def test_replay_failure_does_not_change_status(real, client, db_session, ana, monkeypatch, caplog):
    h = bearer(new_pat(db_session, ana, ["read", "write"]))
    real_row = audit_mod.AuditLog

    def boom(**kw):
        if "failed" in kw["detail"]:
            raise RuntimeError("replay insert fails")
        return real_row(**kw)

    monkeypatch.setattr(audit_mod, "AuditLog", boom)
    before = AUDIT_WRITE_FAILURES.labels("replay")._value.get()
    with caplog.at_level(logging.ERROR):
        r = client.post("/handoffs", json={"kind": "bogus"}, headers=h)
    assert r.status_code == 422
    assert AUDIT_WRITE_FAILURES.labels("replay")._value.get() == before + 1
    assert any("durable audit replay failed" in r.getMessage() for r in caplog.records)


def test_no_token_in_audit(real, client, db_session, ana):
    tok = new_pat(db_session, ana, ["read"])
    client.get("/me?q=secret", headers=bearer(tok))
    client.get("/me?q=secret", headers=bearer(tok + "x"))
    all_rows = rows(db_session, "pat.use")
    assert all_rows
    for row in db_session.scalars(select(AuditLog)):
        assert tok not in f"{row.target}{row.detail}" and "q=secret" not in f"{row.target}{row.detail}"


def test_audit_due_expires():
    assert ratelimit.audit_due("k", now=1000.0) is True
    assert ratelimit.audit_due("k", now=1030.0) is False
    assert ratelimit.audit_due("k", now=1060.0) is True


def test_admin_row_has_param_names_only(real, client, db_session, as_ana, make_token):
    admin = bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))
    assert client.get("/admin/audit?q=secret", headers=admin).status_code == 200
    row = rows(db_session, "admin.list_audit")[0]
    assert row.detail == {"params": ["q"]} and "secret" not in str(row.detail)


def test_failure_status_map():
    from sqlalchemy.exc import DataError

    from roracle.db import failure_status
    from roracle.ingest.embed import EmbedUnavailable
    from roracle.labels.policy import ExportBlocked

    assert failure_status(DataError("s", {}, Exception())) == 422
    assert failure_status(EmbedUnavailable("x")) == 503
    assert failure_status(ExportBlocked("L")) == 403
    assert failure_status(RuntimeError()) == 500


def test_mcp_scope_denied_replayed(app, db_session, ana, as_ana):
    from mcp_helpers import call

    with TestClient(app) as mc:
        err, _ = call(mc, as_ana, "create_handoff", title="t", brief="b", audience="authenticated")
    assert err
    (row,) = rows(db_session, "auth.scope_denied")
    assert row.detail["need"] == "manage" and row.detail["via"] == "mcp" and row.detail["failed"] == 403


def test_mcp_429_audited(app, db_session, settings, ana):
    limit(app, settings, 1)
    h = bearer(new_pat(db_session, ana, ["read"]))
    with TestClient(app) as mc:
        codes = [rpc(mc, h, "tools/list").status_code for _ in range(3)]
    assert codes[0] == 200 and codes[1:] == [429, 429]
    (row,) = rows(db_session, "mcp.rate_limited")
    assert row.target == "/mcp" and row.user_id == ana
