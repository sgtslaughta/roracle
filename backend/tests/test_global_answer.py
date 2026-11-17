# ruff: noqa: F811
"""P13 T7: global candidates, build/citations, routing, eval wiring."""

from types import SimpleNamespace

from api_seed import seed
from global_helpers import GES, LLM, cfg, fresh_slots, label, me, mk, other_user, run_build, world  # noqa: F401

from roracle.auth import Principal
from roracle.chat import global_admit
from roracle.chat import global_answer as ga
from roracle.chat.retrieve import rank_for_chat
from roracle.retrieval import Passage


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


def test_citation_resolves_to_real_chunk_of_cited_doc_with_pages(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 3)
    _, _, msgs, passages = run_build(db_session, GES(hits), cfg, me, LLM())
    assert [x.doc_id for x in passages] == [str(d.id) for d, _ in docs]
    for x in passages:
        assert not x.unit and x.pages and x.chunk_id.split(":")[1] in {"0", "1"}  # real chunk, not "d"
        assert x.version_id == x.chunk_id.split(":")[0]
    body = msgs[1]["content"]
    assert all(f'<finding n="{i}">' in body for i in (1, 2, 3)) and body.endswith("Question: alpha doc")
    assert "overview of" not in body  # reduce sees the notes, not raw units


def test_build_resolution_is_batched_one_es_call(db_session, cfg, me, user):
    """I6: all notes resolve with ONE ES search (was up to 2 per note + an expansion per note)."""
    _, hits = world(db_session, user.id, 8)
    es, llm = GES(hits), LLM()
    cands = ga.candidates(db_session, es, llm, cfg, me, "q")
    notes = ga.map_docs(llm, cfg, "q", cands)
    before = len(es.calls)
    _, passages = ga.build(db_session, es, cfg, me, "q", notes, cands)
    assert len(passages) == 8 and len(es.calls) - before == 1


def test_fallback_citation_is_the_best_ranked_chunk_not_an_arbitrary_one(db_session, cfg, me, user):
    d, v = seed(db_session, user.id, title="Doc")
    hits = [
        mk(d, v, "d", unit="document", sources=(0, 1, 2)),
        mk(d, v, "2", text="the best match"),
        mk(d, v, "0"),
    ]
    cands = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q")
    for key in (None, f"{v.id}:nonsense"):  # the model named no / an unknown section
        _, passages = ga.build(db_session, GES(hits), cfg, me, "q", [ga.MapNote(str(d.id), "n", key)], cands)
        assert [x.chunk_id.split(":")[1] for x in passages] == ["2"]  # first chunk in ES (score) order


def test_named_chunk_is_used_directly(db_session, cfg, me, user):
    d, v = seed(db_session, user.id, title="Doc")
    hits = [mk(d, v, "0"), mk(d, v, "1", pages=(9,))]
    cands = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q")
    es = GES(hits)
    _, ps = ga.build(db_session, es, cfg, me, "q", [ga.MapNote(str(d.id), "n", f"{v.id}:1")], cands)
    assert ps[0].pages == [9] and es.calls == []


def test_build_drops_note_whose_doc_became_invisible_or_unresolvable(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 3)
    cands = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q")
    notes = [ga.MapNote(str(d.id), f"n{i}", f"{v.id}:d") for i, (d, v) in enumerate(docs)]
    docs[1][0].visibility = "private"
    docs[1][0].owner_id = other_user(db_session)
    cands[2].best, cands[2].units = None, [x for x in cands[2].units if x.unit]  # doc 2: no real chunk known
    cands[2].units[0].source_chunks = []
    _, passages = ga.build(db_session, GES(hits), cfg, me, "q", notes, cands)
    assert [x.doc_id for x in passages] == [str(docs[0][0].id)]


def test_none_is_an_exact_match(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)
    reply = {
        "Doc0": "NONE.",
        "Doc1": "Nonetheless, this matters.",
        "Doc2": "None of the participants improved.",
        "Doc3": "none",
    }
    llm = LLM(lambda t, m: f"SECTION: d\n{reply[t]}" if t == "Doc2" else reply[t])
    notes = ga.map_docs(llm, cfg, "q", ga.candidates(db_session, GES(hits), llm, cfg, me, "q"))
    assert [n.text for n in notes] == ["Nonetheless, this matters.", "None of the participants improved."]


def test_fewer_than_three_candidates_fall_back_to_fact(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 2)
    llm = LLM()
    prep, meta = ga.route_question(
        db_session, GES(hits), llm, cfg, me, "main themes across all the documents"
    )
    assert prep is None and meta == {"route": "fact"} and llm.calls == []
    assert global_admit.SLOTS.users == {}  # admission released


def test_route_question_global_meta_and_eligibility(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)
    es, llm = GES(hits), LLM()
    q = "What are the main themes across all the documents?"
    prep, meta = ga.route_question(db_session, es, llm, cfg, me, q)
    assert meta == {"route": "global", "global": {"docs_considered": 4}} and len(prep.docs) == 4
    assert llm.calls == []  # nothing mapped before the stream
    prep.release()
    prep.release()  # idempotent
    for kw in ({"eligible": False}, {}):
        question = q if kw else "What is the batch size used?"
        p2, m2 = ga.route_question(db_session, es, llm, cfg, me, question, **kw)
        assert p2 is None and m2 == {"route": "fact"}


def test_global_failure_falls_back_to_fact(db_session, cfg, me, user, monkeypatch):
    monkeypatch.setattr(ga, "candidates", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    prep, meta = ga.route_question(db_session, GES(), LLM(), cfg, me, "main themes across all the documents")
    assert prep is None and meta == {"route": "fact"} and global_admit.SLOTS.users == {}


def test_ranked_is_doc_ordered_units(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 3)
    cands = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q")
    r = ga.ranked(cands)
    assert [x.doc_id for x in r][::3] == [c.doc_id for c in cands] and isinstance(r[0], Passage)
    assert rank_for_chat  # fact path untouched


# ---- I5 excludes / ignored / pins


def test_dropped_docs_never_reach_candidates_and_pins_come_first(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 5)
    ids = [str(d.id) for d, _ in docs]
    got = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q", dropped={ids[0], ids[3]})
    assert {c.doc_id for c in got} == {ids[1], ids[2], ids[4]}
    got = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q", pins=[ids[4]], dropped={ids[4], ids[0]})
    assert ids[4] not in {c.doc_id for c in got}  # exclude beats pin
    got = ga.candidates(db_session, GES(hits), LLM(), cfg, me, "q", pins=[ids[4]])
    assert got[0].doc_id == ids[4]


def test_route_question_passes_dropped(db_session, cfg, me, user):
    docs, hits = world(db_session, user.id, 5)
    q = "What are the main themes across all the documents?"
    gone = {str(docs[0][0].id), str(docs[1][0].id)}
    prep, _ = ga.route_question(db_session, GES(hits), LLM(), cfg, me, q, dropped=gone)
    assert prep and {c.doc_id for c in prep.docs}.isdisjoint(gone) and len(prep.docs) == 3
    prep.release()


def test_eval_global_route_ranks_candidates_and_records_answer(db_session, cfg, me, user):
    """M2: router on + global question => candidate docs as the ranked list; judged runs record answer + latency."""
    from roracle.eval import runner

    _, hits = world(db_session, user.id, 4)
    es = GES(hits)

    class L(LLM):
        def __init__(self):
            super().__init__()
            base = self.chat.completions.create
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kw: (
                        base(**kw)
                        if "timeout" in kw
                        else SimpleNamespace(
                            choices=[SimpleNamespace(message=SimpleNamespace(content="Answer [1] [2]."))]
                        )
                    )
                )
            )

    llm, answers, raw = L(), {}, []
    q = "What are the main themes across all the documents?"
    rank = runner.default_rank(db_session, es, llm, cfg, "chat", answers)
    found = rank(me, q, raw)
    assert {x.doc_id for x in found} == {c.doc_id for c in answers[(str(me.user_id), me.via, q)]} and raw
    rec = runner._global_answer(db_session, es, llm, cfg, me, q, answers[(str(me.user_id), me.via, q)])
    assert rec["answer"] == "Answer [1] [2]." and rec["docs_cited"] == 4 and rec["latency_ms"] >= 0
    assert all(not x.unit for x in rec["passages"])
    assert rank(me, "What is the batch size used?", []) is not None and len(answers) == 1  # fact: untouched


def test_admitted_question_cap_sends_the_next_user_to_the_fact_path(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)
    c = cfg.model_copy(update={"global_max_admitted": 1})
    q = "What are the main themes across all the documents?"
    first, _ = ga.route_question(db_session, GES(hits), LLM(), c, me, q)
    other = Principal(other_user(db_session), "sub-2", "o", (), False)
    prep, meta = ga.route_question(db_session, GES(hits), LLM(), c, other, q)
    assert first and prep is None and meta == {"route": "fact", "global": {"skipped": "busy"}}
    first.release()


def test_fallback_carries_excluded_by_label_and_drops_the_reduce_cap(db_session, cfg, me, user):
    _, hits = world(db_session, user.id, 4)
    p, _ = ga.route_question(
        db_session, GES(hits), LLM(), cfg, me, "What are the main themes across all the documents?"
    )
    ex = [{"doc_id": "d", "label": "secret"}]
    run = ga.stream_prepare(
        db_session, GES(hits), SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))))),
        cfg, me, "q", p, [], lambda: (["m"], [], {}, [], ex),
    )  # fmt: skip
    g, frames = run(), []
    try:
        while True:
            frames.append(next(g))
    except StopIteration as e:
        out = e.value
    p.release()
    assert out == (["m"], [], {}, None, [], None)
    assert any('"fallback": "error"' in f and '"excluded_by_label"' in f for f in frames)
