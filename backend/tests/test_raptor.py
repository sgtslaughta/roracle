"""P13 T6: section/document summary units (fake store/LLM, real Postgres for labels)."""

import json
from types import SimpleNamespace

import pytest
from ingest_helpers import FakeLLM
from sqlalchemy import update
from test_context_notes import Store, _label

from roracle import search_index
from roracle.config import Settings
from roracle.ingest import raptor
from roracle.ingest.chunking import Chunk
from roracle.models import Document, DocumentVersion

CFG = Settings(raptor=True, raptor_model="rm", raptor_section_chars=400)


class SpyLLM(FakeLLM):
    def __init__(self, hook=None):
        super().__init__(4)
        self.calls, self.hook = [], hook
        self.chat.completions.create = self._spy

    def _spy(self, **kw):
        self.calls.append(kw)
        if self.hook:
            self.hook(len(self.calls))
        msg = SimpleNamespace(content=f"summary {len(self.calls)}")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def C(n, head=None, page=1):
    return Chunk(n, f"text {n} " * 20, "", [head] if head else [], [page], [])


@pytest.fixture
def vd(db_session, document, blob):
    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="a.pdf",
        content_type="x", size=1,
    )  # fmt: skip
    db_session.add(v)
    db_session.flush()
    return v, document


def test_sections_by_first_heading_and_windows():
    cs = [C(0, "A"), C(1, "A"), C(2, "B"), C(3, "A")]
    assert [[c.n for c in s] for s in raptor.sections(cs)] == [[0, 1], [2], [3]]
    cs = [C(i) for i in range(19)]
    assert [len(s) for s in raptor.sections(cs)] == [8, 8, 3]
    assert raptor.sections([]) == []


def test_build_units_per_document_and_cached(db_session, vd):
    v, d = vd
    cs = [C(0, "A", 1), C(1, "A", 2), C(2, "B", 3)]
    llm, store = SpyLLM(), Store()
    units = raptor.build_units(db_session, llm, CFG, store, v, d, cs)
    assert [(u.kind, u.key) for u in units] == [("section", "s0"), ("section", "s1"), ("document", "d")]
    assert units[0].source_chunks == [f"{v.id}:0", f"{v.id}:1"] and units[0].pages == [1, 2]
    assert units[0].headings == ["A"]
    assert units[2].source_chunks == [f"{v.id}:{i}" for i in range(3)] and units[2].pages == [1, 2, 3]
    assert all(c["model"] == "rm" for c in llm.calls) and len(llm.calls) == 3
    assert f"derived/{v.id}/raptor-v1.json" in store.data
    again = SpyLLM()
    assert raptor.build_units(db_session, again, CFG, store, v, d, cs) == units and again.calls == []


def test_exclude_llm_no_units_no_calls(db_session, vd):
    v, d = vd
    d.label_id = _label(db_session, exclude_llm=True).id
    db_session.flush()
    llm, store = SpyLLM(), Store()
    assert raptor.build_units(db_session, llm, CFG, store, v, d, [C(0, "A")]) == []
    assert llm.calls == [] and store.data == {}


def test_label_raised_mid_job_stops_and_returns_nothing(db_session, vd):
    v, d = vd
    lab = _label(db_session, exclude_llm=True)

    def raise_label(n):
        if n == 1:
            db_session.execute(update(Document).where(Document.id == d.id).values(label_id=lab.id))

    llm, store = SpyLLM(raise_label), Store()
    assert raptor.build_units(db_session, llm, CFG, store, v, d, [C(0, "A"), C(1, "B")]) == []
    assert len(llm.calls) == 1
    assert len(json.loads(store.data[f"derived/{v.id}/raptor-v1.json"])) == 1


def test_index_units_carry_acl_label_and_sources(db_session, vd, monkeypatch):
    v, d = vd
    got = []
    monkeypatch.setattr(
        search_index.helpers, "bulk", lambda es, actions, **k: got.extend(actions) or (len(got), [])
    )
    units = raptor.build_units(db_session, SpyLLM(), CFG, Store(), v, d, [C(0, "A", 2)])
    acl = search_index.acl_fields(db_session, d)
    search_index.index_units(
        None, v, d, units, [[0.0] * 4] * len(units), acl, "m", title="T", is_latest=True, tags=["x"],
        index="idx",
    )  # fmt: skip
    assert {a["_id"] for a in got} == {f"{v.id}:s0", f"{v.id}:d"}
    for a in got:
        s = a["_source"]
        assert (
            s["unit"] in ("section", "document") and s["source_chunks"] and s["label_id"] == str(d.label_id)
        )
        assert {k: s[k] for k in acl} == acl and s["doc_id"] == str(d.id) and s["bboxes"] == []
        assert s["chunk_id"] == a["_id"] and s["pages"] == [2] and s["is_latest"] is True


def test_delete_stale_queries_keep_units_apart():
    calls = []
    es = SimpleNamespace(delete_by_query=lambda **k: calls.append(k))
    search_index.delete_stale_chunks(es, "v", 2)
    search_index.delete_stale_units(es, "v", ["v:s0"])
    chunks_q, units_q = (c["query"]["bool"] for c in calls)
    assert {"exists": {"field": "unit"}} in chunks_q["must_not"]
    assert {"exists": {"field": "unit"}} in units_q["filter"]
    assert units_q["must_not"] == [{"ids": {"values": ["v:s0"]}}]
    search_index.delete_stale_units(es, "v", [])
    assert calls[-1]["query"]["bool"]["must_not"] == []


def test_long_section_windowed_and_document_sees_every_section(db_session, vd):
    v, d = vd
    cfg = CFG.model_copy(update={"raptor_section_chars": 300})
    cs = [C(i, "A") for i in range(6)] + [
        C(6, "B")
    ]  # section A ~840 chars > 300 -> windows -> summary of summaries
    llm = SpyLLM()
    units = raptor.build_units(db_session, llm, cfg, Store(), v, d, cs)
    assert [u.key for u in units] == ["s0", "s1", "d"]
    assert len(llm.calls) > 3 and all(len(c["messages"][1]["content"]) <= 300 for c in llm.calls)
    last = llm.calls[-1]["messages"][1]["content"]  # document call: built from section summaries only
    assert units[0].text in last and units[1].text in last
    assert units[0].source_chunks == [f"{v.id}:{i}" for i in range(6)]


def test_small_sections_merged_and_capped():
    cs = [Chunk(i, "x" * 10, "", [f"H{i}"], [1], []) for i in range(10)]
    secs = raptor.sections(cs)
    assert len(secs) == 10
    assert [len(s) for s in raptor.merge_sections(secs, 30, 99)] == [3, 3, 3, 1]  # folded until >= 30 chars
    capped = raptor.merge_sections(secs, 0, 3)
    assert len(capped) == 3 and [c.n for s in capped for c in s] == list(range(10))


def test_source_chunks_strided_to_cap(db_session, vd):
    v, d = vd
    cfg = CFG.model_copy(update={"raptor_section_chars": 10**6})
    cs = [Chunk(i, "t", "", ["A"], [1], []) for i in range(450)]
    units = raptor.build_units(db_session, SpyLLM(), cfg, Store(), v, d, cs)
    assert all(len(u.source_chunks) <= raptor.MAX_SOURCES for u in units)


def test_label_raise_during_windows_returns_nothing(db_session, vd):
    v, d = vd
    lab = _label(db_session, exclude_llm=True)
    cfg = CFG.model_copy(update={"raptor_section_chars": 300})

    def raise_label(n):
        if n == 1:
            db_session.execute(update(Document).where(Document.id == d.id).values(label_id=lab.id))

    llm = SpyLLM(raise_label)
    assert raptor.build_units(db_session, llm, cfg, Store(), v, d, [C(i, "A") for i in range(6)]) == []
    assert len(llm.calls) == 1


def test_delete_stale_chunks_single_query_with_keep_units():
    calls = []
    es = SimpleNamespace(delete_by_query=lambda **k: calls.append(k))
    search_index.delete_stale_chunks(es, "v", 2, keep_units=["v:s0", "v:d"])
    (q,) = calls
    assert q["query"]["bool"]["must_not"] == [{"ids": {"values": ["v:0", "v:1", "v:s0", "v:d"]}}]


def test_llm_outage_does_not_block_ingest_units(db_session, vd, monkeypatch):
    from roracle.ingest import pipeline

    v, d = vd

    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(pipeline, "build_units", boom)
    args = (db_session, v, d, [C(0)], CFG, None, None, Store(), {}, "t", True, [], "idx")
    assert pipeline._index_units(*args) is None  # keep existing units, retry via rebuild
    with pytest.raises(RuntimeError):
        pipeline._index_units(*args, soft=False)  # a rebuild job fails loudly instead
