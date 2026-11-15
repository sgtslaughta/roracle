import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from ingest_helpers import docling_doc, fake_convert, make_version
from mcp_helpers import rpc
from sqlalchemy import select, text

from roracle import runtime
from roracle.ingest import pipeline
from roracle.models import AppSetting, AuditLog, Document, DocumentVersion


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


def put(client, adm, body):
    return client.put("/admin/settings", json=body, headers=adm)


def test_guard_non_admin_pat_and_anonymous(client, adm, as_ana):
    assert client.get("/admin/settings").status_code == 401
    for method in (client.get, lambda u, **k: client.put(u, json={}, **k)):
        r = method("/admin/settings", headers=as_ana)
        assert r.status_code == 403 and r.json()["detail"] == "admin only"
    pat = client.post("/me/tokens", json={"name": "x", "scopes": ["read", "write"]}, headers=adm).json()
    r = client.get("/admin/settings", headers=bearer(pat["token"]))
    assert r.status_code == 403


def test_every_admin_call_audited(client, adm, db_session):
    assert client.get("/admin/settings?x=1", headers=adm).status_code == 200
    put(client, adm, {"rerank_depth": 20})
    client.get("/admin/needs-title", headers=adm)
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action.like("admin.%"))).all()
    by = {r.action: r for r in rows}
    assert {"admin.get_settings", "admin.put_settings", "admin.needs_title", "admin.settings_update"} <= set(
        by
    )
    assert by["admin.get_settings"].target == "/admin/settings"
    assert by["admin.get_settings"].detail == {"query": {"x": "1"}}
    assert by["admin.settings_update"].detail == {"changes": {"rerank_depth": {"old": 50, "new": 20}}}


def test_get_shape_and_null_resets(client, adm):
    s = client.get("/admin/settings", headers=adm).json()
    assert s["rerank_depth"] == {"value": 50, "source": "env"}
    assert set(s) == set(runtime.KEYS)
    out = put(client, adm, {"rerank_depth": 20}).json()
    assert out["rerank_depth"] == {"value": 20, "source": "override"}
    out = put(client, adm, {"rerank_depth": None}).json()
    assert out["rerank_depth"] == {"value": 50, "source": "env"}


@pytest.mark.parametrize(
    "body",
    [
        {"nope": 1},
        {"rerank_depth": 5},
        {"rerank_depth": "20"},
        {"pats_enabled": 1},
        {"near_dup_threshold": 0.2},
        {"max_upload_bytes": 10},
        {"chat_models": []},
        {"chat_models": ["a", ""]},
        {"chat_default_model": "ghost"},
        {"chat_models": ["a"], "chat_default_model": "b"},
    ],
)
def test_bad_put_is_422(client, adm, body, db_session):
    assert put(client, adm, body).status_code == 422
    assert db_session.scalars(select(AppSetting)).all() == []


def test_chat_models_reflected(client, adm, as_ana):
    put(client, adm, {"chat_models": ["m1", "m2", "m3"], "chat_default_model": "m2"})
    r = client.get("/chats/models", headers=as_ana).json()
    assert r == {"models": ["m2", "m1", "m3"], "default": "m2"}
    # dropping the list override while a default override is stored must not orphan the default
    assert put(client, adm, {"chat_models": None}).status_code == 422
    put(client, adm, {"chat_default_model": None, "chat_models": None})
    assert client.get("/chats/models", headers=as_ana).json()["models"] == ["qwen2.5:14b"]


@pytest.mark.usefixtures("_blob_store_override")
def test_upload_limit_override(client, adm, as_ana):
    up = lambda: client.post(
        "/documents", headers=as_ana, files={"file": ("a.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")}
    )
    assert up().status_code == 201
    put(client, adm, {"max_upload_bytes": 1024 * 1024})
    assert up().status_code == 413


@pytest.mark.usefixtures("_blob_store_override")
def test_upload_quota(client, adm, as_ana, db_session):
    up = lambda data: client.post(
        "/documents", headers=as_ana, files={"file": ("a.pdf", data, "application/pdf")}
    )
    put(client, adm, {"upload_quota_bytes": 10})
    assert up(b"12345678").status_code == 201
    r = up(b"abc")
    assert r.status_code == 413 and r.json()["detail"] == "upload quota exceeded"
    assert up(b"ab").status_code == 201  # exactly at quota
    put(client, adm, {"upload_quota_bytes": 0})
    assert up(b"abcdefghijkl").status_code == 201


def test_pats_disabled_override_blocks_rest_and_mcp(app, client, adm):
    tok = client.post("/me/tokens", json={"name": "x"}, headers=adm).json()["token"]
    assert client.get("/me", headers=bearer(tok)).status_code == 200
    put(client, adm, {"pats_enabled": False})
    assert client.get("/me", headers=bearer(tok)).status_code == 401
    with TestClient(app) as mc:
        assert rpc(mc, bearer(tok), "tools/list").status_code == 401
        assert rpc(mc, adm, "tools/list").status_code == 200
        put(mc, adm, {"pats_enabled": None})
        assert rpc(mc, bearer(tok), "tools/list").status_code == 200


def test_worker_dedupe_uses_override(db_session, session_factory, cfg, store, user, monkeypatch):
    seen = []

    def spy(session, p, v, a, b):
        seen.append((a, b))

    monkeypatch.setattr(pipeline, "find_near_duplicate", spy)
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    monkeypatch.setattr(pipeline, "_index", lambda *a, **k: None)
    db_session.add(AppSetting(key="near_dup_threshold", value=0.91))
    db_session.commit()
    v = make_version(db_session, store, user)
    with session_factory() as s:
        d = s.get(Document, v.document_id)
        pipeline._dedupe(s, s.get(DocumentVersion, v.id), d, "some text here", cfg, session_factory)
    assert seen == [(0.91, cfg.near_dup_summary_threshold)]


def _flatten(routes, prefix=""):
    for r in routes:
        if hasattr(r, "original_router"):  # FastAPI wraps included routers
            yield from _flatten(r.original_router.routes, prefix + r.include_context.prefix)
        elif hasattr(r, "dependant"):
            yield prefix + r.path, r


def _admin_routes(app):
    return [(p, r) for p, r in _flatten(app.routes) if p.startswith("/admin")]


def test_every_admin_route_requires_admin(app):
    from roracle.api.routes.admin_deps import require_admin

    def walk(d):
        for sub in d.dependencies:
            yield sub.call
            yield from walk(sub)

    routes = _admin_routes(app)
    assert routes
    assert len(routes) >= 3
    for path, r in routes:
        assert require_admin in set(walk(r.dependant)), path


def test_every_admin_route_403_for_non_admin(app, client, as_ana):
    import re

    dummy = "00000000-0000-0000-0000-000000000001"
    n = 0
    for path, r in _admin_routes(app):
        url = re.sub(r"\{[^}]+\}", dummy, path)
        for method in r.methods - {"HEAD", "OPTIONS"}:
            resp = client.request(method, url, headers=as_ana, json={})
            assert resp.status_code == 403, (method, path, resp.status_code)
            n += 1
    assert n >= 15


def test_admin_ratings_forbidden_for_non_admin(client, as_ana):
    assert client.get("/admin/ratings", headers=as_ana).status_code == 403


def test_denied_attempt_audited(client, as_ana, db_session):
    assert client.get("/admin/settings", headers=as_ana).status_code == 403
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "admin.denied")).one()
    assert row.target == "/admin/settings"


@pytest.mark.usefixtures("_blob_store_override")
def test_quota_lock_taken_before_sum(client, adm, as_ana, db_session):
    from sqlalchemy import event

    put(client, adm, {"upload_quota_bytes": 100})
    seen = []
    eng = db_session.get_bind()

    def spy(conn, cur, stmt, *a):
        if "pg_advisory_xact_lock" in stmt:
            seen.append("lock")
        elif "sum(" in stmt.lower() and "document_versions" in stmt:
            seen.append("sum")

    event.listen(eng, "before_cursor_execute", spy)
    try:
        r = client.post("/documents", headers=as_ana, files={"file": ("a.pdf", b"abc", "application/pdf")})
    finally:
        event.remove(eng, "before_cursor_execute", spy)
    assert r.status_code == 201 and seen[:2] == ["lock", "sum"]


@pytest.mark.usefixtures("_blob_store_override")
def test_quota_counts_trash_not_rejected_or_cancelled(client, adm, as_ana, db_session):
    up = lambda data: client.post(
        "/documents", headers=as_ana, files={"file": ("a.pdf", data, "application/pdf")}
    )
    put(client, adm, {"upload_quota_bytes": 10})
    did = up(b"12345678").json()
    db_session.execute(text("UPDATE documents SET deleted_at = now()"))
    db_session.flush()
    assert up(b"abc").status_code == 413  # trash still counts
    db_session.execute(text("UPDATE document_versions SET stage = 'rejected'"))
    db_session.flush()
    assert up(b"abc").status_code == 201
    assert did


def test_put_bounds_and_dedupe(client, adm):
    assert put(client, adm, {"rerank_depth": 101}).status_code == 422
    assert put(client, adm, {"upload_quota_bytes": 2**63}).status_code == 422
    out = put(client, adm, {"chat_models": ["a", "b", "a"]}).json()
    assert out["chat_models"]["value"] == ["a", "b"]


def test_invalid_stored_override_ignored(db_session):
    db_session.add_all(
        [AppSetting(key="rerank_depth", value="junk"), AppSetting(key="pats_enabled", value=False)]
    )
    db_session.flush()
    assert runtime.overrides(db_session) == {"pats_enabled": False}
