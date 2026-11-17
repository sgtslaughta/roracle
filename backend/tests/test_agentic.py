"""P13 T8: agentic query decomposition (unit with a spy ranker; chat SSE and MCP wiring with fake ES/LLM)."""

import json
import time
from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from global_helpers import label
from mcp_helpers import call
from test_chat_stream import chunk, hit, sse

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import Principal, get_app_settings
from roracle.chat import agentic
from roracle.config import Settings
from roracle.retrieval import Passage

pytestmark = pytest.mark.usefixtures("_blob_store_override")
Q = "Compare spectral methods and preconditioning for sparse systems"  # heuristic route: multihop


def P(cid, text="t", doc="d"):
    return Passage(
        chunk_id=cid, doc_id=doc, version_id="v", text=text, highlight="", pages=[1], bboxes=[], score=1.0
    )


class Planner:
    """Chat-completion fake for the planner: `replies` is a list (last repeats) of raw message contents."""

    def __init__(self, replies, delay=0.0):
        self.replies, self.delay, self.calls = replies, delay, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        time.sleep(self.delay)
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])


def settings(**kw):
    base = dict(  # noqa: C408
        database_url="x", s3_endpoint="x", s3_access_key="x", s3_secret_key="x", oidc_issuer="x", oidc_jwks_url="x",
        agentic_retrieval=True,
    )  # fmt: skip
    return Settings(**{**base, **kw})


@pytest.fixture
def ranker(monkeypatch):
    calls = []

    def rank(session, es, llm, st, p, question, **kw):
        calls.append((p, question, kw))
        n = len(calls)
        return [P(f"c{n}-{i}", f"text of {question} {i}") for i in range(3)] + [P("shared", "shared text")]

    monkeypatch.setattr(agentic, "rank_for_chat", rank)
    return calls


SESSION = SimpleNamespace(info={})  # a worker-style session: release_before_io is a no-op
PRINCIPAL = Principal(
    user_id="u", sub="s", username="n", groups=(), is_admin=False, via="pat", scopes=frozenset({"read"})
)


def run(llm, st=None, deadline=None):
    return agentic.retrieve(SESSION, None, llm, st or settings(), PRINCIPAL, Q, deadline=deadline)


def test_step_and_subquery_caps(ranker):
    n = [0]

    def many(*_):
        n[0] += 1
        return json.dumps({"subqueries": [f"q{n[0]}-{i}" for i in range(9)], "done": False})

    class Fresh(Planner):
        def _create(self, **kw):
            self.replies = [many()]
            return super()._create(**kw)

    llm = Fresh([""])
    _, subs = run(llm, settings(agentic_max_steps=3, agentic_max_subqueries=4))
    assert len(llm.calls) == 3 and len(subs) == 12  # 3 rounds x at most 4 sub-queries
    assert len(ranker) == 1 + 12  # the question itself + the sub-queries, nothing else


@pytest.mark.parametrize(
    "reply", ["not json at all", '{"subqueries": "x", "done": false}', '{"subqueries": []}', ""]
)
def test_invalid_plan_stops_the_loop(ranker, reply):
    llm = Planner([reply])
    passages, subs = run(llm)
    assert subs == [] and len(ranker) == 1 and len(llm.calls) == 1 and len(passages) == 4


def test_done_stops_and_planner_failure_is_not_an_error(ranker):
    assert run(Planner(['{"subqueries": ["x"], "done": true}']))[1] == []

    class Boom:
        chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(TimeoutError()))
        )

    assert run(Boom())[1] == [] and len(ranker) == 2


def test_every_retrieval_is_the_callers_rank_for_chat_and_results_are_fused_and_deduped(ranker):
    llm = Planner(['{"subqueries": ["alpha", "beta"], "done": false}', '{"subqueries": [], "done": true}'])
    st = settings()
    passages, subs = agentic.retrieve(
        SESSION, "ES", llm, st, PRINCIPAL, Q, viewer=None, scope="collection:c", excludes=["d1"]
    )
    assert [c[1] for c in ranker] == [Q, "alpha", "beta"] and subs == ["alpha", "beta"]
    assert all(c[0] is PRINCIPAL for c in ranker)  # the caller's principal (a PAT here) for every retrieval
    assert all(c[2]["scope"] == "collection:c" and c[2]["excludes"] == ["d1"] for c in ranker)
    ids = [x.chunk_id for x in passages]
    assert len(ids) == len(set(ids)) and ids[0] == "shared"  # in all three lists: best fused rank, once


def test_repeated_subqueries_are_not_run_twice(ranker):
    llm = Planner(['{"subqueries": ["Alpha", "alpha", "' + Q + '"], "done": false}'])
    _, subs = run(llm)
    assert subs == ["Alpha"]  # case-insensitive, and never the question itself


def test_planner_prompt_has_only_the_question_and_gated_passage_text(ranker):
    llm = Planner(['{"subqueries": [], "done": true}'])
    run(llm)
    prompt = json.dumps(llm.calls[0]["messages"])
    assert "text of " + Q in prompt and Q in prompt  # passages that came back from rank_for_chat
    assert llm.calls[0]["timeout"] <= 8.0 and llm.calls[0]["temperature"] == 0


def test_deadline_stops_the_loop_after_the_first_retrieval(ranker):
    llm = Planner(['{"subqueries": ["x"], "done": false}'])
    passages, subs = run(
        llm, deadline=time.monotonic() + 0.2
    )  # less than MIN_STEP_S left: no planner call at all
    assert llm.calls == [] and subs == [] and len(ranker) == 1 and passages


def test_added_latency_is_one_planner_call_plus_the_sub_retrievals_per_round(ranker, monkeypatch):
    llm = Planner(
        ['{"subqueries": ["a", "b"], "done": false}', '{"subqueries": [], "done": true}'], delay=0.05
    )
    t = time.monotonic()
    run(llm)
    assert time.monotonic() - t < 0.5  # 2 planner calls x 50 ms + instant fakes: no hidden waits


def test_admission_is_one_per_user_and_capped():
    agentic.SLOTS.users.clear()
    st = settings(agentic_max_admitted=1)
    a = PRINCIPAL
    b = Principal(user_id="v", sub="s2", username="n2", groups=(), is_admin=False)
    assert agentic.admit(st, a) and not agentic.admit(st, a)  # one loop per user
    assert not agentic.admit(st, b)  # process cap
    agentic.leave(a)
    assert agentic.admit(st, b)
    agentic.leave(b)


# ---- wiring (real Postgres, fake ES/LLM)


class AgentLLM:
    def __init__(self, session, plan='{"subqueries": ["spectral", "preconditioning"], "done": false}'):
        self.session, self.plan, self.planner, self.in_tx, self.streams = session, plan, [], [], []
        self.embeddings = SimpleNamespace(
            create=lambda model, input: SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=[0.1] * 768) for i in range(len(input))]
            )
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        if kw.get("stream"):
            self.streams.append(kw)
            return iter([chunk("Combined [1]."), chunk("")])
        if kw["messages"][0]["content"].startswith("You plan searches"):
            self.planner.append(kw)
            self.in_tx.append(self.session.in_transaction())
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.plan))])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Combined [1]."))])


@pytest.fixture(autouse=True)
def fresh_slots():
    agentic.SLOTS.users.clear()


@pytest.fixture
def setup(app, db_session, settings, ana):
    def make(agentic_on=True, router=False, plan=None):
        llm = AgentLLM(db_session, *([plan] if plan else []))
        docs = [seed(db_session, ana, title=f"Doc{i}") for i in range(3)]
        hits = [hit(d, v, text=f"alpha {d.title}", chunk_no=0) for d, v in docs]
        app.dependency_overrides[get_es] = lambda: FakeES(hits)
        app.dependency_overrides[get_llm] = lambda: llm
        s = settings.model_copy(update={"agentic_retrieval": agentic_on, "router": router})
        app.dependency_overrides[get_app_settings] = lambda: s
        return llm, docs

    return make


def test_flag_off_means_zero_planner_calls_and_no_agentic_meta(client, as_ana, setup):
    llm, _ = setup(agentic_on=False)
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": Q}))
    assert llm.planner == [] and ev[0][0] != "meta" and ev[-1][0] == "done"


def test_chat_stream_meta_first_then_progress_then_subqueries(client, as_ana, db_session, setup):
    from roracle.db import RELEASE_BEFORE_IO

    db_session.info[RELEASE_BEFORE_IO] = True  # as every request/stream session in production
    llm, _docs = setup()
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": Q}))
    assert ev[0] == ("meta", {"agentic": True})  # the very first frame, before any planner call
    kinds = [e for e, _ in ev]
    assert "progress" in kinds and kinds.index("progress") < kinds.index("citations")
    meta2 = [d for e, d in ev if e == "meta"][1]
    assert meta2["subqueries"] == ["spectral", "preconditioning"]
    assert (
        llm.in_tx == [False] * len(llm.planner) and llm.planner
    )  # no DB transaction held across planner calls
    assert agentic.SLOTS.users == {}  # admission released at the end of the stream
    cites = next(d for e, d in ev if e == "citations")["items"]
    assert cites and all(c["chunk_id"] and c["page"] == 2 for c in cites)
    assert ev[-1][0] == "done"


def test_router_multihop_uses_the_loop_and_fact_does_not(client, as_ana, setup):
    llm, _ = setup(router=True)
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": Q}))
    assert ev[0] == ("meta", {"route": "multihop", "agentic": True})
    llm.planner.clear()
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "What is alpha?"}))
    assert ev[0] == ("meta", {"route": "fact"}) and llm.planner == []


def test_busy_takes_the_plain_path(client, as_ana, ana, setup):
    llm, _ = setup()
    agentic.SLOTS.users[str(ana)] = time.monotonic() + 60  # this user already has a loop running
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": Q}))
    assert ev[0] == ("meta", {"agentic": False}) and llm.planner == [] and ev[-1][0] == "done"


def test_exclude_llm_text_never_reaches_the_planner_or_the_answer(
    client, as_ana, ana, db_session, app, setup, settings
):
    llm, docs = setup()
    secret = label(db_session, "NoLLM", exclude_llm=True)
    d, v = seed(db_session, ana, title="Secret")
    d.label_id = secret.id
    db_session.flush()
    es = FakeES(
        [hit(dd, vv, text=f"alpha {dd.title}") for dd, vv in docs] + [hit(d, v, text="TOPSECRET payload")]
    )
    app.dependency_overrides[get_es] = lambda: es  # the (faked) ES returns the gated document too
    cid = client.post("/chats", headers=as_ana, json={}).json()["id"]
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": Q}))
    assert ev[-1][0] == "done" and llm.planner
    assert "TOPSECRET" not in json.dumps(llm.planner) and "TOPSECRET" not in json.dumps(llm.streams)


def test_mcp_ask_reports_subqueries_and_releases(app, as_ana, setup):
    setup()
    with TestClient(app) as mc:
        err, out = call(mc, as_ana, "ask", question=Q)
    assert not err and out["subqueries"] == ["spectral", "preconditioning"] and out["citations"]
    assert agentic.SLOTS.users == {}
