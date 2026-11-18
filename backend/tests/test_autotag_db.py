"""Auto-tags against Postgres: gate (a), vocabulary scope, apply semantics, adoption, backfill."""

from types import SimpleNamespace

import pytest
from api_seed import seed
from sqlalchemy import select
from test_autotag import FakeLLM

from roracle import autotag
from roracle.models import Job, Tag
from roracle.models_labels import Label

cfg = SimpleNamespace(summary_model="m")


def _tags(db, doc_id):
    return dict(
        db.execute(select(Tag.tag, Tag.source).where(Tag.document_id == doc_id).order_by(Tag.tag)).all()
    )


def _nollm(db):
    lab = Label(name="NoLLM", colour="#aabbcc", rank=3, exclude_llm=True)
    db.add(lab)
    db.flush()
    return lab


def test_vocabulary_only_public_llm_allowed_live_docs(db_session, ana):
    pub, _ = seed(db_session, ana)
    priv, _ = seed(db_session, ana, visibility="private")
    secret, _ = seed(db_session, ana)
    secret.label_id = _nollm(db_session).id
    gone, _ = seed(db_session, ana)
    gone.deleted_at = gone.created_at
    pub2, _ = seed(db_session, ana)
    for d, t in ((pub, "common"), (pub2, "common"), (pub, "rare"), (priv, "private-x"), (secret, "secret-x"),
                 (gone, "gone-x")):  # fmt: skip
        db_session.add(Tag(document_id=d.id, tag=t, source="llm" if t == "rare" else "user"))
    db_session.flush()
    assert autotag.vocabulary(db_session) == ["common", "rare"]


def test_apply_replaces_llm_tags_keeps_user_tags_and_queues_es_sync(db_session, ana):
    d, _ = seed(db_session, ana)
    db_session.add_all(
        [Tag(document_id=d.id, tag="mine"), Tag(document_id=d.id, tag="old-auto", source="llm")]
    )
    db_session.flush()
    autotag.apply(db_session, d.id, ["mine", "fresh"])
    assert _tags(db_session, d.id) == {"mine": "user", "fresh": "llm"}  # user tag never downgraded
    jobs = db_session.scalars(select(Job.payload).where(Job.kind == "acl_sync")).all()
    assert jobs == [{"doc_id": str(d.id)}]


def test_apply_respects_max_tags(db_session, ana, monkeypatch):
    monkeypatch.setattr(autotag, "MAX_TAGS", 2)
    d, _ = seed(db_session, ana)
    db_session.add(Tag(document_id=d.id, tag="mine"))
    db_session.flush()
    autotag.apply(db_session, d.id, ["a", "b", "c"])
    assert _tags(db_session, d.id) == {"a": "llm", "mine": "user"}


def test_handler_tags_summary_only_doc_and_flags_version(db_session, ana):
    d, v = seed(db_session, ana)  # no keywords, no arXiv category
    llm = FakeLLM('["Sparse Retrieval"]')
    autotag.handle_auto_tags(db_session, {"version_id": str(v.id)}, settings=cfg, llm=llm)
    assert _tags(db_session, d.id) == {"sparse-retrieval": "llm"} and v.meta["auto_tagged"] is True
    body = llm.calls[0]["messages"][1]["content"]
    assert "sum" in body and "keywords" not in body.lower() and "arxiv" not in body.lower()


def test_handler_passes_hints_when_present(db_session, ana):
    _, v = seed(db_session, ana)
    v.meta = {**v.meta, "keywords": ["GNN"], "arxiv_category": "cs.LG"}
    db_session.flush()
    llm = FakeLLM("[]")
    autotag.handle_auto_tags(db_session, {"version_id": str(v.id)}, settings=cfg, llm=llm)
    body = llm.calls[0]["messages"][1]["content"]
    assert "Author keywords: GNN" in body and "arXiv category: cs.LG" in body


def test_gate_a_excluded_label_never_reaches_llm(db_session, ana):
    d, v = seed(db_session, ana)
    d.label_id = _nollm(db_session).id
    db_session.flush()
    llm = FakeLLM()
    autotag.handle_auto_tags(db_session, {"version_id": str(v.id)}, settings=cfg, llm=llm)
    assert llm.calls == [] and _tags(db_session, d.id) == {} and "auto_tagged" not in v.meta


def test_no_summary_no_llm_tags(db_session, ana):
    d, v = seed(db_session, ana)
    v.summary = ""
    db_session.flush()
    llm = FakeLLM()
    autotag.handle_auto_tags(db_session, {"version_id": str(v.id)}, settings=cfg, llm=llm)
    assert llm.calls == [] and _tags(db_session, d.id) == {}


def test_enqueue_missing_eligibility_and_dedupe(db_session, ana):
    _, need = seed(db_session, ana)
    _, done = seed(db_session, ana)
    done.meta = {**done.meta, "auto_tagged": True}
    _, nosum = seed(db_session, ana)
    nosum.summary = ""
    gated, _ = seed(db_session, ana)
    gated.label_id = _nollm(db_session).id
    seed(db_session, ana, stage="parsed")
    db_session.flush()
    assert autotag.enqueue_missing(db_session) == 1
    jobs = db_session.scalars(select(Job.payload).where(Job.kind == "auto_tags")).all()
    assert jobs == [{"version_id": str(need.id)}]
    assert autotag.enqueue_missing(db_session) == 0
    seed(db_session, ana)
    seed(db_session, ana)
    db_session.flush()
    assert autotag.enqueue_missing(db_session, limit=1) == 1


@pytest.mark.parametrize("exc", [TimeoutError("slow"), RuntimeError("down")])
def test_at_ingest_is_best_effort(db_session, session_factory, ana, exc):
    d, _ = seed(db_session, ana)
    db_session.commit()
    ok = autotag.at_ingest(db_session, session_factory, FakeLLM(exc=exc), "m", d, "T", "A summary.", {})
    assert ok is False and _tags(db_session, d.id) == {}


def test_at_ingest_writes_tags(db_session, session_factory, ana):
    d, _ = seed(db_session, ana)
    db_session.commit()
    assert autotag.at_ingest(db_session, session_factory, FakeLLM('["RAG"]'), "m", d, "T", "A summary.", {})
    db_session.expire_all()
    assert _tags(db_session, d.id) == {"rag": "llm"}


def test_vocabulary_r16_and_agent_gates(db_session, ana):
    ok, _ = seed(db_session, ana)
    stuck, _ = seed(db_session, ana, stage="needs_title")  # everyone-visible but never searchable (R16)
    blocked, _ = seed(db_session, ana)
    lab = Label(name="NoAgents", colour="#aabbcc", rank=2, block_agents=True)
    db_session.add(lab)
    db_session.flush()
    blocked.label_id = lab.id
    for d, t in ((ok, "fine"), (stuck, "stuck-x"), (blocked, "blocked-x")):
        db_session.add(Tag(document_id=d.id, tag=t))
    db_session.flush()
    assert autotag.vocabulary(db_session) == ["fine"]


def test_apply_skips_tags_the_owner_rejected(db_session, ana):
    d, _ = seed(db_session, ana)
    d.rejected_tags = ["nope"]
    db_session.flush()
    autotag.apply(db_session, d.id, ["nope", "yes"])
    assert _tags(db_session, d.id) == {"yes": "llm"}
