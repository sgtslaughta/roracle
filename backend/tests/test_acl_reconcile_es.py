"""Real ES: noop-aware ACL sync and the drift-only reconcile (Task 10.4)."""

import uuid

import pytest
from elasticsearch import Elasticsearch, helpers
from sqlalchemy import func, select

from roracle import acl_sync, maintenance, search_index
from roracle.models import Document, DocumentCoauthor, DocumentShare, Job, Label, Tag
from roracle.search_index import acl_hash

pytestmark = pytest.mark.integration
DIMS = 4


@pytest.fixture
def es(settings):
    c = Elasticsearch(settings.elastic_url)
    model = f"fake-embed-{uuid.uuid4().hex[:8]}"
    name = search_index.index_name(model)
    search_index.ensure_index(c, model, DIMS, alias=f"chunks-t-{uuid.uuid4().hex[:8]}")
    c.indices.put_settings(index=name, settings={"index": {"refresh_interval": "-1"}})
    c.test_index = name
    yield c
    c.indices.delete(index=name, ignore_unavailable=True)
    c.close()


def _doc(db, user, i, *, vis="everyone"):
    d = Document(owner_id=user.id, title=f"t{i}", org_unit="/", visibility=vis)
    db.add(d)
    db.flush()
    db.add(Tag(document_id=d.id, tag="x"))
    return d


def _index(es, db, docs, chunks=2, *, hashed=True):
    """Index `chunks` chunks per document exactly as ingest would (fields + acl_hash) from Postgres."""
    items = acl_sync.read_acl_many(db, [d.id for d in docs])
    acts = []
    for did, (acl, title) in items.items():
        for n in range(chunks):
            src = {**{k: v for k, v in acl.items()}, "doc_id": did, "title": title, "chunk_id": f"{did}:{n}"}
            if hashed:
                src["acl_hash"] = acl_hash(acl, title)
            acts.append({"_index": es.test_index, "_id": f"{did}:{n}", "_source": src})
    helpers.bulk(es, acts, refresh=True)


def _seq(es):
    es.indices.refresh(index=es.test_index)
    r = es.search(index=es.test_index, size=10000, seq_no_primary_term=True, source=False)
    return {h["_id"]: h["_seq_no"] for h in r["hits"]["hits"]}


def _src(es, doc_id, n=0):
    es.indices.refresh(index=es.test_index)
    return es.get(index=es.test_index, id=f"{doc_id}:{n}")["_source"]


def _run_acl_syncs(es, db, session_factory):
    """Run the queued acl_sync jobs the reconcile enqueued (what the worker would do)."""
    db.commit()
    for j in db.scalars(
        select(Job).where(Job.kind == "acl_sync", Job.status == "queued").order_by(Job.id)
    ).all():
        acl_sync.handle_acl_sync(db, j.payload, es=es, session_factory=session_factory)
        j.status = "done"
    db.commit()


def test_unchanged_document_is_a_noop(es, db_session, user):
    d = _doc(db_session, user, 0)
    _index(es, db_session, [d])
    before = _seq(es)
    items = acl_sync.read_acl_many(db_session, [d.id])
    assert (
        search_index.sync_acl_many(es, list(items), lambda ids, items=items: {i: items[i] for i in ids}) == 0
    )
    assert _seq(es) == before


def test_changed_share_reaches_every_chunk(es, db_session, user):
    d = _doc(db_session, user, 0, vis="shared")
    _index(es, db_session, [d], chunks=3)
    db_session.add(DocumentShare(document_id=d.id, principal_kind="group", principal="g9"))
    db_session.flush()
    items = acl_sync.read_acl_many(db_session, [d.id])
    assert (
        search_index.sync_acl_many(es, list(items), lambda ids, items=items: {i: items[i] for i in ids}) == 3
    )
    for n in range(3):
        s = _src(es, d.id, n)
        assert s["share_groups"] == ["g9"] and s["acl_hash"] == acl_hash(*items[str(d.id)])


def _new_label(db, d):
    lab = Label(name=f"L{uuid.uuid4().hex[:6]}", colour="#000000", rank=1)
    db.add(lab)
    db.flush()
    d.label_id = lab.id


def test_every_field_change_is_written(es, db_session, user):
    d = _doc(db_session, user, 0)
    _index(es, db_session, [d])
    changes = {
        "visibility": lambda: setattr(d, "visibility", "private"),
        "org_unit": lambda: setattr(d, "org_unit", "/other"),
        "title": lambda: setattr(d, "title", "renamed"),
        "tags": lambda: db_session.add(Tag(document_id=d.id, tag="y")),
        "label": lambda: _new_label(db_session, d),
        "share": lambda: db_session.add(
            DocumentShare(document_id=d.id, principal_kind="user", principal="u")
        ),
        "coauthor": lambda: db_session.add(DocumentCoauthor(document_id=d.id, user_id=user.id)),
    }
    for name, change in changes.items():
        change()
        db_session.flush()
        items = acl_sync.read_acl_many(db_session, [d.id])
        assert (
            search_index.sync_acl_many(es, list(items), lambda ids, items=items: {i: items[i] for i in ids})
            == 2
        ), name
        assert _src(es, d.id)["acl_hash"] == acl_hash(*items[str(d.id)]), name


def test_reconcile_in_sync_corpus_writes_nothing(es, db_session, user, monkeypatch):
    monkeypatch.setattr(maintenance, "RECONCILE_BATCH", 500)
    docs = [_doc(db_session, user, i) for i in range(1000)]
    db_session.flush()
    _index(es, db_session, docs, chunks=1)
    before = _seq(es)
    maintenance.handle_reconcile_tags(db_session, {}, es=es)
    while nxt := db_session.scalars(
        select(Job).where(Job.kind == "reconcile_tags", Job.status == "queued")
    ).all():
        j = nxt[0]
        j.status = "done"
        maintenance.handle_reconcile_tags(db_session, j.payload, es=es)
    assert _seq(es) == before
    assert db_session.scalar(select(func.count()).select_from(Job).where(Job.kind == "reconcile_tags")) <= 3
    assert db_session.scalar(select(func.count()).select_from(Job).where(Job.kind == "acl_sync")) == 0


def test_reconcile_fixes_only_the_drifted_docs(es, db_session, user, session_factory):
    docs = [_doc(db_session, user, i) for i in range(5)]
    db_session.flush()
    _index(es, db_session, docs, chunks=2)
    before = _seq(es)
    target = docs[2]
    target.visibility = "private"  # PG moved on; ES still says everyone
    db_session.flush()
    maintenance.handle_reconcile_tags(db_session, {}, es=es)
    assert [j.payload["doc_id"] for j in db_session.scalars(select(Job).where(Job.kind == "acl_sync"))] == [
        str(target.id)
    ]
    maintenance.handle_reconcile_tags(db_session, {}, es=es)  # again: a pending job is not duplicated
    assert db_session.scalar(select(func.count()).select_from(Job).where(Job.kind == "acl_sync")) == 1
    _run_acl_syncs(es, db_session, session_factory)
    after = _seq(es)
    changed = {i for i in after if after[i] != before[i]}
    assert changed == {f"{target.id}:0", f"{target.id}:1"}
    assert _src(es, target.id)["visibility"] == "private"


def test_reconcile_finds_one_stale_chunk_and_missing_hash(es, db_session, user, session_factory):
    d, legacy = _doc(db_session, user, 0), _doc(db_session, user, 1)
    db_session.flush()
    _index(es, db_session, [d], chunks=3)
    _index(es, db_session, [legacy], chunks=1, hashed=False)
    es.update(
        index=es.test_index, id=f"{d.id}:1", doc={"visibility": "private", "acl_hash": "stale"}, refresh=True
    )
    maintenance.handle_reconcile_tags(db_session, {}, es=es)
    _run_acl_syncs(es, db_session, session_factory)
    assert _src(es, d.id, 1)["visibility"] == "everyone" and _src(es, d.id, 1)["acl_hash"] != "stale"
    assert "acl_hash" in _src(es, legacy.id)


def test_partial_sync_invalidates_the_hash_so_reconcile_repairs_it(es, db_session, user, session_factory):
    d = _doc(db_session, user, 0)
    _index(es, db_session, [d])
    acl, _ = acl_sync.read_acl_many(db_session, [d.id])[str(d.id)]
    no_tags = {k: v for k, v in acl.items() if k != "tags"}
    search_index.sync_acl(es, d.id, lambda: ({**no_tags, "visibility": "private"}, None))
    assert "acl_hash" not in _src(es, d.id)
    d.visibility = "private"
    db_session.flush()
    maintenance.handle_reconcile_tags(db_session, {}, es=es)
    _run_acl_syncs(es, db_session, session_factory)
    assert _src(es, d.id)["acl_hash"] == acl_hash(*acl_sync.read_acl_many(db_session, [d.id])[str(d.id)])


def test_revocation_between_detect_and_repair_is_never_overwritten(es, db_session, user, session_factory):
    """The reconcile only detects. A share revoked after detection must win: the repair job re-reads Postgres."""
    d = _doc(db_session, user, 0, vis="shared")
    db_session.add(DocumentShare(document_id=d.id, principal_kind="group", principal="g-old"))
    db_session.flush()
    _index(es, db_session, [d])
    d.title = "renamed"  # drift: ES is behind
    db_session.flush()
    maintenance.handle_reconcile_tags(db_session, {}, es=es)  # detects, enqueues acl_sync
    db_session.execute(
        DocumentShare.__table__.delete().where(DocumentShare.document_id == d.id)
    )  # revoked now
    _run_acl_syncs(es, db_session, session_factory)
    s = _src(es, d.id)
    assert s["share_groups"] == [] and s["title"] == "renamed"


def test_sync_acl_many_rereads_postgres_on_every_try(es, db_session, user):
    d = _doc(db_session, user, 0, vis="shared")
    db_session.add(DocumentShare(document_id=d.id, principal_kind="group", principal="g-old"))
    db_session.flush()
    _index(es, db_session, [d])
    calls = []

    def compute(ids):
        calls.append(ids)
        db_session.execute(DocumentShare.__table__.delete().where(DocumentShare.document_id == d.id))
        return acl_sync.read_acl_many(db_session, [d.id])

    search_index.sync_acl_many(es, [str(d.id)], compute)
    assert len(calls) == 1 and _src(es, d.id)["share_groups"] == []


def test_sync_ensures_keyword_mapping_even_on_a_bare_index(
    settings, db_session, user, session_factory, monkeypatch
):
    """An index of chunks-all that never went through ensure_index: the first ACL write must not dynamic-map
    label_id as text (the must_not terms label_id gate would never match)."""
    c = Elasticsearch(settings.elastic_url)
    name = f"chunks-bare-{uuid.uuid4().hex[:8]}"
    c.indices.create(
        index=name,
        aliases={search_index.MUTATE_ALIAS: {}},
        mappings={"properties": {"doc_id": {"type": "keyword"}}},
    )
    monkeypatch.setattr(search_index, "_mapping_ensured", False)
    d = _doc(db_session, user, 0)
    db_session.flush()
    try:
        c.index(index=name, id="x:0", document={"doc_id": str(d.id), "chunk_id": "x:0"}, refresh=True)
        items = acl_sync.read_acl_many(db_session, [d.id])
        assert search_index.sync_acl_many(c, list(items), lambda ids: items) == 1
        props = c.indices.get_mapping(index=name)[name]["mappings"]["properties"]
        assert props["label_id"]["type"] == "keyword" and props["acl_hash"]["type"] == "keyword"
        got = c.search(index=name, query={"bool": {"must_not": [{"terms": {"label_id": [str(d.label_id)]}}]}})
        assert got["hits"]["total"]["value"] == 0  # the label gate matches
    finally:
        c.indices.delete(index=name, ignore_unavailable=True)
        c.close()
