"""Shared fakes/fixtures for the re-embed tests (fake ES over real Postgres)."""

from types import SimpleNamespace

import pytest
from elasticsearch import NotFoundError
from es_fakes import msearch as _es_msearch
from sqlalchemy import select

from roracle import reembed
from roracle.config import Settings
from roracle.models import DocumentVersion, Job

SRC = "chunks-old-1"


class FakeIndices:
    def __init__(self, es):
        self.es = es
        self.alias_calls = []

    def exists(self, index):
        return index in self.es.data or index in self.es.aliases

    def exists_alias(self, name, index=None):
        return name in self.es.aliases and (index is None or index in self.es.aliases[name])

    def create(self, index, mappings=None, aliases=None, settings=None):
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

    msearch = _es_msearch

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
