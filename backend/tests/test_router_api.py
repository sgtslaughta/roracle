"""P13 T7: router wiring in the chat SSE stream and MCP `ask` (fake ES/LLM, real Postgres)."""

import json
import time
from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from mcp_helpers import call
from test_chat_stream import chunk, hit, sse

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import get_app_settings
from roracle.chat import global_answer as ga

pytestmark = pytest.mark.usefixtures("_blob_store_override")
GLOBAL_Q = "What are the main themes across all the documents?"


class RouterLLM:
    def __init__(self, session=None, delay=0.0):
        self.maps, self.reduces, self.in_tx, self.session, self.delay = [], [], [], session, delay
        self.embeddings = SimpleNamespace(
            create=lambda model, input: SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=[0.1] * 768) for i in range(len(input))]
            )
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        if kw.get("stream"):
            self.reduces.append(kw)
            return iter([chunk("Themes: [1] and [2]."), chunk("")])
        if kw["messages"][0]["content"].startswith("You read ONE document"):
            self.maps.append(kw)
            self.in_tx.append(self.session.in_transaction() if self.session else None)
            time.sleep(self.delay)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="SECTION: 0\nfinding"))]
            )
        self.reduces.append(kw)  # MCP ask: non-streaming reduce
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Themes: [1]."))])


@pytest.fixture(autouse=True)
def fresh_slots(monkeypatch):
    monkeypatch.setattr(ga, "SLOTS", ga.Slots())


@pytest.fixture
def setup(app, db_session, settings, ana):
    def make(router=True, n=4, delay=0.0):
        llm = RouterLLM(db_session, delay)
        docs = [seed(db_session, ana, title=f"Doc{i}") for i in range(n)]
        hits = [hit(d, v, text=f"alpha {d.title}", chunk_no=0) for d, v in docs]
        app.dependency_overrides[get_es] = lambda: FakeES(hits)
        app.dependency_overrides[get_llm] = lambda: llm
        s = settings.model_copy(update={"router": router, "global_timeout_s": 5.0})
        app.dependency_overrides[get_app_settings] = lambda: s
        return llm, docs

    return make


def test_sse_meta_global_route_and_docs_considered(client, as_ana, ana, db_session, setup):
    llm, docs = setup()
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    r = client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": GLOBAL_Q})
    ev = sse(r)
    assert ev[0] == ("meta", {"route": "global", "global": {"docs_considered": 4}})  # meta is the FIRST frame
    assert [e for e, _ in ev].count("progress") >= 1 and ev[1][0] == "progress"
    assert llm.in_tx == [False] * 4  # C1: no DB transaction/connection is held while the map calls run
    assert ga.SLOTS.users == {} and ga.SLOTS.used == 0  # admission released at the end of the stream
    cites = next(d for e, d in ev if e == "citations")["items"]
    assert len(llm.maps) == 4 and len(llm.reduces) == 1
    assert llm.reduces[0]["max_tokens"] == 1200
    assert [c["n"] for c in cites] == [1, 2] and all(c["chunk_id"] and c["page"] == 2 for c in cites)
    assert {c["doc_id"] for c in cites} <= {str(d.id) for d, _ in docs}
    assert ev[-1][0] == "done"


def test_sse_fact_question_reports_route_without_global(client, as_ana, ana, db_session, setup):
    llm, _ = setup()
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "What is alpha?"}))
    assert ev[0] == ("meta", {"route": "fact"}) and llm.maps == []


def test_router_off_has_no_meta_and_no_router_call(client, as_ana, ana, db_session, setup):
    llm, _ = setup(router=False)
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": GLOBAL_Q}))
    assert ev[0][0] != "meta" and llm.maps == []


def test_global_with_too_few_docs_falls_back_to_fact(client, as_ana, ana, db_session, setup):
    llm, _ = setup(n=2)
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": GLOBAL_Q}))
    assert ev[0] == ("meta", {"route": "fact"}) and llm.maps == []


def test_mcp_ask_returns_route(app, as_ana, ana, db_session, setup):
    llm, _ = setup()
    with TestClient(app) as mc:
        err, out = call(mc, as_ana, "ask", question=GLOBAL_Q)
        assert not err and out["route"] == "global" and out["answer"] == "Themes: [1]."
        assert out["citations"][0]["chunk_id"]
        err, out = call(mc, as_ana, "ask", question="What is alpha?")
        assert not err and out["route"] == "fact"
    assert json.dumps(llm.reduces[0]["messages"]).count("alpha Doc") == 0  # reduce sees findings, not chunks


def test_busy_user_gets_fact_path_with_reason(client, as_ana, ana, db_session, setup):
    llm, _ = setup()
    ga.SLOTS.users[str(ana)] = time.monotonic() + 60  # this user already has a global question in flight
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": GLOBAL_Q}))
    assert ev[0] == ("meta", {"route": "fact", "global": {"skipped": "busy"}}) and llm.maps == []


def test_chat_excludes_and_ignored_are_honoured(client, as_ana, ana, db_session, setup):
    llm, docs = setup(n=6)
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    gone = [str(docs[0][0].id), str(docs[1][0].id)]
    client.patch(f"/chats/{cid}", headers=as_ana, json={"excludes": [gone[0]]})
    r = client.post(
        f"/chats/{cid}/messages", headers=as_ana,
        json={"content": GLOBAL_Q, "viewer": {"ignored": [gone[1]]}},
    )  # fmt: skip
    ev = sse(r)
    assert ev[0] == ("meta", {"route": "global", "global": {"docs_considered": 4}})
    sent = json.dumps([kw["messages"] for kw in llm.maps])
    assert "Doc0" not in sent and "Doc1" not in sent


def test_disconnect_before_first_frame_releases_admission_and_saves_the_turn(
    client, as_ana, ana, db_session, setup, settings
):
    from roracle.api.routes import chats
    from roracle.auth import Principal

    llm, _ = setup()
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    p = Principal(ana, "ana", "ana", (), False)
    s = settings.model_copy(update={"router": True})
    r = chats.post_message(
        __import__("uuid").UUID(cid), chats.MessageIn(content=GLOBAL_Q), p, db_session, s, FakeES([]), llm
    )
    assert ga.SLOTS.users == {}  # no hits: fact path, nothing held
    llm2, _ = setup()
    r = chats.post_message(
        __import__("uuid").UUID(cid),
        chats.MessageIn(content=GLOBAL_Q),
        p,
        db_session,
        s,
        FakeES(hits_of(db_session)),
        llm2,
    )
    assert str(ana) in ga.SLOTS.users  # admitted, stream not started

    async def gone(_msg):
        raise OSError("client disconnected")

    with pytest.raises(OSError):
        anyio_run(r.stream_response(gone))
    assert ga.SLOTS.users == {} and llm2.maps == []  # closed before any work: cleanup ran, no LLM spent
    from sqlalchemy import select

    from roracle.models import ChatMessage

    assert db_session.scalars(select(ChatMessage.status).where(ChatMessage.role == "assistant")).all() == [
        "interrupted"
    ]


def hits_of(db):
    from sqlalchemy import select

    from roracle.models import Document, DocumentVersion

    return [
        hit(d, v, text=f"alpha {d.title}")
        for d, v in db.execute(
            select(Document, DocumentVersion).join(
                DocumentVersion, DocumentVersion.document_id == Document.id
            )
        )
    ]


def anyio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_time_to_first_sse_byte_is_independent_of_map_time(client, as_ana, ana, db_session, setup, settings):
    """Perf: meta is emitted after candidates only (no LLM), the map (0.25 s per call here) runs afterwards."""
    import asyncio
    import statistics

    from roracle.api.routes import chats
    from roracle.auth import Principal

    llm, _ = setup(n=6, delay=0.25)
    p = Principal(ana, "ana", "ana", (), False)
    s = settings.model_copy(update={"router": True, "global_timeout_s": 5.0})
    es = FakeES(hits_of(db_session))
    ts = []
    for _ in range(30):
        cid = client.post("/chats", headers=as_ana, json={}).json()["id"]

        async def first(cid=cid):
            t0 = time.perf_counter()
            r = chats.post_message(
                __import__("uuid").UUID(cid), chats.MessageIn(content=GLOBAL_Q), p, db_session, s, es, llm
            )
            f = await r.body_iterator.__anext__()
            dt = time.perf_counter() - t0
            await r.body_iterator.aclose()
            return dt, f

        dt, f = asyncio.run(first())
        assert f.startswith("event: meta")
        ts.append(dt * 1000)
    ts.sort()
    print(
        f"global time-to-first-SSE-byte (fakes, map 250 ms/call): p50={statistics.median(ts):.1f} ms p95={ts[28]:.1f} ms"
    )
    assert ts[28] < 200  # far below one map call (250 ms): the first byte never waits for the map


def test_mcp_global_budget_is_capped(app, as_ana, ana, db_session, setup, monkeypatch):
    setup()
    seen = []
    real = ga.map_docs
    monkeypatch.setattr(ga, "map_docs", lambda *a, **k: (seen.append(k.get("timeout")), real(*a, **k))[1])
    with TestClient(app) as mc:
        err, out = call(mc, as_ana, "ask", question=GLOBAL_Q)
    assert (
        not err and out["route"] == "global" and seen == [5.0]
    )  # min(global_timeout_s=5, global_mcp_budget_s=45)
