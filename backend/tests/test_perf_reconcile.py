"""The suite's reconcile step on a tiny fixture: the walk's result must be what the suite row reads (a key mismatch
crashed the CI perf job once)."""

from types import SimpleNamespace

import pytest
from api_seed import seed

from roracle.models import Job
from roracle.perf import suite
from roracle.queue import enqueue


class Es:
    """Just enough Elasticsearch for `drifted` and the index-operation counter."""

    def __init__(self, drift=()):
        self.drift, self.indices = list(drift), SimpleNamespace(refresh=lambda **k: None, stats=self._stats)

    def _stats(self, **k):
        return {"_all": {"primaries": {"indexing": {"index_total": 7}}}}

    def search(self, **k):
        return {"hits": {"hits": [{"fields": {"doc_id": [d]}} for d in self.drift]}}


@pytest.fixture
def docs(db_session, ana):
    out = [seed(db_session, ana, title=f"D{i}")[0] for i in range(3)]
    db_session.commit()
    return out


PROFILE = {"docs": 3}


def test_clean_corpus_row_passes_with_zero(session_factory, docs):
    row = suite.reconcile_row(PROFILE, session_factory, Es(), {"reconcile_es_writes": {"max": 0}}, 1.5)
    assert row["ok"] and row["values"] == {"value": 0} and "0 documents with drift" in row["note"]


def test_drift_fails_the_gate_even_when_a_repair_is_already_queued(db_session, session_factory, docs):
    with session_factory() as s:
        enqueue(s, "acl_sync", {"doc_id": str(docs[0].id)})  # reconcile will skip enqueueing: dedupe
        s.commit()
    row = suite.reconcile_row(
        PROFILE, session_factory, Es(drift=[str(docs[0].id)]), {"reconcile_es_writes": {"max": 0}}, 3
    )
    assert not row["ok"] and row["values"]["value"] >= 1 and "1 documents with drift" in row["note"]
    assert (
        db_session.query(Job).filter(Job.kind == "reconcile_tags").count() == 0
    )  # the walk cleans up after itself
