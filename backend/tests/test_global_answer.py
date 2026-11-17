"""P13 T7: global map-reduce (fake ES that ignores ACL; Postgres is the authority; fake LLM)."""

import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from api_seed import seed

from roracle.auth import Principal
from roracle.chat import global_answer as ga
from roracle.chat.retrieve import rank_for_chat
from roracle.models import User
from roracle.models_labels import Label
from roracle.retrieval import Passage


class GES:
    """Honours ids / doc_id / no-unit filters (like ES would); ignores ACL on purpose."""

    def __init__(self, hits=()):
        self.hits, self.calls = list(hits), []

    def search(self, **kw):
        self.calls.append(kw)
        if "knn" in kw:
            return {"hits": {"hits": []}}
        flt = kw["query"]["bool"]["filter"]
        ids = next((f["ids"]["values"] for f in flt if "ids" in f), None)
        doc = next((f["term"]["doc_id"] for f in flt if "doc_id" in f.get("term", {})), None)
        nounit = {"bool": {"must_not": [{"exists": {"field": "unit"}}]}} in flt
        out = [
            h for h in self.hits
            if (ids is None or h["_source"]["chunk_id"] in ids)
            and (doc is None or h["_source"]["doc_id"] == doc)
            and not (nounit and h["_source"].get("unit"))
        ]  # fmt: skip
        return {"hits": {"hits": out}}


def mk(doc, ver, key="0", text=None, unit=None, sources=(), pages=(2,)):
    return {
        "_source": {
            "chunk_id": f"{ver.id}:{key}", "doc_id": str(doc.id), "version_id": str(ver.id),
            "text": text or f"chunk {key} of {doc.title}", "pages": list(pages), "bboxes": [],
            "title": doc.title, "authors": ["Ann"], "year": 2020,
            **({"unit": unit, "source_chunks": [f"{ver.id}:{n}" for n in sources]} if unit else {}),
        }
    }  # fmt: skip


class LLM:
    """embeddings + chat; `reply(doc_title, messages)` decides each map answer; tracks concurrency."""

    def __init__(self, reply=None, delay=0.0):
        self.reply = reply or (lambda title, msgs: f"SECTION: d\nFinding about {title}")
        self.delay, self.calls, self.live, self.peak, self.lock = delay, [], 0, 0, threading.Lock()
        self.embeddings = SimpleNamespace(
            create=lambda model, input: SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=[0.1] * 768) for i in range(len(input))]
            )
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        title = kw["messages"][1]["content"].split("\n", 1)[0].removeprefix("Document: ")
        with self.lock:
            self.calls.append(kw)
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            time.sleep(self.delay)
            r = self.reply(title, kw["messages"])
        finally:
            with self.lock:
                self.live -= 1
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])


@pytest.fixture
def me(user):
    return Principal(user.id, "sub-1", "researcher", (), False)


@pytest.fixture
def cfg(settings):
    return settings.model_copy(update={"router": True, "global_timeout_s": 5.0})


def world(db, owner, n=5, **kw):
    docs = [seed(db, owner, title=f"Doc{i}", **kw) for i in range(n)]
    hits = []
    for d, v in docs:
        hits += [mk(d, v, "d", text=f"overview of {d.title}", unit="document", sources=(0, 1)),
                 mk(d, v, "0"), mk(d, v, "1", pages=(7,))]  # fmt: skip
    return docs, hits


def other_user(db) -> uuid.UUID:
    u = User(id=uuid.uuid4(), sub="sub-2", username="o", email="o@example.org")
    db.add(u)
    db.flush()
    return u.id


def label(db, name, **gates):
    lab = Label(name=name, colour="#123456", rank=50 + len(name), **gates)
    db.add(lab)
    db.flush()
    return lab


def test_candidates_exclude_invisible_exclude_llm_and_block_agents(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 3)
    secret = label(db_session, "NoLLM", exclude_llm=True)
    agents = label(db_session, "NoAgent", block_agents=True)
    d_llm, v_llm = seed(db_session, user.id, title="NoLlmDoc")
    d_llm.label_id = secret.id
    d_ag, v_ag = seed(db_session, user.id, title="NoAgentDoc")
    d_ag.label_id = agents.id
    d_priv, v_priv = seed(db_session, other_user(db_session), title="PrivateOther", visibility="private")
    db_session.flush()
    extra = [
        mk(d, v, "d", text="x", unit="document") for d, v in ((d_llm, v_llm), (d_ag, v_ag), (d_priv, v_priv))
    ]
    es = GES(extra + hits)  # ES (faked) returns everything
    got = ga.candidates(db_session, es, LLM(), cfg, me, "q")
    assert {d.title for d in got} == {"Doc0", "Doc1", "Doc2", "NoAgentDoc"}  # jwt: only agent-blocked stays
    pat = Principal(user.id, "sub-1", "researcher", (), False, via="pat")
    got = ga.candidates(db_session, es, LLM(), cfg, pat, "q")
    assert {d.title for d in got} == {"Doc0", "Doc1", "Doc2"}  # PAT: block_agents also gone
    assert all(x.doc_id == d.doc_id for d in got for x in d.units)


def test_candidates_cap_and_only_latest_searchable(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 6)
    stale = docs[0][1]
    stale.is_latest = False
    db_session.flush()
    small = cfg.model_copy(update={"global_max_docs": 4})
    got = ga.candidates(db_session, GES(hits), LLM(), small, me, "q")
    assert len(got) == 4 and docs[0][0].title not in {d.title for d in got}
    for d in got:  # summary units first, then chunks, never another doc's
        assert d.units[0].unit == "document"


def test_map_gets_only_one_docs_own_units_and_none_dropped(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)
    llm = LLM(lambda t, m: "NONE" if t == "Doc1" else f"SECTION: d\nabout {t}")
    cands = ga.candidates(db_session, GES(hits), llm, cfg, me, "q")
    notes = ga.map_docs(llm, cfg, "what?", cands)
    assert [n.text for n in notes] == ["about Doc0", "about Doc2", "about Doc3"]  # NONE dropped, order kept
    assert all(n.section_key.endswith(":d") for n in notes)
    assert len(llm.calls) == 4
    for kw in llm.calls:
        body = kw["messages"][1]["content"]
        mine = body.split("\n", 1)[0].removeprefix("Document: ")
        assert all(f"Doc{i}" not in body for i in range(4) if f"Doc{i}" != mine)
        assert kw["max_tokens"] == cfg.global_map_tokens and kw["temperature"] == 0


def test_fanout_and_concurrency_are_bounded(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 12)
    c = cfg.model_copy(update={"global_max_docs": 9, "global_concurrency": 3})
    llm = LLM(delay=0.05)
    cands = ga.candidates(db_session, GES(hits), llm, c, me, "q")
    notes = ga.map_docs(llm, c, "q", cands)
    assert len(cands) == 9 and len(llm.calls) == 9 and len(notes) == 9
    assert 1 < llm.peak <= 3


def test_timed_out_and_failed_docs_dropped(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)

    def reply(t, m):
        if t == "Doc1":
            time.sleep(0.6)
        return RuntimeError("boom") if t == "Doc2" else f"SECTION: d\nabout {t}"

    c = cfg.model_copy(update={"global_timeout_s": 0.3, "global_concurrency": 4})
    llm = LLM(reply)
    cands = ga.candidates(db_session, GES(hits), llm, c, me, "q")
    t0 = time.perf_counter()
    notes = ga.map_docs(llm, c, "q", cands)
    assert time.perf_counter() - t0 < 0.55
    assert [n.text for n in notes] == ["about Doc0", "about Doc3"]


def test_citation_resolves_to_real_chunk_of_cited_doc_with_pages(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 3)
    llm = LLM()
    es = GES(hits)
    cands = ga.candidates(db_session, es, llm, cfg, me, "alpha doc")
    notes = ga.map_docs(llm, cfg, "alpha doc", cands)
    msgs, passages = ga.build(db_session, es, cfg, me, "alpha doc", notes)
    assert [x.doc_id for x in passages] == [str(d.id) for d, _ in docs]
    for x in passages:
        assert not x.unit and x.pages and x.chunk_id.split(":")[1] in {"0", "1"}  # real chunk, not "d"
        assert x.version_id == x.chunk_id.split(":")[0]
    body = msgs[1]["content"]
    assert all(f'<finding n="{i}">' in body for i in (1, 2, 3)) and msgs[1]["content"].endswith(
        "Question: alpha doc"
    )
    assert "overview of" not in body  # reduce sees the notes, not raw units


def test_build_drops_note_whose_doc_became_invisible_or_unresolvable(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 3)
    es = GES(hits)
    notes = [ga.MapNote(str(d.id), f"n{i}", f"{v.id}:d") for i, (d, v) in enumerate(docs)]
    docs[1][0].visibility = "private"
    docs[1][0].owner_id = other_user(db_session)
    es.hits = [h for h in hits if h["_source"]["doc_id"] != str(docs[2][0].id)]  # no source for doc 2
    _, passages = ga.build(db_session, es, cfg, me, "q", notes)
    assert [x.doc_id for x in passages] == [str(docs[0][0].id)]


def test_fewer_than_three_candidates_fall_back_to_fact(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 2)
    llm = LLM()
    assert ga.make_plan(db_session, GES(hits), llm, cfg, me, "q") is None
    assert not [c for c in llm.calls]  # no map call spent
    plan, meta = ga.route_question(
        db_session, GES(hits), llm, cfg, me, "main themes across all the documents"
    )
    assert plan is None and meta == {"route": "fact"}


def test_route_question_global_meta_and_eligibility(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)
    es, llm = GES(hits), LLM()
    q = "What are the main themes across all the documents?"
    plan, meta = ga.route_question(db_session, es, llm, cfg, me, q)
    assert meta == {"route": "global", "global": {"docs_considered": 4}} and len(plan.passages) == 4
    assert plan.messages[0]["role"] == "system"
    n = len(llm.calls)
    plan, meta = ga.route_question(db_session, es, llm, cfg, me, q, eligible=False)
    assert plan is None and meta == {"route": "fact"} and len(llm.calls) == n
    plan, meta = ga.route_question(db_session, es, llm, cfg, me, "What is the batch size used?")
    assert plan is None and meta == {"route": "fact"}


def test_global_failure_falls_back_to_fact(db_session, cfg, me, user, monkeypatch):
    monkeypatch.setattr(ga, "make_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    plan, meta = ga.route_question(db_session, GES(), LLM(), cfg, me, "main themes across all the documents")
    assert plan is None and meta == {"route": "fact"}


def test_ranked_is_doc_ordered_units(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 3)
    cands = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q")
    r = ga.ranked(cands)
    assert [x.doc_id for x in r][::3] == [c.doc_id for c in cands] and isinstance(r[0], Passage)
    assert rank_for_chat  # fact path untouched
