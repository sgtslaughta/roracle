import uuid

import pytest
from conftest import bearer
from elasticsearch import Elasticsearch

from roracle.api.routes.library import get_es, normalize_tag
from roracle.models import Document, Vote

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FakeES:
    def __init__(self, missing=False):
        self.calls, self.missing = [], missing

    def update_by_query(self, **kw):
        if self.missing:
            from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
            from elasticsearch import NotFoundError

            meta = ApiResponseMeta(404, "1.1", HttpHeaders(), 0.0, NodeConfig("http", "x", 9200))
            raise NotFoundError("index_not_found_exception", meta, {})
        self.calls.append(kw)


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    return f


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


@pytest.fixture
def up(client):
    def make(headers, visibility="everyone"):
        r = client.post(
            "/documents",
            headers=headers,
            data={"visibility": visibility},
            files={"file": ("p.pdf", uuid.uuid4().bytes, "application/pdf")},
        )
        assert r.status_code == 201, r.text
        return r.json()["document_id"]

    return make


def _vote(client, h, d, **body):
    return client.put(f"/documents/{d}/vote", headers=h, json=body)


# ---- votes
def test_vote_toggle_and_counts(client, as_ana, as_bob, up):
    d = up(as_ana)
    assert _vote(client, as_ana, d, value=1).json() == {"up": 1, "down": 0, "mine": 1}
    assert _vote(client, as_bob, d, value=-1).json() == {"up": 1, "down": 1, "mine": -1}
    assert _vote(client, as_bob, d, value=1).json() == {"up": 2, "down": 0, "mine": 1}  # upsert
    assert _vote(client, as_bob, d, value=0).json() == {"up": 1, "down": 0, "mine": 0}  # delete
    assert _vote(client, as_bob, d, value=0).status_code == 200  # idempotent


@pytest.mark.parametrize("reason", ["outdated", "wrong", "low_quality"])
def test_downvote_reason_stored(client, as_ana, up, db_session, reason):
    d = up(as_ana)
    assert _vote(client, as_ana, d, value=-1, reason=reason).status_code == 200
    assert db_session.query(Vote).one().reason == reason


def test_reason_validation(client, as_ana, up):
    d = up(as_ana)
    assert _vote(client, as_ana, d, value=-1, reason="boring").status_code == 422
    assert _vote(client, as_ana, d, value=1, reason="wrong").status_code == 422
    assert _vote(client, as_ana, d, value=0, reason="wrong").status_code == 422
    assert _vote(client, as_ana, d, value=2).status_code == 422
    assert _vote(client, as_ana, d, value=1, superseded_by=d).status_code == 422
    assert _vote(client, as_ana, d, value=-1, reason="wrong", superseded_by=d).status_code == 422


def test_superseded_rules(client, as_ana, as_bob, up):
    d, other = up(as_ana), up(as_ana)
    assert _vote(client, as_bob, d, value=-1, reason="superseded").status_code == 422
    assert _vote(client, as_bob, d, value=-1, reason="superseded", superseded_by=d).status_code == 422
    missing = _vote(client, as_bob, d, value=-1, reason="superseded", superseded_by=str(uuid.uuid4()))
    assert missing.status_code == 422 and missing.json()["detail"] == "superseded_by not found"
    hidden = up(as_ana, "private")
    r = _vote(client, as_bob, d, value=-1, reason="superseded", superseded_by=hidden)
    assert r.status_code == 422 and r.json()["detail"] == "superseded_by not found"
    assert _vote(client, as_bob, d, value=-1, reason="superseded", superseded_by=other).status_code == 200


def test_vote_invisible_is_404(client, as_ana, as_bob, up):
    d = up(as_ana, "private")
    r = _vote(client, as_bob, d, value=1)
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    assert _vote(client, as_bob, str(uuid.uuid4()), value=1).status_code == 404


# ---- tags
@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("Machine Learning!", "machine-learning"),
        ("  --a__b--  ", "a-b"),
        ("RAG", "rag"),
        ("x" * 40, "x" * 40),
        ("Größe", "grösse"),
        ("数学", "数学"),
        ("naïve", "naïve"),
    ],
)
def test_normalize_ok(raw, norm):
    assert normalize_tag(raw) == norm


@pytest.mark.parametrize("raw", ["  --  ", "", "!!!", "x" * 41])
def test_normalize_bad(raw):
    with pytest.raises(ValueError, match="tag"):
        normalize_tag(raw)


def test_tag_add_idempotent_and_list(client, as_ana, as_bob, up, fes):
    d = up(as_ana)
    r = client.post(f"/documents/{d}/tags", headers=as_bob, json={"tag": "Machine Learning!"})
    assert r.status_code == 200 and r.json() == {"tag": "machine-learning", "tags": ["machine-learning"]}
    client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "machine-learning"})
    r = client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "zeta"})
    assert r.json()["tags"] == ["machine-learning", "zeta"]
    assert fes.calls[-1]["script"]["params"] == {"tags": ["machine-learning", "zeta"]}
    assert fes.calls[-1]["query"] == {"term": {"doc_id": d}}
    assert fes.calls[-1]["refresh"] is True


def test_tag_422(client, as_ana, up, fes):
    d = up(as_ana)
    assert client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "  --  "}).status_code == 422
    assert client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "x" * 41}).status_code == 422
    assert client.post(f"/documents/{d}/tags", headers=as_ana, json={}).status_code == 422


def test_tag_delete_permissions(client, as_ana, as_bob, admin, up, fes):
    d = up(as_ana)
    p = up(as_ana, "private")
    for t in ("a", "b"):
        client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": t})
    assert client.delete(f"/documents/{d}/tags/a", headers=as_bob).status_code == 403
    r = client.delete(f"/documents/{p}/tags/a", headers=as_bob)
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    assert client.post(f"/documents/{p}/tags", headers=as_bob, json={"tag": "a"}).status_code == 404
    r = client.delete(f"/documents/{d}/tags/A", headers=as_ana)
    assert r.status_code == 200 and r.json() == {"tags": ["b"]}
    assert fes.calls[-1]["script"]["params"] == {"tags": ["b"]}
    assert client.delete(f"/documents/{d}/tags/b", headers=admin).json() == {"tags": []}


def test_tag_without_es_index_is_silent(app, client, as_ana, up):
    app.dependency_overrides[get_es] = lambda: FakeES(missing=True)
    d = up(as_ana)
    assert client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "a"}).status_code == 200


@pytest.mark.integration
def test_tag_change_updates_es(app, client, as_ana, up, settings):
    from roracle.search_index import ensure_index, index_name

    es = Elasticsearch(settings.elastic_url)
    model = f"lib-{uuid.uuid4().hex[:8]}"
    ensure_index(es, model, 4)
    try:
        d = up(as_ana)
        es.index(index=index_name(model), id="c1", document={"doc_id": d, "tags": []}, refresh=True)
        app.dependency_overrides[get_es] = lambda: es
        assert (
            client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "Deep Nets"}).status_code == 200
        )
        assert es.get(index=index_name(model), id="c1")["_source"]["tags"] == ["deep-nets"]
        client.delete(f"/documents/{d}/tags/deep-nets", headers=as_ana)
        assert es.get(index=index_name(model), id="c1")["_source"]["tags"] == []
    finally:
        es.indices.delete(index=index_name(model), ignore_unavailable=True)


# ---- collections
def test_collections_private_and_counts(client, as_ana, as_bob, up):
    a, b = up(as_ana), up(as_ana)
    r = client.post("/collections", headers=as_ana, json={"name": "Reading"})
    assert r.status_code == 201
    c = r.json()
    assert c["name"] == "Reading" and c["count"] == 0
    for d in (a, b, a):
        assert client.put(f"/collections/{c['id']}/items/{d}", headers=as_ana).status_code == 200
    assert client.get("/collections", headers=as_ana).json() == [{**c, "count": 2}]
    assert client.get("/collections", headers=as_bob).json() == []
    assert client.put(f"/collections/{c['id']}/items/{a}", headers=as_bob).status_code == 404
    assert client.delete(f"/collections/{c['id']}/items/{a}", headers=as_bob).status_code == 404
    assert client.delete(f"/collections/{c['id']}/items/{a}", headers=as_ana).status_code == 200
    assert client.delete(f"/collections/{c['id']}/items/{a}", headers=as_ana).status_code == 200
    assert client.get("/collections", headers=as_ana).json()[0]["count"] == 1


def test_collection_rules(client, as_ana, as_bob, up):
    hidden = up(as_ana, "private")
    c = client.post("/collections", headers=as_bob, json={"name": "x"}).json()["id"]
    assert client.put(f"/collections/{c}/items/{hidden}", headers=as_bob).status_code == 404
    assert client.put(f"/collections/{uuid.uuid4()}/items/{hidden}", headers=as_bob).status_code == 404
    assert client.post("/collections", headers=as_bob, json={"name": ""}).status_code == 422
    assert client.post("/collections", headers=as_bob, json={"name": "n" * 101}).status_code == 422


def test_unicode_normalization_is_stable():
    for raw in ("Größe", "数学", "naïve", "ＡＢＣ"):
        n = normalize_tag(raw)
        assert normalize_tag(n) == n
    assert normalize_tag("ＡＢＣ") == "abc"


def test_tag_cap(client, as_ana, up, fes):
    d = up(as_ana)
    for i in range(50):
        assert client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": f"t{i}"}).status_code == 200
    r = client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "extra"})
    assert r.status_code == 422 and r.json()["detail"] == "too many tags"
    assert client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "t3"}).status_code == 200


def test_vote_upsert_from_two_sessions(client, as_ana, up, session_factory, db_session):
    d = up(as_ana)
    assert _vote(client, as_ana, d, value=-1, reason="wrong").status_code == 200
    uid = db_session.query(Vote.user_id).scalar()
    with session_factory() as s2:  # second session upserts the same key
        from sqlalchemy.dialects.postgresql import insert

        s2.execute(
            insert(Vote)
            .values(user_id=uid, document_id=d, value=1)
            .on_conflict_do_update(
                index_elements=[Vote.user_id, Vote.document_id], set_={"value": 1, "reason": None}
            )
        )
        s2.commit()
    db_session.expire_all()
    assert _vote(client, as_ana, d, value=-1).json() == {"up": 0, "down": 1, "mine": -1}
    assert db_session.query(Vote).count() == 1


def test_remove_item_after_losing_access(client, as_ana, as_bob, up, db_session):
    d = up(as_ana)
    c = client.post("/collections", headers=as_bob, json={"name": "x"}).json()["id"]
    assert client.put(f"/collections/{c}/items/{d}", headers=as_bob).status_code == 200
    db_session.execute(Document.__table__.update().where(Document.id == d).values(visibility="private"))
    db_session.flush()
    assert client.put(f"/collections/{c}/items/{d}", headers=as_bob).status_code == 404
    r = client.delete(f"/collections/{c}/items/{d}", headers=as_bob)
    assert r.status_code == 200 and r.json()["count"] == 0


def test_tag_sync_retries_conflicts_and_raises_on_failures(app, client, as_ana, up):
    d = up(as_ana)

    class Seq(FakeES):
        def __init__(self, *r):
            super().__init__()
            self.r = list(r)

        def update_by_query(self, **kw):
            self.calls.append(kw)
            return self.r.pop(0) if self.r else {}

    f = Seq({"version_conflicts": 1}, {})
    app.dependency_overrides[get_es] = lambda: f
    assert client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "ml"}).status_code == 200
    assert len(f.calls) == 2
    bad = Seq({"failures": [{"x": 1}]})
    app.dependency_overrides[get_es] = lambda: bad
    r = client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "dl"})
    assert r.status_code == 503  # committed in PG; durable acl_sync converges ES


def test_tag_es_failure_queues_acl_sync_and_keeps_tag(app, client, as_ana, up, db_session):
    from sqlalchemy import select

    from roracle.models import Job, Tag

    d = up(as_ana)

    class Down(FakeES):
        def update_by_query(self, **kw):
            raise RuntimeError("es down")

    app.dependency_overrides[get_es] = lambda: Down()
    r = client.post(f"/documents/{d}/tags", headers=as_ana, json={"tag": "ml"})
    assert r.status_code == 503
    db_session.expire_all()
    assert db_session.scalars(select(Tag.tag).where(Tag.document_id == d)).all() == ["ml"]
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert [j.payload for j in jobs] == [{"doc_id": d}]
    r = client.delete(f"/documents/{d}/tags/ml", headers=as_ana)
    assert r.status_code == 503
    assert len(db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()) == 2
