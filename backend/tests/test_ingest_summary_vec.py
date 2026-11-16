"""Lazy summary-vector backfill in pipeline._index (R9)."""

import json

import pytest
from ingest_helpers import FakeLLM, docling_doc, handler, make_version

from roracle.ingest import pipeline
from roracle.ingest.embed import to_bytes
from roracle.models import DocumentVersion
from roracle.queue import enqueue, run_once

pytestmark = pytest.mark.integration
DIMS = 4


WRONG = to_bytes([0.5] * 3)


@pytest.mark.parametrize(
    ("summary", "stored", "sent", "row"),
    [
        ("S", None, "len", "new"),  # missing -> compute + persist
        ("S", WRONG, "len", "new"),  # wrong dims + summary -> re-embed
        ("", WRONG, None, "same"),  # wrong dims, no summary -> None, row untouched
    ],
)
def test_lazy_summary_backfill(
    db_session, session_factory, cfg, store, es, user, monkeypatch, summary, stored, sent, row
):
    v = make_version(db_session, store, user, title="T")
    store.put_bytes(pipeline.docling_key(v.id), json.dumps(docling_doc()).encode(), "application/json")
    db_session.query(DocumentVersion).filter_by(id=v.id).update(
        {"stage": "deduped", "summary": summary, "summary_embedding": stored}
    )
    db_session.commit()
    seen = []
    real = pipeline.search_index.index_chunks
    monkeypatch.setattr(
        pipeline.search_index,
        "index_chunks",
        lambda *a, **k: (seen.append(k.get("summary_vector")), real(*a, **k))[1],
    )
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    assert run_once(session_factory, {"ingest": handler(cfg, store, es, FakeLLM(DIMS), session_factory)})
    db_session.expire_all()
    got = db_session.get(DocumentVersion, v.id)
    assert got.stage == "searchable"
    assert len(seen) == 1
    assert (len(seen[0]) == DIMS) if sent == "len" else seen[0] is None
    assert (len(got.summary_embedding) == DIMS * 4) if row == "new" else got.summary_embedding == stored
