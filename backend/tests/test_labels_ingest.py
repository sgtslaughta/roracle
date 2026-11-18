"""Gate (a) at ingest: the summary LLM is skipped for excluded labels; chunks are still embedded."""

import pytest
from ingest_helpers import FakeLLM, docling_doc, fake_convert, handler, make_version
from test_ingest_handler import _chunks

from roracle.ingest import pipeline
from roracle.models import DocumentVersion
from roracle.models_labels import Label
from roracle.queue import enqueue, run_once

pytestmark = pytest.mark.integration


class SpyLLM(FakeLLM):
    def __init__(self, dims):
        super().__init__(dims)
        self.chats = 0
        real = self.chat.completions.create

        def spy(**kw):
            self.chats += 1
            return real(**kw)

        self.chat.completions.create = spy


@pytest.mark.parametrize("excluded", [True, False])
def test_ingest_summary_skipped_for_excluded_label(
    db_session, session_factory, cfg, store, es, user, monkeypatch, s3, excluded
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    kw = {}
    if excluded:
        lab = Label(name="NoLLM", colour="#aabbcc", rank=3, exclude_llm=True)
        db_session.add(lab)
        db_session.flush()
        kw["label_id"] = lab.id
    v = make_version(db_session, store, user, **kw)
    llm = SpyLLM(4)
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    assert run_once(session_factory, {"ingest": handler(cfg, store, es, llm, session_factory)})
    db_session.expire_all()
    got = db_session.get(DocumentVersion, v.id)
    assert got.stage == "searchable" and _chunks(es, cfg, v)  # chunks still embedded
    if excluded:
        assert llm.chats == 0 and got.summary == ""
    else:
        assert llm.chats == 2 and got.summary.startswith("A summary")  # summary + auto-tags
