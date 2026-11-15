import uuid
from types import SimpleNamespace

import pytest
from elasticsearch import Elasticsearch

from roracle.auth import Principal
from roracle.ingest.chunking import Chunk
from roracle.models import Document, DocumentVersion
from roracle.retrieval import search_chunks
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name

pytestmark = pytest.mark.integration
DIMS = 4


def _vec(text: str) -> list[float]:
    t = text.lower()
    return [float("alpha" in t or "automobile" in t), float("beta" in t), float("gamma" in t), 0.1]


class FakeLLM:
    def __init__(self):
        self.embeddings = SimpleNamespace(create=self._embed)

    def _embed(self, model, input):
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=_vec(t)) for i, t in enumerate(input)]
        )


@pytest.fixture
def es(settings):
    c = Elasticsearch(settings.elastic_url)
    yield c
    c.close()


@pytest.fixture
def cfg(settings, es):
    m = f"Test Model:{uuid.uuid4().hex[:8]}"
    s = settings.model_copy(update={"embed_model": m, "embed_dims": DIMS, "rerank_url": ""})
    ensure_index(es, m, DIMS)
    yield s
    es.indices.delete(index=index_name(m), ignore_unavailable=True)


def _p(uid, admin=False):
    return Principal(user_id=uid, sub="s", username="u", groups=(), is_admin=admin)


def _add(es, cfg, db_session, blob, owner, title, text, *, vis="everyone", latest=True, version=1, doc=None,
         filename="a.pdf", tags=(), authors=(), year=None):  # fmt: skip
    d = doc or Document(owner_id=owner.id, title=title, visibility=vis, org_unit="/")
    if not doc:
        db_session.add(d)
        db_session.flush()
    v = DocumentVersion(
        id=uuid.uuid4(), document_id=d.id, version=version, blob_sha256=blob.sha256, filename=filename,
        content_type="x", size=1, is_latest=latest, meta={"authors": list(authors), "year": year},
    )  # fmt: skip
    db_session.add(v)
    db_session.flush()
    index_chunks(
        es, v, d, [Chunk(0, text, text, ["H"], [1], [{"page": 1}])], [_vec(text)],
        acl_fields(db_session, d), cfg.embed_model, title=title, tags=list(tags),
    )  # fmt: skip
    es.indices.refresh(index=index_name(cfg.embed_model))
    return d


def _search(cfg, es, p, q, **kw):
    kw.setdefault("filters", {})
    return search_chunks(es, FakeLLM(), cfg, p, q, **kw)


def test_invisible_doc_not_found(es, cfg, db_session, blob, user):
    other = Document(owner_id=user.id, title="x", visibility="private", org_unit="/")
    db_session.add(other)
    db_session.flush()
    _add(es, cfg, db_session, blob, user, "Secret", "zebra gamma unique", vis="private", doc=other)
    stranger = _p(uuid.uuid4())
    assert _search(cfg, es, stranger, "zebra") == []
    assert _search(cfg, es, stranger, "gamma") == []
    assert len(_search(cfg, es, _p(user.id), "zebra")) == 1


def test_bm25_and_knn_find_visible(es, cfg, db_session, blob, user):
    _add(es, cfg, db_session, blob, user, "Doc A", "quokka lives here alpha")
    _add(es, cfg, db_session, blob, user, "Doc B", "nothing relevant beta")
    p = _p(uuid.uuid4())
    hits = _search(cfg, es, p, "quokka")  # BM25 match
    assert hits[0].text.startswith("quokka") and "<em>quokka</em>" in hits[0].highlight
    knn = _search(cfg, es, p, "automobile")  # no lexical overlap: kNN-only recall
    assert knn[0].title == "Doc A"
    assert knn[0].highlight == knn[0].text[:240]  # fallback highlight


def test_highlight_escaped(es, cfg, db_session, blob, user):
    _add(es, cfg, db_session, blob, user, "X", "<script>alert(1)</script> quokka gamma")
    _add(es, cfg, db_session, blob, user, "Y", "<script>alert(1)</script> delta")
    p = _p(uuid.uuid4())
    import re

    for q in ("quokka", "automobile gamma"):
        for h in _search(cfg, es, p, q):
            assert "<script>" not in h.highlight
            assert not re.findall(r"<(?!/?em>)", h.highlight)
    y = next(h for h in _search(cfg, es, p, "alpha delta") if h.title == "Y")
    assert "&lt;script&gt;" in y.highlight


def test_unknown_filter_raises(es, cfg):
    with pytest.raises(ValueError):
        _search(cfg, es, _p(uuid.uuid4()), "q", filters={"bogus": ["x"]})


def test_filters_and_or(es, cfg, db_session, blob, user):
    _add(es, cfg, db_session, blob, user, "A", "shared term", tags=["ml", "nlp"], authors=["Ann"], year=2020)
    _add(es, cfg, db_session, blob, user, "B", "shared term", tags=["ml"], authors=["Bob"], year=2021,
         filename="b.docx")  # fmt: skip
    _add(es, cfg, db_session, blob, user, "C", "shared term", tags=["bio"], authors=["Ann"], year=2021)
    p = _p(uuid.uuid4())
    titles = lambda **f: sorted({x.title for x in _search(cfg, es, p, "shared", filters=f)})
    assert titles() == ["A", "B", "C"]
    assert titles(tags=["ml", "bio"]) == ["A", "B", "C"]  # OR within facet
    assert titles(tags=["ml"], year=[2021]) == ["B"]  # AND across facets
    assert titles(author=["Ann"], year=[2021]) == ["C"]
    assert titles(file_type=["docx"]) == ["B"]
    assert titles(tags=["ml"], author=["Zed"]) == []


def test_doc_ids_filter(es, cfg, db_session, blob, user):
    a = _add(es, cfg, db_session, blob, user, "A", "shared term")
    _add(es, cfg, db_session, blob, user, "B", "shared term")
    hits = _search(cfg, es, _p(uuid.uuid4()), "shared", doc_ids=[str(a.id)])
    assert {h.doc_id for h in hits} == {str(a.id)}


def test_non_latest_excluded(es, cfg, db_session, blob, user):
    d = _add(es, cfg, db_session, blob, user, "A", "oldversion shared alpha", latest=False)
    _add(es, cfg, db_session, blob, user, "A", "newversion shared", version=2, doc=d)
    texts = [x.text for x in _search(cfg, es, _p(uuid.uuid4()), "shared alpha")]
    assert texts == ["newversion shared"]


def test_empty_query_raises(es, cfg):
    with pytest.raises(ValueError):
        _search(cfg, es, _p(uuid.uuid4()), "  ")


def test_summary_vector_finds_doc_and_respects_acl(es, cfg, db_session, blob, user):
    sv = [0.0, 0.0, 1.0, 0.0]  # FakeLLM embeds "qq" as [0,0,0,.1]; craft summary vec nearest instead
    q = [0.0, 0.0, 0.0, 1.0]
    sv = q

    def add(title, vis):
        d = Document(owner_id=user.id, title=title, visibility=vis, org_unit="/")
        db_session.add(d)
        db_session.flush()
        v = DocumentVersion(
            id=uuid.uuid4(), document_id=d.id, version=1, blob_sha256=blob.sha256, filename="a.pdf",
            content_type="x", size=1, is_latest=True, meta={},
        )  # fmt: skip
        db_session.add(v)
        db_session.flush()
        index_chunks(
            es, v, d, [Chunk(0, "zzz", "zzz", [], [1], [])], [[1.0, 0.0, 0.0, 0.0]],
            acl_fields(db_session, d), cfg.embed_model, title=title, summary_vector=sv,
        )  # fmt: skip

    add("Visible", "everyone")
    add("Hidden", "private")
    es.indices.refresh(index=index_name(cfg.embed_model))
    llm = FakeLLM()
    llm._embed = lambda model, input: SimpleNamespace(data=[SimpleNamespace(index=0, embedding=q)])
    llm.embeddings = SimpleNamespace(create=llm._embed)
    out = search_chunks(es, llm, cfg, _p(uuid.uuid4()), "nomatchword", filters={})
    assert [x.title for x in out] == ["Visible"]
