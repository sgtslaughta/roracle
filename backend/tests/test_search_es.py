"""Search API against a real Elasticsearch: ACL pre-filter, facets from the visible set only."""

import uuid
from types import SimpleNamespace

import pytest
from api_seed import seed, user_id
from elasticsearch import Elasticsearch

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import Principal, get_app_settings
from roracle.ingest.chunking import Chunk
from roracle.labels.policy import Policy
from roracle.models import Tag, User
from roracle.retrieval import search_chunks
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name
from roracle.search_query import bm25_clause, parse_query, text_clauses
from roracle.search_signals import sync_signals

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_blob_store_override")]
DIMS = 4


class FakeLLM:
    def __init__(self):
        self.embeddings = SimpleNamespace(create=lambda model, input: SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[1.0, 0.0, 0.0, 0.1]) for i in range(len(input))]))  # fmt: skip


@pytest.fixture
def es(settings):
    c = Elasticsearch(settings.elastic_url)
    yield c
    c.close()


@pytest.fixture
def wired(app, settings, es):
    model = f"Test Model:{uuid.uuid4().hex[:8]}"
    cfg = settings.model_copy(update={"embed_model": model, "embed_dims": DIMS, "rerank_url": ""})
    ensure_index(es, model, DIMS)
    app.dependency_overrides[get_es] = lambda: es
    app.dependency_overrides[get_llm] = FakeLLM
    app.dependency_overrides[get_app_settings] = lambda: cfg
    yield cfg
    es.indices.delete(index=index_name(model), ignore_unavailable=True)


def index(es, cfg, db, doc, ver, text, tags=()):
    index_chunks(
        es, ver, doc, [Chunk(0, text, text, ["H"], [1], [{"page": 1, "l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}])],
        [[1.0, 0.0, 0.0, 0.1]], acl_fields(db, doc), cfg.embed_model, title=doc.title, tags=list(tags),
    )  # fmt: skip
    es.indices.refresh(index=index_name(cfg.embed_model))


def test_acl_and_facets_real_es(client, as_ana, as_bob, db_session, es, wired, session_factory):
    _ana, bob = user_id(db_session, client, as_ana, "ana"), user_id(db_session, client, as_bob, "bob")
    d1, v1 = seed(db_session, bob, title="Open", year=2021)
    d2, v2 = seed(db_session, bob, title="Secret", visibility="private", year=1999)
    db_session.add_all([Tag(document_id=d1.id, tag="ml"), Tag(document_id=d2.id, tag="secret")])
    db_session.flush()
    index(es, wired, db_session, d1, v1, "quokka text", ["ml"])
    index(es, wired, db_session, d2, v2, "quokka secret text", ["secret"])
    db_session.commit()  # facets count head docs the signals job marked searchable (R13)
    sync_signals(es, session_factory, [str(d1.id), str(d2.id)])
    es.indices.refresh(index=index_name(wired.embed_model))
    r = client.get("/search", headers=as_ana, params={"q": "quokka"}).json()
    assert [x["title"] for x in r["results"]] == ["Open"] and r["total"] == 1
    assert r["results"][0]["passages"][0]["highlight"].count("<em>quokka</em>") == 1
    assert r["facets"]["tags"] == [{"value": "ml", "count": 1}]
    assert r["facets"]["year"] == [{"value": "2021", "count": 1}]
    own = client.get("/search", headers=as_bob, params={"q": "quokka"}).json()
    assert own["total"] == 2
    none = client.get("/search", headers=as_ana, params={"q": "secret", "tags": "secret"}).json()
    assert none["total"] == 0  # facets are disjunctive (R17) but never mention the invisible doc
    assert none["facets"]["tags"] == [{"value": "ml", "count": 1}]
    assert none["facets"]["year"] == []  # year facet honours the tags=secret filter, which no visible doc has


def _search(es, cfg, db, user, q="x", **kw):
    p = Principal(user.id, "sub", "u", (), False)
    return search_chunks(es, FakeLLM(), cfg, p, q, policy=Policy([]), filters={}, **kw)


def test_exclusion_and_field_scope_real_es(client, as_ana, db_session, es, wired):
    ana = user_id(db_session, client, as_ana, "ana")
    d1, v1 = seed(db_session, ana, title="One", authors=("Zola",))
    d2, v2 = seed(db_session, ana, title="Two", authors=("Hugo",))
    index(es, wired, db_session, d1, v1, "alpha beta")
    index(es, wired, db_session, d2, v2, "alpha gamma")
    user = db_session.get(User, ana)
    _flt, nn = text_clauses(parse_query("alpha -gamma"), "anywhere")
    got = _search(es, wired, db_session, user, "alpha", must_not=nn)
    assert {x.doc_id for x in got} == {str(d1.id)}
    pq = parse_query("Zola")
    got = _search(
        es, wired, db_session, user, "Zola", knn=False,
        extra_filter=[{"term": {"doc_head": True}}], bm25=bm25_clause(pq, "authors", ("chunk",)),
    )  # fmt: skip
    assert {x.doc_id for x in got} == {str(d1.id)}


def test_hyphenated_exclusion_is_phrase_real_es(client, as_ana, db_session, es, wired):
    ana = user_id(db_session, client, as_ana, "ana")
    d1, v1 = seed(db_session, ana, title="One")
    d2, v2 = seed(db_session, ana, title="Two")
    index(es, wired, db_session, d1, v1, "alpha state of the art")
    index(es, wired, db_session, d2, v2, "alpha of the")
    _flt, nn = text_clauses(parse_query("alpha -state-of-the-art"), "anywhere")
    got = _search(es, wired, db_session, db_session.get(User, ana), "alpha", must_not=nn)
    assert {x.doc_id for x in got} == {str(d2.id)}
