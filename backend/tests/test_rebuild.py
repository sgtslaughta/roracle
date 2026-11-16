"""P13 T5: rebuild jobs, shadow guards, re-embed prompt parity (fake ES, real Postgres)."""
# ruff: noqa: F811

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from reembed_support import SRC, FakeEs, FakeLlm, base, drain, es, vid  # noqa: F401
from sqlalchemy import select

from roracle import reembed, search_index
from roracle.config import Settings
from roracle.ingest import pipeline, rebuild
from roracle.ingest.embed import chunk_prompt, prompt_sha
from roracle.models import Document, DocumentVersion, Job
from roracle.models_eval import EvalSet
from roracle.queue import NonRetryable


def _jobs(db, kind):
    return list(db.scalars(select(Job).where(Job.kind == kind).order_by(Job.id)))


def _searchable(db, vid_):
    v = db.get(DocumentVersion, __import__("uuid").UUID(vid_))
    v.stage, v.is_latest = "searchable", True
    db.flush()
    return v


def test_reembed_copy_reproduces_pipeline_prompts(db_session, session_factory, es, base, document, vid):
    v = _searchable(db_session, vid)
    v.summary = "Sum."
    db_session.commit()
    es.put(f"{vid}:0", document.id, text="body", headings=["H"], context="the note")
    es.put(f"{vid}:1", document.id, text="plain", headings=[])
    reembed.start(db_session, es, base, "new", 2)
    db_session.commit()
    drain(db_session, session_factory, es, FakeLlm(2), base)
    tgt = es.data["chunks-new-1"]
    assert tgt[f"{vid}:0"]["prompt_sha"] == prompt_sha(chunk_prompt("t", "H\nbody", "Sum.", "the note"))
    assert tgt[f"{vid}:1"]["prompt_sha"] == prompt_sha(chunk_prompt("t", "plain", "Sum.", None))
    assert tgt[f"{vid}:0"]["context"] == "the note"  # source note carried over verbatim


def test_enqueue_all_selects_latest_searchable_live(db_session, document, vid, blob):
    v = _searchable(db_session, vid)
    other = Document(owner_id=document.owner_id, title="x", org_unit="/", visibility="private")
    gone = Document(owner_id=document.owner_id, title="g", org_unit="/", visibility="private")
    gone.deleted_at = datetime.now(UTC)
    db_session.add_all([other, gone])
    db_session.flush()
    for doc, stage, latest in (
        (other, "parsed", True),
        (gone, "searchable", True),
        (document, "searchable", False),
    ):
        db_session.add(
            DocumentVersion(
                document_id=doc.id, version=2 if doc is document else 1, blob_sha256=blob.sha256,
                filename="a.pdf", content_type="x", size=1, stage=stage, is_latest=latest,
            )
        )  # fmt: skip
    db_session.flush()
    assert rebuild.enqueue_all(db_session, {"context"}, "chunks-x-shadow") == 1
    (job,) = _jobs(db_session, "rebuild_units")
    assert job.payload["version_id"] == str(v.id) and job.payload["into"] == "chunks-x-shadow"
    assert job.payload["what"] == ["context"]
    (watch,) = _jobs(db_session, "rebuild_watch")
    assert watch.payload["batch"] == job.payload["batch"]
    rebuild.enqueue_all(db_session, {"context"}, None)
    assert _jobs(db_session, "rebuild_units")[-1].payload["into"] is None


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "_index", lambda *a, **k: seen.append((a, k)))
    return seen


def _units(db, session_factory, es, payload, cfg=None):
    rebuild.handle_rebuild_units(
        db, payload, settings=cfg or Settings(context_model="m", embed_dims=2), store=SimpleNamespace(
            get_bytes=lambda k: b"{}"), es=es, llm=None, session_factory=session_factory,
    )  # fmt: skip


def test_units_skip_deleted_nonsearchable_superseded(db_session, session_factory, es, document, vid, calls):
    v = _searchable(db_session, vid)
    p = {"version_id": vid, "what": ["context"], "into": None}
    _units(db_session, session_factory, es, p)
    assert len(calls) == 1 and calls[0][1] == {"into": None, "rebuild": True}
    assert calls[0][0][4].context_notes is True  # forced on for what=context
    v.is_latest = False
    _units(db_session, session_factory, es, p)
    v.is_latest, v.stage = True, "embedded"
    _units(db_session, session_factory, es, p)
    v.stage = "searchable"
    document.deleted_at = datetime.now(UTC)
    db_session.flush()
    _units(db_session, session_factory, es, p)
    assert len(calls) == 1


def test_units_need_context_model_and_existing_shadow(db_session, session_factory, es, vid, calls):
    _searchable(db_session, vid)
    with pytest.raises(NonRetryable):
        _units(
            db_session, session_factory, es, {"version_id": vid, "what": ["context"]}, Settings(embed_dims=2)
        )
    with pytest.raises(NonRetryable):
        _units(
            db_session,
            session_factory,
            es,
            {"version_id": vid, "what": ["context"], "into": "chunks-gone-shadow"},
        )


def _ref(db):
    s = EvalSet(name="ref", is_reference=True)
    db.add(s)
    db.flush()
    return s


def test_watch_pending_requeues_and_done_enqueues_eval_once(db_session, document, vid):
    _searchable(db_session, vid)
    _ref(db_session)
    rebuild.enqueue_all(db_session, {"context"}, None)
    watch = _jobs(db_session, "rebuild_watch")[0]
    cfg = Settings(eval_on_reindex=True)
    rebuild.handle_rebuild_watch(db_session, watch.payload, settings=cfg)
    assert len(_jobs(db_session, "rebuild_watch")) == 2 and _jobs(db_session, "eval_run") == []
    for j in _jobs(db_session, "rebuild_units"):
        j.status = "done"
    db_session.flush()
    rebuild.handle_rebuild_watch(db_session, watch.payload, settings=cfg)
    (ev,) = _jobs(db_session, "eval_run")
    assert ev.payload["trigger"] == "reindex" and len(_jobs(db_session, "rebuild_watch")) == 2
    rebuild.handle_rebuild_watch(db_session, {**watch.payload, "into": "chunks-a-shadow"}, settings=cfg)
    assert len(_jobs(db_session, "eval_run")) == 1  # a shadow is judged by explicit compare
    rebuild.handle_rebuild_watch(db_session, watch.payload, settings=Settings(eval_on_reindex=False))
    assert len(_jobs(db_session, "eval_run")) == 1


def test_shadow_create_and_drop_guards(es):
    cfg = Settings(embed_model="old", embed_dims=2)
    active = search_index.active_index(cfg)
    es.data[active], es.aliases["chunks"], es.aliases["chunks-all"] = {}, {active}, {active}
    for bad in (active, "chunks", "chunks-all", "x", "chunks-A b"):
        with pytest.raises(ValueError):
            rebuild.create_shadow(es, cfg, bad)
    rebuild.create_shadow(es, cfg, "chunks-old-shadow")
    assert es.aliases["chunks"] == {active} and "chunks-old-shadow" in es.aliases["chunks-all"]
    rebuild.create_shadow(es, cfg, "chunks-old-shadow")  # idempotent
    with pytest.raises(ValueError):
        rebuild.drop_shadow(es, active, active)  # active by name
    with pytest.raises(ValueError):
        rebuild.drop_shadow(es, active)  # holder of the read alias
    with pytest.raises(ValueError):
        rebuild.drop_shadow(es, "chunks-unknown")
    rebuild.drop_shadow(es, "chunks-old-shadow", active)
    assert "chunks-old-shadow" not in es.data and active in es.data


def test_start_refuses_while_reembed_runs(db_session, es, base, vid):
    reembed.start(db_session, es, base, "new", 2)
    with pytest.raises(reembed.AlreadyRunning):
        rebuild.start(db_session, es, base, {"context"}, None)
