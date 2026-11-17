"""P13 T7: router wiring in the chat SSE stream and MCP `ask` (fake ES/LLM, real Postgres)."""

import json
from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from mcp_helpers import call
from test_chat_stream import chunk, hit, sse

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import get_app_settings

pytestmark = pytest.mark.usefixtures("_blob_store_override")
GLOBAL_Q = "What are the main themes across all the documents?"


class RouterLLM:
    def __init__(self):
        self.maps, self.reduces = [], []
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
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="SECTION: 0\nfinding"))]
            )
        self.reduces.append(kw)  # MCP ask: non-streaming reduce
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Themes: [1]."))])


@pytest.fixture
def setup(app, db_session, settings, ana):
    def make(router=True, n=4, scope_hits=True):
        llm = RouterLLM()
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
    assert ev[0] == ("meta", {"route": "global", "global": {"docs_considered": 4}})
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
