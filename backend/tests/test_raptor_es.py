"""P13 T6 against real ES: per-document summary units lifecycle, ACL/label parity, PAT gate, expansion."""

import pytest
from ingest_helpers import make_version
from test_embed_reuse import ingest, paras

from roracle import search_index
from roracle.acl_sync import handle_acl_sync
from roracle.auth import Principal
from roracle.labels.policy import Policy
from roracle.models import Document
from roracle.models_labels import Label
from roracle.retrieval import expand_units, search_chunks
from roracle.search_index import MUTATE_ALIAS

pytestmark = pytest.mark.integration
ALL = ("chunk", "section", "document")


def _docs(es, cfg, vid, unit):
    idx = search_index.active_index(cfg)
    es.indices.refresh(index=idx)
    exists = {"exists": {"field": "unit"}}
    q = {"bool": {"filter": [{"term": {"version_id": str(vid)}}, *([exists] if unit else [])]}}
    if not unit:
        q["bool"]["must_not"] = [exists]
    return [h["_source"] | {"_id": h["_id"]} for h in es.search(index=idx, query=q, size=100)["hits"]["hits"]]


@pytest.fixture
def ingested(db_session, session_factory, cfg, store, es, user):
    cfg = cfg.model_copy(update={"raptor": True, "read_alias": f"chunks-t6-{cfg.embed_model[-8:]}"})
    v = make_version(db_session, store, user, visibility="everyone")
    ingest(db_session, session_factory, cfg, store, es, v, paras(3))
    yield cfg, v


def test_units_indexed_with_acl_label_and_real_sources(ingested, es, db_session):
    cfg, v = ingested
    d = db_session.get(Document, v.document_id)
    units, chunks = _docs(es, cfg, v.id, True), _docs(es, cfg, v.id, False)
    assert {u["unit"] for u in units} == {"section", "document"} and chunks
    ids = {c["chunk_id"] for c in chunks}
    for u in units:
        assert u["label_id"] == str(d.label_id) and u["is_latest"] is True
        assert u["visibility"] == chunks[0]["visibility"] == "everyone"
        assert u["source_chunks"] and set(u["source_chunks"]) <= ids and u["doc_id"] == str(d.id)
    assert {c["chunk_id"] for c in chunks if "unit" in c} == set()


def test_stale_cleanup_and_acl_sync_and_delete(ingested, es, db_session, session_factory):
    cfg, v = ingested
    n = len(_docs(es, cfg, v.id, False))
    search_index.delete_stale_chunks(es, v.id, n)
    assert len(_docs(es, cfg, v.id, True)) >= 2  # units survive chunk cleanup
    d = db_session.get(Document, v.document_id)
    d.visibility = "private"
    db_session.commit()
    handle_acl_sync(None, {"doc_id": str(d.id)}, es=es, session_factory=session_factory)
    assert {u["visibility"] for u in _docs(es, cfg, v.id, True)} == {"private"}
    search_index.delete_stale_units(es, v.id, [f"{v.id}:d"])
    assert [u["_id"] for u in _docs(es, cfg, v.id, True)] == [f"{v.id}:d"]
    es.delete_by_query(index=MUTATE_ALIAS, query={"term": {"doc_id": str(d.id)}}, refresh=True)
    assert _docs(es, cfg, v.id, True) == [] and _docs(es, cfg, v.id, False) == []


def test_search_default_chunks_only_units_opt_in_pat_gate_and_expansion(ingested, es, db_session, user):
    cfg, v = ingested
    d = db_session.get(Document, v.document_id)
    me = Principal(user.id, "s", "u", (), False)
    pol = Policy.load(db_session)
    llm = _Llm(cfg.embed_dims)
    q = "Paragraph retrieval"
    kw = {"policy": pol, "filters": {}, "summary_knn": False}
    default = search_chunks(es, llm, cfg, me, q, **kw)
    assert default and all(x.unit is None for x in default)
    withu = search_chunks(es, llm, cfg, me, q, units=ALL, **kw)
    units = [x for x in withu if x.unit]
    assert units
    out = expand_units(es, cfg, me, pol, q, withu, exclude_llm=True)
    assert out and all(x.unit is None for x in out)
    real = {c["chunk_id"] for c in _docs(es, cfg, v.id, False)}
    assert {x.chunk_id for x in out} <= real  # only real chunk ids: citations resolve to passages
    lab = Label(name="NoAgents", colour="#aabbcc", rank=3, block_agents=True)
    db_session.add(lab)
    db_session.flush()
    d.label_id = lab.id
    db_session.commit()
    pat = Principal(user.id, "s", "u", (), False, via="pat", scopes=frozenset({"read"}))
    # ES still carries the old label until acl_sync: sync it as production would
    search_index.sync_acl(es, d.id, lambda: (search_index.acl_fields(db_session, d), None))
    got = search_chunks(
        es, llm, cfg, pat, q, units=ALL, policy=Policy.load(db_session), filters={}, summary_knn=False
    )
    assert got == []  # chunks and units of a block_agents document are hidden from PATs


class _Llm:
    def __init__(self, dims):
        from ingest_helpers import FakeLLM

        self.embeddings = FakeLLM(dims).embeddings


def test_demotion_reaches_units(ingested, es, db_session):
    cfg, v = ingested
    search_index.mark_previous_versions(es, v.document_id, v.version + 1)
    es.indices.refresh(index=search_index.active_index(cfg))
    assert {u["is_latest"] for u in _docs(es, cfg, v.id, True)} == {False}


def test_doc_delete_query_removes_units(ingested, es, db_session):
    cfg, v = ingested
    from roracle.models import User

    d = db_session.get(Document, v.document_id)
    owner = db_session.get(User, d.owner_id)
    assert _docs(es, cfg, v.id, True)
    # the DELETE route and the purge both delete by doc_id over chunks-all: same query, units match
    es.delete_by_query(
        index=MUTATE_ALIAS, query={"term": {"doc_id": str(d.id)}}, refresh=True, conflicts="proceed"
    )
    assert _docs(es, cfg, v.id, True) == [] and owner is not None


def test_units_only_rebuild_leaves_chunks_untouched(ingested, es, db_session, session_factory, store):
    import json

    from ingest_helpers import FakeLLM
    from test_embed_reuse import det_doc

    from roracle.ingest import pipeline

    cfg, v = ingested
    before = {
        c["_id"]: es.get(index=search_index.active_index(cfg), id=c["_id"])["_seq_no"]
        for c in _docs(es, cfg, v.id, False)
    }
    d = db_session.get(Document, v.document_id)
    doc = json.loads(store.get_bytes(pipeline.docling_key(v.id)))
    search_index.delete_stale_units(es, v.id, [])
    assert _docs(es, cfg, v.id, True) == []
    pipeline.index_units_only(db_session, v, d, doc, cfg, es, FakeLLM(4), session_factory, store)
    after = {
        c["_id"]: es.get(index=search_index.active_index(cfg), id=c["_id"])["_seq_no"]
        for c in _docs(es, cfg, v.id, False)
    }
    assert after == before and {u["unit"] for u in _docs(es, cfg, v.id, True)} == {"section", "document"}
    assert det_doc  # keep import used


def test_expand_drops_source_pointing_at_another_document(
    ingested, es, db_session, session_factory, store, user
):
    cfg, v = ingested
    other = make_version(db_session, store, user, visibility="everyone")
    ingest(db_session, session_factory, cfg, store, es, other, paras(2))
    foreign = _docs(es, cfg, other.id, False)[0]["chunk_id"]
    mine = next(u for u in _docs(es, cfg, v.id, True) if u["unit"] == "document")
    from roracle.retrieval import Passage

    u = Passage(mine["chunk_id"], mine["doc_id"], mine["version_id"], "s", "", [], [], 1.0, unit="document",
                source_chunks=[foreign])  # fmt: skip
    me = Principal(user.id, "s", "u", (), False)
    assert expand_units(es, cfg, me, Policy.load(db_session), "q", [u], exclude_llm=True) == []
