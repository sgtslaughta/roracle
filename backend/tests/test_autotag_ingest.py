"""Auto-tags at ingest: after the summary, best-effort, written to Postgres AND the indexed ES chunks."""

import pytest
from ingest_helpers import FakeLLM, docling_doc, fake_convert, handler, make_version
from sqlalchemy import select
from test_ingest_handler import _chunks

from roracle.autotag import SYSTEM_PROMPT
from roracle.ingest import pipeline
from roracle.models import DocumentVersion, Tag
from roracle.queue import enqueue, run_once

pytestmark = pytest.mark.integration


class TagLLM(FakeLLM):
    """Summaries as FakeLLM; the tagging prompt answers `reply` (or raises it)."""

    def __init__(self, dims, reply):
        super().__init__(dims)
        self.reply, self.tag_calls = reply, []
        real = self.chat.completions.create

        def create(**kw):
            if kw["messages"][0]["content"] != SYSTEM_PROMPT:
                return real(**kw)
            self.tag_calls.append(kw)
            if isinstance(self.reply, Exception):
                raise self.reply
            return type(real(**kw))(choices=[_choice(self.reply)])

        self.chat.completions.create = create


def _choice(content):
    from types import SimpleNamespace

    return SimpleNamespace(message=SimpleNamespace(content=content))


def _ingest(db_session, session_factory, cfg, store, es, user, monkeypatch, llm):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = make_version(db_session, store, user)
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    assert run_once(session_factory, {"ingest": handler(cfg, store, es, llm, session_factory)})
    db_session.expire_all()
    return db_session.get(DocumentVersion, v.id)


def test_ingest_auto_tags_reach_postgres_and_es(
    db_session, session_factory, cfg, store, es, user, monkeypatch, s3
):
    llm = TagLLM(4, '["Sparse Retrieval", "Lab Notebooks"]')
    v = _ingest(db_session, session_factory, cfg, store, es, user, monkeypatch, llm)
    assert v.stage == "searchable" and v.meta["auto_tagged"] is True
    body = llm.tag_calls[0]["messages"][1]["content"]
    assert v.summary in body and "keywords" not in body.lower()  # no hints: summary-only prompt
    rows = dict(db_session.execute(select(Tag.tag, Tag.source).where(Tag.document_id == v.document_id)).all())
    assert rows == {"lab-notebooks": "llm", "sparse-retrieval": "llm"}
    assert {tuple(h["_source"]["tags"]) for h in _chunks(es, cfg, v)} == {
        ("lab-notebooks", "sparse-retrieval")
    }


def test_ingest_survives_tagging_failure(db_session, session_factory, cfg, store, es, user, monkeypatch, s3):
    llm = TagLLM(4, TimeoutError("llm timed out"))
    v = _ingest(db_session, session_factory, cfg, store, es, user, monkeypatch, llm)
    assert v.stage == "searchable" and v.summary.startswith("A summary") and "auto_tagged" not in v.meta
    assert len(llm.tag_calls) == 1
    assert db_session.scalars(select(Tag).where(Tag.document_id == v.document_id)).all() == []
