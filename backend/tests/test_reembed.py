"""Embedding-model switch: copy -> cutover -> catch-up against a fake ES (real Postgres)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import bearer
from elasticsearch import NotFoundError
from sqlalchemy import func, select

from roracle import reembed, runtime, search_index
from roracle.config import Settings
from roracle.models import DocumentVersion, Job, Tag

SRC = "chunks-old-1"


class FakeIndices:
    def __init__(self, es):
        self.es = es
        self.alias_calls = []

    def exists(self, index):
        return index in self.es.data or index in self.es.aliases

    def exists_alias(self, name, index=None):
        return name in self.es.aliases and (index is None or index in self.es.aliases[name])

    def create(self, index, mappings=None, aliases=None):
        self.es.data[index] = {}
        for a in aliases or {}:
            self.es.aliases.setdefault(a, set()).add(index)

    def get_alias(self, name):
        if not self.es.aliases.get(name):
            raise NotFoundError("nf", SimpleNamespace(status=404), {})
        return {i: {"aliases": {name: {}}} for i in self.es.aliases[name]}

    def update_aliases(self, actions):
        self.alias_calls.append(actions)
        for a in actions:
            ((op, spec),) = a.items()
            if op == "remove_index":
                self.es.data.pop(spec["index"])
                continue
            s = self.es.aliases.setdefault(spec["alias"], set())
            (s.add if op == "add" else s.discard)(spec["index"])

    def delete(self, index):
        for i in [index] if isinstance(index, str) else index:
            self.es.data.pop(i)
            for s in self.es.aliases.values():
                s.discard(i)


class FakeEs:
    def __init__(self):
        self.data = {SRC: {}}
        self.aliases = {"chunks": {SRC}, "chunks-all": {SRC}}
        self.indices = FakeIndices(self)
        self.mutations = []
        self.after_bulk = lambda: None  # hook: a concurrent change landing right after our write

    def put(self, cid, doc_id, text="hello", index=SRC, **kw):
        self.data[index][cid] = {
            "chunk_id": cid, "doc_id": str(doc_id), "version_id": cid.split(":")[0], "text": text,
            "title": "t", "headings": [], "owner_id": "o", "visibility": "private", "org_unit": "/",
            "share_users": [], "share_groups": [], "tags": [], "is_latest": True, "version": 1,
            "embedding": [9.0, 9.0], "embedding_model": "old", **kw,
        }  # fmt: skip

    def count(self, index):
        return {"count": len(self.data[index])}

    def search(self, index, size=10, sort=None, search_after=None, query=None, **kw):
        docs = self.data[index]
        if query and "ids" in query:
            ids = [i for i in query["ids"]["values"] if i in docs]
            return {"hits": {"hits": [{"_id": i} for i in ids]}}
        keys = sorted(k for k in docs if not search_after or k > search_after[0])[:size]
        return {"hits": {"hits": [{"_id": k, "_source": dict(docs[k]), "sort": [k]} for k in keys]}}

    def update_by_query(self, **kw):
        self.mutations.append(kw["index"])
        return {}

    def delete_by_query(self, **kw):
        self.mutations.append(kw["index"])
        q, docs = kw["query"], self.data.get(kw["index"], {})
        if "ids" in q:
            doomed = [i for i in q["ids"]["values"] if i in docs]
        elif "term" in q:
            ((f, v),) = q["term"].items()
            doomed = [i for i, d in docs.items() if d.get(f) == v]
        else:
            doomed = []
        for i in doomed:
            docs.pop(i)
        return {}


class FakeLlm:
    def __init__(self, dims):
        self.embeddings = SimpleNamespace(create=lambda model, input: self._create(model, input))
        self.dims, self.models = dims, []

    def _create(self, model, input):
        self.models.append(model)
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[1.0] * self.dims) for i, _ in enumerate(input)]
        )


@pytest.fixture
def es(monkeypatch):
    fake = FakeEs()

    def bulk(client, actions, **kw):
        n = 0
        for a in actions:
            fake.data[a["_index"]][a["_id"]] = a["_source"]
            n += 1
        fake.after_bulk()
        return n, []

    monkeypatch.setattr(reembed.helpers, "bulk", bulk)
    return fake


@pytest.fixture
def vid(db_session, document, blob):
    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="a.pdf",
        content_type="x", size=1,
    )  # fmt: skip
    db_session.add(v)
    db_session.flush()
    return str(v.id)


@pytest.fixture
def base():
    return Settings(embed_model="old", embed_dims=2)


def drain(db, sf, es, llm, base, limit=50):
    """Run queued reembed jobs in order (ignoring run_after) until none is left."""
    for _ in range(limit):
        j = db.scalar(select(Job).where(Job.kind == "reembed", Job.status == "queued").order_by(Job.id))
        if j is None:
            return
        j.status = "done"
        db.flush()
        reembed.handle_reembed(db, j.payload, es=es, llm=llm, session_factory=sf, settings=base)
        db.commit()
    raise AssertionError("did not terminate")


def test_cutover_swaps_alias_once_and_switches_model(db_session, session_factory, es, base, document, vid):
    db_session.commit()
    for n in range(5):
        es.put(f"{vid}:{n}", document.id, text=f"t{n}")
    llm = FakeLlm(3)
    reembed.start(db_session, es, base, "new-model", 3)
    db_session.commit()
    drain(db_session, session_factory, es, llm, base)
    target = "chunks-new-model-1"
    assert len(es.indices.alias_calls) == 1  # single atomic swap
    assert {tuple(sorted(a)) for a in es.indices.alias_calls[0]} == {("remove",), ("add",)}
    assert es.aliases["chunks"] == {target} and es.aliases["chunks-all"] == {SRC, target}
    assert sorted(es.data[target]) == sorted(es.data[SRC])
    doc = es.data[target][f"{vid}:0"]
    assert doc["embedding"] == [1.0] * 3 and doc["embedding_model"] == "new-model"
    assert llm.models and set(llm.models) == {"new-model"}
    eff = runtime.effective(db_session, base)
    assert (eff.embed_model, eff.embed_dims, search_index.active_index(eff)) == ("new-model", 3, target)
    assert search_index.active_index(base) == search_index.index_name("old")  # env bootstrap default


def _run_copy(db_session, session_factory, es, base):
    reembed.start(db_session, es, base, "old", 2)  # same model, next generation
    db_session.commit()
    drain(db_session, session_factory, es, FakeLlm(2), base)


def test_copy_takes_acl_from_postgres_not_source(db_session, session_factory, es, base, document, vid):
    """Source ES carries a stale ACL (S4): the target gets Postgres' fresh ACL/tags/title, never the source's."""
    document.visibility = "everyone"
    document.title = "fresh"
    db_session.add(Tag(document_id=document.id, tag="x"))
    db_session.commit()
    es.put(
        f"{vid}:0",
        document.id,
        visibility="org",
        org_unit="/x",
        share_users=["u"],
        owner_id="own",
        tags=["old"],
    )
    _run_copy(db_session, session_factory, es, base)
    t = es.data["chunks-old-2"][f"{vid}:0"]
    assert (t["visibility"], t["org_unit"], t["share_users"], t["tags"], t["title"]) == (
        "everyone", "/", [], ["x"], "fresh",
    )  # fmt: skip
    assert t["owner_id"] == str(document.owner_id)
    assert es.mutations == []  # nothing left to resync: written correct the first time


def test_acl_change_right_after_write_is_converged(db_session, session_factory, es, base, document, vid):
    db_session.commit()
    es.put(f"{vid}:0", document.id)

    def late_patch():  # PATCH landed after our PG read; its update_by_query ran before the bulk wrote
        document.visibility = "everyone"
        db_session.commit()
        es.after_bulk = lambda: None

    es.after_bulk = late_patch
    _run_copy(db_session, session_factory, es, base)
    assert es.mutations == ["chunks-all"]  # verify pass pushed Postgres' ACL through the mutation alias


def test_doc_deleted_before_copy_is_not_written(db_session, session_factory, es, base, document, vid):
    document.deleted_at = datetime.now(UTC)
    db_session.commit()
    es.put(f"{vid}:0", document.id)
    _run_copy(db_session, session_factory, es, base)
    assert es.data["chunks-old-2"] == {}


def test_doc_deleted_during_copy_is_absent_after_cutover(
    db_session, session_factory, es, base, document, vid
):
    db_session.commit()
    es.put(f"{vid}:0", document.id)

    def delete_now():
        document.deleted_at = datetime.now(UTC)  # purge's delete_by_query already ran before our write
        db_session.commit()
        es.after_bulk = lambda: None

    es.after_bulk = delete_now
    _run_copy(db_session, session_factory, es, base)
    assert es.data["chunks-old-2"] == {} and es.aliases["chunks"] == {"chunks-old-2"}


def test_version_purged_or_chunk_dropped_is_not_resurrected(
    db_session, session_factory, es, base, document, vid
):
    db_session.commit()
    es.put(f"{vid}:0", document.id)
    es.put(f"{vid}:1", document.id)
    es.put(f"{uuid.uuid4()}:0", document.id)  # version no longer in Postgres

    def shrink():  # re-ingest produced fewer chunks: delete_stale_chunks removed :1 from the source
        es.data[SRC].pop(f"{vid}:1")
        es.after_bulk = lambda: None

    es.after_bulk = shrink
    _run_copy(db_session, session_factory, es, base)
    assert list(es.data["chunks-old-2"]) == [f"{vid}:0"]


def test_older_version_chunks_not_latest_after_copy(
    db_session, session_factory, es, base, document, vid, blob
):
    v2 = DocumentVersion(
        document_id=document.id,
        version=2,
        blob_sha256=blob.sha256,
        filename="b.pdf",
        content_type="x",
        size=1,
    )
    db_session.add(v2)
    db_session.commit()
    es.put(f"{vid}:0", document.id, version=1, is_latest=True)  # demotion by v2's ingest raced the copy
    _run_copy(db_session, session_factory, es, base)
    assert es.data["chunks-old-2"][f"{vid}:0"]["is_latest"] is False


def test_reconcile_tags_enqueued_before_cutover_and_after_catchup(
    db_session, session_factory, es, base, document, vid
):
    db_session.commit()
    es.put(f"{vid}:0", document.id)
    _run_copy(db_session, session_factory, es, base)
    n = db_session.scalar(select(func.count()).select_from(Job).where(Job.kind == "reconcile_tags"))
    assert n >= 4  # end of copy, cutover, two catch-up rounds


def test_catchup_copies_chunk_ingested_during_copy(db_session, session_factory, es, base, document, vid):
    db_session.commit()
    es.put(f"{vid}:0", document.id)
    llm = FakeLlm(2)
    reembed.start(db_session, es, base, "old", 2)
    db_session.commit()
    for _ in range(2):  # copy batch + cutover job
        j = db_session.scalar(
            select(Job).where(Job.kind == "reembed", Job.status == "queued").order_by(Job.id)
        )
        j.status = "done"
        db_session.flush()
        reembed.handle_reembed(
            db_session, j.payload, es=es, llm=llm, session_factory=session_factory, settings=base
        )
        db_session.commit()
    es.put(f"{vid}:1", document.id, text="late")  # lands in the old index after the scan passed
    drain(db_session, session_factory, es, llm, base)
    assert f"{vid}:1" in es.data["chunks-old-2"]
    rounds = db_session.scalars(select(Job.payload["round"].astext).where(Job.kind == "reembed")).all()
    assert rounds.count("1") >= 1 and rounds.count("2") >= 1  # catch-up runs twice


def test_start_rejects_second_running_job(db_session, es, base):
    reembed.start(db_session, es, base, "new", 2)
    with pytest.raises(reembed.AlreadyRunning):
        reembed.start(db_session, es, base, "other", 2)


def test_status_reports_progress(db_session, session_factory, es, base, document, vid):
    db_session.commit()
    assert reembed.status(db_session, es, base) == {
        "active": {"index": "chunks-old-1", "model": "old", "dims": 2}, "running": None, "old": [],
    }  # fmt: skip
    for n in range(2):
        es.put(f"{vid}:{n}", document.id)
    reembed.start(db_session, es, base, "new", 2)
    j = db_session.scalar(select(Job).where(Job.kind == "reembed"))
    reembed.handle_reembed(
        db_session, j.payload, es=es, llm=FakeLlm(2), session_factory=session_factory, settings=base
    )
    s = reembed.status(db_session, es, base)
    assert s["running"]["target"] == {"model": "new", "dims": 2, "index": "chunks-new-1"}
    assert (s["running"]["phase"], s["running"]["copied"], s["running"]["total"]) == ("cutover", 2, 2)


def test_drop_old_keeps_active(db_session, es, base):
    es.data["chunks-new-1"] = {}
    es.aliases["chunks-all"].add("chunks-new-1")
    es.aliases["chunks"] = {"chunks-new-1"}
    runtime.set_active_embedding(db_session, "new", 2, "chunks-new-1")
    assert reembed.drop_old(db_session, es, base) == [SRC]
    assert list(es.data) == ["chunks-new-1"]


def test_drop_old_refuses_when_alias_is_not_the_active_index(db_session, es, base):
    """Cutover swapped the alias but failed before persisting: PG still says SRC, alias says target."""
    es.data["chunks-new-1"] = {}
    es.aliases["chunks-all"].add("chunks-new-1")
    es.aliases["chunks"] = {"chunks-new-1"}
    with pytest.raises(reembed.AliasMismatch):
        reembed.drop_old(db_session, es, base)
    assert set(es.data) == {SRC, "chunks-new-1"}
    assert reembed.status(db_session, es, base)["old"] == ["chunks-new-1"]  # never SRC-vs-live confusion


def test_model_change_guard_aborts_stale_ingest(db_session, base):
    runtime.set_active_embedding(db_session, "new", 3, "chunks-new-1")
    with pytest.raises(RuntimeError, match="embedding model changed"):
        runtime.assert_embedding_unchanged(db_session, base)
    runtime.assert_embedding_unchanged(db_session, runtime.effective(db_session, base))


def test_retrieval_reads_active_index(db_session, base):
    from roracle import retrieval

    runtime.set_active_embedding(db_session, "new", 2, "chunks-new-3")
    eff = runtime.effective(db_session, base)
    seen = []

    class E:
        def search(self, index, **kw):
            seen.append(index)
            return {"hits": {"hits": []}}

    p = SimpleNamespace(user_id="u", groups=(), is_admin=True)
    retrieval.search_chunks(E(), FakeLlm(2), eff, p, "q", filters={}, qvec=[0.0, 0.0])
    assert seen and set(seen) == {"chunks-new-3"}


def test_mutations_target_chunks_all(db_session, es):
    search_index.sync_acl(es, "d", lambda: ({"owner_id": "o", "visibility": "x", "org_unit": "/",
                                             "share_users": [], "share_groups": []}, "t"))  # fmt: skip
    search_index.delete_stale_chunks(es, "v", 1)
    search_index.mark_previous_versions(es, "d", 2)
    from roracle.api.routes import library

    library._sync_es(db_session, es, __import__("uuid").uuid4())
    assert es.mutations == ["chunks-all"] * 4


def test_purge_deletes_via_chunks_all(db_session, es):
    from roracle import maintenance

    maintenance._purge_doc(db_session, es, __import__("uuid").uuid4())
    assert es.mutations == ["chunks-all"]


def test_legacy_plain_chunks_index_is_adopted(db_session):
    calls = []

    class Idx(FakeIndices):
        def exists(self, index):
            return index in ("chunks",) or index in self.es.data

        def exists_alias(self, name, index=None):
            return (
                False
                if name == "chunks" and name not in self.es.aliases
                else super().exists_alias(name, index)
            )

        def get_mapping(self, index):
            return {index: {"mappings": {"properties": search_index._mapping(2)["properties"]}}}

        def put_alias(self, **kw):
            calls.append(("put_alias", kw))

    es = FakeEs()
    es.data = {"chunks": {"a": {}}}
    es.aliases = {}
    es.indices = Idx(es)
    es.reindex = lambda **kw: calls.append(("reindex", kw)) or {"failures": []}
    name = search_index.ensure_index(es, "old", 2)
    assert name == "chunks-old-1"
    assert calls[0][0] == "reindex" and calls[0][1]["source"] == {"index": "chunks"}
    swap = es.indices.alias_calls[0]
    assert {"remove_index": {"index": "chunks"}} in swap
    assert {"add": {"index": name, "alias": "chunks"}} in swap
    assert {"add": {"index": name, "alias": "chunks-all"}} in swap
    assert len(es.indices.alias_calls) == 1


def test_legacy_reindex_failure_keeps_old_index(db_session):
    es = FakeEs()
    es.data = {"chunks": {}}
    es.aliases = {}
    es.indices.exists = lambda index: index in es.data
    es.indices.exists_alias = lambda name, index=None: False
    es.reindex = lambda **kw: {"failures": [{"x": 1}]}
    with pytest.raises(RuntimeError, match="reindex"):
        search_index.ensure_index(es, "old", 2)
    assert "chunks" in es.data and es.indices.alias_calls == []


# --- REST ---------------------------------------------------------------------------------------


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.fixture
def api_es(app, es):
    from roracle.api.routes.library import get_es

    app.dependency_overrides[get_es] = lambda: es
    return es


def test_rest_contract(client, adm, as_ana, db_session, api_es):
    assert client.post("/admin/reindex", json={"model": "m", "dims": 2}, headers=as_ana).status_code == 403
    r = client.get("/admin/reindex", headers=adm)
    assert r.status_code == 200 and r.json()["running"] is None and r.json()["active"]["index"]
    assert client.post("/admin/reindex", json={"model": "", "dims": 2}, headers=adm).status_code == 422
    r = client.post("/admin/reindex", json={"model": "m", "dims": 2}, headers=adm)
    assert r.status_code == 202
    j = db_session.get(Job, r.json()["job_id"])
    assert j.kind == "reembed" and j.payload["target"] == {"model": "m", "dims": 2, "index": "chunks-m-1"}
    assert client.post("/admin/reindex", json={"model": "m2", "dims": 2}, headers=adm).status_code == 409
    g = client.get("/admin/reindex", headers=adm).json()
    assert g["running"] == {"target": j.payload["target"], "copied": 0, "total": None, "phase": "copy"}
    assert client.delete("/admin/reindex/old", headers=adm).status_code == 409  # running
    j.status = "done"
    db_session.flush()
    assert client.delete("/admin/reindex/old", headers=adm).status_code == 409  # alias != active index
    runtime.set_active_embedding(db_session, "m0", 2, SRC)
    api_es.data["chunks-m-1"] = {}
    api_es.aliases["chunks-all"].add("chunks-m-1")
    assert client.delete("/admin/reindex/old", headers=adm).status_code == 204
    assert (
        "chunks-m-1" not in api_es.data and SRC in api_es.data
    )  # the index behind `chunks` is never dropped


def test_unguarded_ensure_index_leaves_existing_read_alias(es):
    es.indices.create("chunks-b-1")
    es.indices.es.data["chunks-b-1"] = {}
    es.indices.get_mapping = lambda index: {
        index: {"mappings": {"properties": search_index._mapping(2)["properties"]}}
    }
    search_index.ensure_index(es, "b", 2, name="chunks-b-1")
    assert es.aliases["chunks"] == {SRC} and es.aliases["chunks-all"] == {SRC, "chunks-b-1"}
