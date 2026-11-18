import time

import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from mcp_helpers import call, rpc
from sqlalchemy import text
from test_chat_stream import FakeStreamLLM, hit, new_chat
from test_mcp_tools import B64, pat
from test_search_api import FakeLLM, ready  # noqa: F401

from roracle import onboarding, pats, ratelimit
from roracle.api.routes.chats import get_llm as chat_llm
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import get_app_settings

U = "/me/onboarding"
pytestmark = pytest.mark.usefixtures("_blob_store_override")


def steps(client, h):
    return client.get(U, headers=h).json()["steps"]


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    app.dependency_overrides[chat_llm] = lambda: FakeStreamLLM()
    return f


@pytest.fixture
def mc(app):
    with TestClient(app) as c:
        yield c


def test_get_default(client, as_ana):
    assert client.get(U, headers=as_ana).json() == {"steps": [], "dismissed": False, "pats_enabled": True}


def test_put_dismiss_and_reopen(client, as_ana, as_bob):
    r = client.put(U, json={"dismissed": True}, headers=as_ana)
    assert r.status_code == 200 and r.json()["dismissed"] is True
    assert client.get(U, headers=as_ana).json()["dismissed"] is True
    assert client.get(U, headers=as_bob).json()["dismissed"] is False
    assert client.put(U, json={"dismissed": False}, headers=as_ana).json()["dismissed"] is False
    assert client.put(U, json={}, headers=as_ana).status_code == 422


def test_pats_disabled_flag(client, app, settings, as_ana):
    app.dependency_overrides[get_app_settings] = lambda: settings.model_copy(update={"pats_enabled": False})
    assert client.get(U, headers=as_ana).json()["pats_enabled"] is False


def test_search_ticks_only_with_query(client, as_ana, ana, db_session, fes):
    d, v = seed(db_session, ana, title="Alpha")
    fes.hits = [hit(d, v)]
    client.get("/search", headers=as_ana, params={"q": "alpha", "mode": "quick"})
    client.get("/search", headers=as_ana, params={"q": "  "})
    assert steps(client, as_ana) == []
    client.get("/search", headers=as_ana, params={"q": "alpha"})
    assert steps(client, as_ana) == ["search"]


def test_chat_message_ticks_ask(client, as_ana, ana, db_session, fes):
    cid = new_chat(client, as_ana)
    assert steps(client, as_ana) == []
    r = client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "what is alpha?"})
    assert r.status_code == 200
    assert steps(client, as_ana) == ["ask"]


def test_upload_ticks_upload(client, as_ana, ana):
    r = client.post("/documents", headers=as_ana, files={"file": ("a.pdf", b"a", "application/pdf")})
    assert r.status_code == 201
    assert steps(client, as_ana) == ["upload"]


def test_mcp_upload_does_not_tick_upload(mc, ana, db_session, fes):
    h = pat(db_session, ana, ["write"])
    assert not call(mc, h, "upload", filename="a.pdf", content_base64=B64)[0]
    assert "upload" not in onboarding.state(db_session, ana)["steps"]  # agents never tick human steps


def test_collection_ticks_collect(client, as_ana):
    assert client.post("/collections", json={"name": "c"}, headers=as_ana).status_code in (200, 201)
    assert steps(client, as_ana) == ["collect"]


def test_token_ticks_token(client, as_ana):
    assert client.post("/me/tokens", json={"name": "t"}, headers=as_ana).status_code == 201
    assert steps(client, as_ana) == ["token"]


def test_mcp_pat_call_ticks_mcp_but_jwt_does_not(mc, client, as_ana, ana, db_session, fes):
    rpc(mc, as_ana, "tools/list")
    assert onboarding.state(db_session, ana)["steps"] == []
    rpc(mc, pat(db_session, ana, ["read"]), "tools/list")
    assert onboarding.state(db_session, ana)["steps"] == ["mcp"]


def test_handoff_ticks_handoff(client, as_ana):
    assert client.post("/handoffs", json={"kind": "agent"}, headers=as_ana).status_code == 201
    assert steps(client, as_ana) == ["handoff"]


def test_events_do_not_tick_other_users(client, as_ana, as_bob):
    client.post("/collections", json={"name": "c"}, headers=as_ana)
    assert steps(client, as_ana) == ["collect"]
    assert steps(client, as_bob) == []


def test_ranked_search_ticks_and_bad_query_does_not(client, as_ana, ana, db_session, ready):  # noqa: F811
    d, v = seed(db_session, ana, title="Alpha")
    ready.hits = [hit(d, v)]
    assert client.get("/search", headers=as_ana, params={"q": "alpha"}).status_code == 200
    assert steps(client, as_ana) == ["search"]
    db_session.execute(text("DELETE FROM onboarding_progress"))
    db_session.commit()
    onboarding.reset_cache()
    assert client.get("/search", headers=as_ana, params={"q": "-only"}).status_code == 422
    assert steps(client, as_ana) == []


def test_rate_limited_mcp_does_not_tick(app, settings, ana, db_session, fes):
    from test_ratelimit import limited

    limited(app, settings, 1)
    rec, token = pats.create(db_session, ana, "t", ["read"], 30)
    db_session.commit()
    ratelimit.LIMITER.take(f"pat:{rec.id}", time.monotonic(), 1)  # drain the bucket
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app) as m:
        r = m.post("/mcp", headers=h, content='{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    assert r.status_code == 429
    assert onboarding.state(db_session, ana)["steps"] == []


def test_duplicate_upload_409_does_not_tick(client, as_ana, ana, db_session):
    f = {"file": ("a.pdf", b"a", "application/pdf")}
    assert client.post("/documents", headers=as_ana, files=f).status_code == 201
    db_session.execute(text("DELETE FROM onboarding_progress"))
    db_session.commit()
    onboarding.reset_cache()
    assert client.post("/documents", headers=as_ana, files=f).status_code == 409
    assert steps(client, as_ana) == []


def test_foreign_chat_404_does_not_tick_ask(client, as_ana, as_bob, fes):
    cid = new_chat(client, as_bob)
    r = client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "hi"})
    assert r.status_code == 404
    assert steps(client, as_ana) == []


def test_read_only_pat_cannot_put_onboarding(client, ana, db_session):
    h = pat(db_session, ana, ["read"])
    assert client.put(U, json={"dismissed": True}, headers=h).status_code == 403


def test_write_pat_cannot_put_onboarding(client, ana, db_session):
    h = pat(db_session, ana, ["read", "write"])
    assert client.put(U, json={"dismissed": True}, headers=h).status_code == 403
    assert onboarding.state(db_session, ana)["dismissed"] is False


def test_agent_pat_does_not_tick_human_steps(client, ana, db_session, fes):
    h = pat(db_session, ana, ["read", "write"])
    d, v = seed(db_session, ana, title="Alpha")
    fes.hits = [hit(d, v)]
    assert client.get("/search", headers=h, params={"q": "alpha"}).status_code == 200
    assert client.post("/collections", json={"name": "c"}, headers=h).status_code in (200, 201)
    assert onboarding.state(db_session, ana)["steps"] == []


def test_agent_handoff_does_not_tick_handoff(client, ana, db_session):
    h = pat(db_session, ana, ["write"])
    assert client.post("/handoffs", json={"kind": "agent"}, headers=h).status_code == 201
    assert onboarding.state(db_session, ana)["steps"] == []
