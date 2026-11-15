import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from mcp_helpers import HDRS, call, rpc
from sqlalchemy import select
from test_search_api import FakeLLM, hit

from roracle import pats
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.models import AuditLog, Collection


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    return f


@pytest.fixture
def mc(app):
    with TestClient(app) as c:
        yield c


def test_unauthenticated_401_with_resource_metadata(mc):
    for h in ({}, {"Authorization": "Bearer nope"}):
        r = rpc(mc, h, "tools/list")
        assert r.status_code == 401 and r.json() == {"detail": "invalid token"}
        assert (
            'resource_metadata="http://testserver/.well-known/oauth-protected-resource"'
            in r.headers["www-authenticate"]
        )
    meta = mc.get("/.well-known/oauth-protected-resource").json()
    assert meta["resource"] == "http://testserver/mcp" and meta["bearer_methods_supported"] == ["header"]


def test_tools_list(mc, as_ana):
    r = rpc(mc, as_ana, "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"search", "get_document", "get_passages", "list_collections"} <= names


def test_search_only_visible_and_audited(mc, as_ana, ana, bob, db_session, fes):
    d1, v1 = seed(db_session, ana, title="Mine")
    d2, v2 = seed(db_session, bob, title="Secret", visibility="private")
    fes.hits = [hit(d2, v2), hit(d1, v1)]
    err, out = call(mc, as_ana, "search", query="alpha", limit=5)
    assert not err
    assert [r["doc_id"] for r in out["results"]] == [str(d1.id)]
    assert out["results"][0]["passages"][0]["chunk_id"] == f"{v1.id}:0"
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.search")).one()
    assert row.user_id == ana and row.target.startswith("sha256:")
    assert call(mc, as_ana, "search", query="x", limit=0)[0] is True


def test_get_document_invisible_is_not_found(mc, as_bob, ana, db_session):
    d, _ = seed(db_session, ana, visibility="private")
    err, text = call(mc, as_bob, "get_document", doc_id=str(d.id))
    assert err and "not found" in text
    err, text = call(mc, as_bob, "get_document", doc_id="zzz")
    assert err and "not found" in text


def test_get_document_ok(mc, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, title="T")
    err, out = call(mc, as_ana, "get_document", doc_id=str(d.id))
    assert not err and out["id"] == str(d.id)


def test_get_passages_drops_invisible_and_expands_context(mc, as_ana, ana, bob, db_session, fes):
    d1, v1 = seed(db_session, ana)
    d2, v2 = seed(db_session, bob, visibility="private")
    fes.hits = [hit(d1, v1, "two", 2), hit(d2, v2, "secret", 0), hit(d1, v1, "one", 1)]
    err, out = call(mc, as_ana, "get_passages", chunk_ids=[f"{v1.id}:1", f"{v2.id}:0"], context=1)
    assert not err
    items = out["result"] if isinstance(out, dict) else out
    assert [i["chunk_id"] for i in items] == [f"{v1.id}:1", f"{v1.id}:2"]
    q = fes.calls[-1]["query"]["bool"]["filter"][1]["terms"]["chunk_id"]
    assert f"{v1.id}:0" in q and f"{v1.id}:2" in q
    assert call(mc, as_ana, "get_passages", chunk_ids=["bad"])[0] is True
    assert call(mc, as_ana, "get_passages", chunk_ids=[f"{v1.id}:0"], context=3)[0] is True


def test_list_collections_and_audit_client(mc, as_ana, ana, db_session):
    db_session.add(Collection(owner_id=ana, name="Reading"))
    db_session.flush()
    err, out = call(mc, as_ana, "list_collections")
    assert not err
    items = out["result"] if isinstance(out, dict) else out
    assert [c["name"] for c in items] == ["Reading"]
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.list_collections")).one()


def test_pat_bearer_works_and_is_audited_with_client(mc, as_ana, ana, db_session):
    _, token = pats.create(db_session, ana, "laptop", ["read"], 30)
    db_session.commit()
    h = {"Authorization": f"Bearer {token}"}
    assert call(mc, h, "list_collections")[0] is False
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.list_collections")).one()
    assert row.client == "pat:laptop"


def test_search_inputs_bounded(mc, as_ana, fes):
    err, _ = call(mc, as_ana, "search", query="x" * 1001)
    assert err
    assert call(mc, as_ana, "search", query="x", collection="c" * 201)[0]
    assert call(mc, as_ana, "get_passages", chunk_ids=[f"{'0' * 36}:{i}" for i in range(51)])[0]
    assert call(mc, as_ana, "get_passages", chunk_ids=["a" * 101])[0]
    assert not fes.calls


def test_validator_and_rest_limits(client, as_ana, fes):
    from roracle.api.routes.search import BadInput, validate_search_params

    with pytest.raises(BadInput):
        validate_search_params("q", {"tags": ["t"] * 51}, 10)
    with pytest.raises(BadInput):
        validate_search_params("q", {"tags": ["t" * 201]}, 10)
    with pytest.raises(BadInput):
        validate_search_params("q", {}, 51)
    assert client.get("/search", headers=as_ana, params={"q": "x" * 1001}).status_code == 422
    assert client.get("/search", headers=as_ana, params={"tags": ["t"] * 51}).status_code == 422


def test_body_cap_413(app, settings, as_ana):
    from roracle.api.app import create_app

    small = create_app(settings.model_copy(update={"mcp_max_upload_bytes": 1024}))
    small.dependency_overrides = app.dependency_overrides
    with TestClient(small) as c:
        r = c.post("/mcp", headers={**HDRS, **as_ana}, content=b" " * 100_000)
        assert r.status_code == 413


def test_non_post_unauthenticated_401(mc):
    assert mc.get("/mcp").status_code == 401
    assert mc.delete("/mcp").status_code == 401


def test_acl_filter_in_es_query(mc, as_ana, ana, db_session, fes):
    call(mc, as_ana, "search", query="alpha")
    flt = fes.calls[0]["query"]["bool"]["filter"]
    assert any("bool" in f and "should" in f["bool"] for f in flt)


def test_failed_call_is_audited(mc, as_bob, ana, db_session):
    d, _ = seed(db_session, ana, visibility="private")
    assert call(mc, as_bob, "get_document", doc_id=str(d.id))[0]
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.get_document")).one()
    assert row.detail == {"error": True}


def test_non_owner_cannot_fetch_unfinished_version(mc, as_ana, as_bob, bob, db_session, fes):
    d, v = seed(db_session, bob, visibility="everyone", stage="embedded")
    fes.hits = [hit(d, v)]
    err, out = call(mc, as_ana, "get_passages", chunk_ids=[f"{v.id}:0"])
    assert not err and (out["result"] if isinstance(out, dict) else out) == []
    err, out = call(mc, as_bob, "get_passages", chunk_ids=[f"{v.id}:0"])  # owner positive control
    assert not err and [i["chunk_id"] for i in (out["result"] if isinstance(out, dict) else out)] == [
        f"{v.id}:0"
    ]
