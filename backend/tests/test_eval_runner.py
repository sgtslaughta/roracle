import uuid

import pytest
from api_seed import seed
from sqlalchemy import select
from test_chat_retrieve import FakeES, FakeLLM, hit

from roracle.auth import Principal
from roracle.chat.retrieve import Viewer, rank_for_chat, retrieve_for_chat
from roracle.config import Settings
from roracle.eval import gold, runner
from roracle.models import Document, User
from roracle.models_eval import EvalResult, EvalRun
from roracle.models_labels import Label
from roracle.retrieval import Passage

SETTINGS = Settings()


def mkuser(db, name, admin=False):
    u = User(id=uuid.uuid4(), sub=name, username=name, is_admin=admin)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def world(db_session):
    adm, ana = mkuser(db_session, "adm", True), mkuser(db_session, "ana")
    pub, _ = seed(db_session, adm.id, title="Pub", visibility="everyone")
    priv, _ = seed(db_session, adm.id, title="Priv", visibility="private")
    sec, _ = seed(db_session, adm.id, title="Sec", visibility="everyone")
    agt, _ = seed(db_session, adm.id, title="Agt", visibility="everyone")
    sec.label_id = db_session.scalar(
        Label.__table__.insert()
        .values(name="S", colour="#111111", rank=1, exclude_llm=True)
        .returning(Label.id)
    )
    agt.label_id = db_session.scalar(
        Label.__table__.insert()
        .values(name="A", colour="#222222", rank=2, block_agents=True)
        .returning(Label.id)
    )
    db_session.flush()
    return {"adm": adm, "ana": ana, "pub": pub, "priv": priv, "sec": sec, "agt": agt}


def passage(doc, text="alpha", chunk="c1"):
    return Passage(f"{chunk}-{doc.id}", str(doc.id), "v", text, "", [1], [], 1.0)


def gs(w, docs, as_pat=False, who=("ana",), quote="alpha"):
    q = gold.GoldQuery(
        key="q",
        text="alpha?",
        kind="fact",
        principals=[str(w[x].id) for x in who],
        as_pat=as_pat,
        relevant=[gold.GoldItem(doc=str(w[d].id), quote=quote, grade=3) for d in docs],
    )
    return gold.GoldSet(name="s", queries=[q])


def run(db, w, g, ranked, path="chat"):
    """ranked: {principal user id: [Passage]}; the fake rank callable replaces ES."""
    rid = runner.run_set(
        db,
        None,
        None,
        SETTINGS,
        g,
        path=path,
        trigger="ci",
        rank=lambda p, q, raw: raw.extend(ranked(p)) or ranked(p),
    )
    db.expire_all()
    return db.get(EvalRun, rid), {
        r.principal: r for r in db.scalars(select(EvalResult).where(EvalResult.run_id == rid))
    }


def test_expected_set_shrinks_to_visible_docs(db_session, world, settings):
    w = world
    g = gs(w, ["pub", "priv"])
    run_, res = run(db_session, w, g, lambda p: [passage(w["pub"])])
    r = res[str(w["ana"].id)]
    assert r.metrics["p_recall@5"] == 1.0  # priv is not in ana's expected set
    assert run_.status == "done" and run_.metrics["acl_violations"] == 0


def test_hit_on_invisible_doc_fails_run(db_session, world):
    w = world
    run_, _ = run(db_session, w, gs(w, ["pub"]), lambda p: [passage(w["priv"]), passage(w["pub"])])
    assert run_.status == "failed" and run_.metrics["acl_violations"] == 1
    assert run_.metrics["es_acl_violations"] == 1 and "acl_violations: 1" in run_.error


def test_es_leak_filtered_by_pipeline_not_a_run_failure(db_session, world):
    w = world
    rid = runner.run_set(
        db_session,
        None,
        None,
        SETTINGS,
        gs(w, ["pub"]),
        trigger="ci",
        rank=lambda p, q, raw: raw.append(passage(w["priv"])) or [passage(w["pub"])],
    )
    r = db_session.get(EvalRun, rid)
    assert (r.status, r.metrics["acl_violations"], r.metrics["es_acl_violations"]) == ("done", 0, 1)


def test_chat_path_drops_exclude_llm_gold(db_session, world):
    w = world
    g = gs(w, ["sec"])
    _, chat = run(db_session, w, g, lambda p: [], path="chat")
    assert chat[str(w["ana"].id)].metrics is None  # empty R: excluded and counted
    run_, search = run(db_session, w, g, lambda p: [passage(w["sec"])], path="search")
    assert search[str(w["ana"].id)].metrics["p_mrr"] == 1.0 and run_.metrics["empty_pairs"] == 0
    run_, _ = run(db_session, w, g, lambda p: [passage(w["sec"])], path="chat")
    assert run_.status == "failed"  # an exclude_llm doc reaching the chat path is a leak


def test_pat_variant_drops_block_agents_gold(db_session, world):
    w = world
    run_, res = run(
        db_session, w, gs(w, ["agt"], as_pat=True), lambda p: [passage(w["agt"])] if p.via == "jwt" else []
    )
    uid = str(w["ana"].id)
    assert res[uid].metrics["p_mrr"] == 1.0 and res[uid + ":pat"].metrics is None
    assert run_.metrics["empty_pairs"] == 1 and run_.status == "done"


def test_r5_refused_before_run_row(db_session, world):
    w = world
    with pytest.raises(ValueError, match="non-admin"):
        runner.run_set(
            db_session, None, None, SETTINGS, gs(w, ["pub"], who=("adm",)), trigger="ci", rank=lambda *a: []
        )
    assert db_session.scalar(select(EvalRun)) is None


def test_results_store_ids_only(db_session, world):
    w = world
    _, res = run(db_session, w, gs(w, ["pub"]), lambda p: [passage(w["pub"], text="SECRET BODY")])
    row = res[str(w["ana"].id)]
    assert row.ranked == [[f"c1-{w['pub'].id}", str(w["pub"].id)]]
    assert "SECRET" not in str(row.ranked) and row.latency_ms >= 0


def test_rank_for_chat_plus_truncation_equals_retrieve_for_chat(db_session, settings, world):
    w = world
    docs = [seed(db_session, w["adm"].id, title=f"D{i}", visibility="everyone") for i in range(6)]
    es = FakeES([hit(d, v, text=f"alpha {i}") | {"_score": 1} for i, (d, v) in enumerate(docs)])
    for h in es.hits:
        h["_score"] = 1.0
    p = Principal(w["ana"].id, "ana", "ana", (), False)
    ranked = rank_for_chat(db_session, es, FakeLLM(), settings, p, "alpha", depth=50)
    out = retrieve_for_chat(
        db_session, es, FakeLLM(), settings, p, "alpha", Viewer(), "all", [], [], k=4, include_notes=False
    )
    assert [x.chunk_id for x in out] == [x.chunk_id for x in ranked[:4]] and len(ranked) == 6
    assert db_session.scalar(select(Document.id).limit(1)) is not None


def test_cli_override_parsing():
    from roracle.eval.cli import parse_overrides

    assert parse_overrides(["rerank_depth=80", "pats_enabled=true"], "idx") == {
        "embed_index": "idx",
        "rerank_depth": 80,
        "pats_enabled": True,
    }
    for bad in ("rerank_depth=5", "nope=1", "novalue"):
        with pytest.raises(ValueError):
            parse_overrides([bad])


def test_cli_eval_only_overrides():
    from roracle import runtime
    from roracle.eval.cli import parse_overrides

    ov = parse_overrides(["rerank_url=", "rerank_api=tei", "chat_candidates=100"])
    assert ov == {"rerank_url": "", "rerank_api": "tei", "chat_candidates": 100}
    assert parse_overrides(["rerank_url=http://localhost:21480/rerank"]) == {
        "rerank_url": "http://localhost:21480/rerank"
    }
    for bad in ("rerank_api_key=x", "rerank_api=nope", "chat_candidates=5", "chat_candidates=9999"):
        with pytest.raises(ValueError):
            parse_overrides([bad])
    assert "rerank_url" not in runtime.SettingsIn.model_fields  # admin-runtime rerank_url would be SSRF


def test_compare_command_splits_brief_args():
    from roracle.eval.cli import parse_overrides
    from roracle.eval.compare import split_kv

    c = "rerank_url=http://localhost:21480/rerank,rerank_api=tei,rerank_model=bge-reranker-v2-m3"
    assert parse_overrides(split_kv(c))["rerank_api"] == "tei"
    assert parse_overrides(split_kv("rerank_url=")) == {"rerank_url": ""}


def test_pat_search_path_withholds_text_of_gated_docs(db_session, world, monkeypatch):
    """N1: /search returns no passages for exclude_llm / block_agents docs to a PAT; the eval must not score them."""
    from dataclasses import replace

    w = world
    every = [passage(w["pub"]), passage(w["sec"]), passage(w["agt"])]
    monkeypatch.setattr(runner, "rank_for_search", lambda *a, raw=None, **k: list(every))
    rank = runner.default_rank(db_session, None, None, SETTINGS, "search")
    human = Principal(w["ana"].id, "ana", "ana", (), False)
    assert [x.doc_id for x in rank(human, "q", [])] == [x.doc_id for x in every]
    assert [x.doc_id for x in rank(replace(human, via="pat"), "q", [])] == [str(w["pub"].id)]
