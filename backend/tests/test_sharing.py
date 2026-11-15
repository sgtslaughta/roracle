"""PATCH /documents/{id}: authorization, validation and ES ACL sync (unit with FakeES)."""

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import select

from roracle.api.routes.library import get_es
from roracle.models import Document, DocumentShare, Job

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FakeES:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def update_by_query(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("es down")


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    return f


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def patch(client, h, d, **body):
    return client.patch(f"/documents/{d.id}", headers=h, json=body)


def test_empty_body_422(client, as_ana, ana, db_session, fes):
    d, _ = seed(db_session, ana)
    assert patch(client, as_ana, d).status_code == 422
    assert patch(client, as_ana, d, title=None).status_code == 422
    assert fes.calls == []


def test_owner_updates_and_es_params(client, as_ana, ana, bob, db_session, fes):
    d, _ = seed(db_session, ana, title="Old")
    r = patch(client, as_ana, d, title=" New ", visibility="shared", share_users=[str(bob), str(ana)],
              share_groups=["/Physics/", "//Math//Sub"])  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "New" and r.json()["visibility"] == "shared"
    (call,) = fes.calls
    assert call["query"] == {"term": {"doc_id": str(d.id)}}
    assert call["refresh"] is True and call["conflicts"] == "proceed"
    assert "New" not in call["script"]["source"]
    assert call["script"]["params"] == {
        "owner_id": str(ana), "visibility": "shared", "org_unit": "/", "title": "New",
        "share_users": [str(bob)], "share_groups": ["/Math/Sub", "/Physics"],
    }  # fmt: skip


def test_shares_replace_and_title_omitted(client, as_ana, ana, bob, db_session, fes):
    d, _ = seed(db_session, ana)
    patch(client, as_ana, d, share_users=[str(bob)], share_groups=["/A"])
    patch(client, as_ana, d, share_users=[])
    rows = db_session.execute(select(DocumentShare.principal_kind, DocumentShare.principal)).all()
    assert [tuple(x) for x in rows] == [("group", "/A")]
    assert fes.calls[-1]["script"]["params"]["title"] is None
    assert fes.calls[-1]["script"]["params"]["share_users"] == []


def test_unknown_user_422(client, as_ana, ana, db_session, fes):
    d, _ = seed(db_session, ana)
    r = patch(client, as_ana, d, share_users=["00000000-0000-0000-0000-000000000009"])
    assert r.status_code == 422 and r.json()["detail"] == "unknown user"
    assert fes.calls == []


@pytest.mark.parametrize(
    ("raw", "norm"), [("/A/B/", "/A/B"), ("/", "/"), ("//A///B", "/A/B"), (" /X ", "/X")]
)
def test_org_unit_normalised(client, as_ana, ana, db_session, fes, raw, norm):
    d, _ = seed(db_session, ana)
    r = patch(client, as_ana, d, org_unit=raw)
    assert r.status_code == 200 and r.json()["org_unit"] == norm
    assert fes.calls[-1]["script"]["params"]["org_unit"] == norm


@pytest.mark.parametrize("bad", ["A/B", "", "   "])
def test_org_unit_invalid(client, as_ana, ana, db_session, fes, bad):
    d, _ = seed(db_session, ana)
    assert patch(client, as_ana, d, org_unit=bad).status_code == 422


def test_non_owner_404_invisible_403_visible(client, as_ana, as_bob, ana, db_session, fes):
    hidden, _ = seed(db_session, ana, visibility="private")
    seen, _ = seed(db_session, ana, visibility="everyone")
    assert patch(client, as_bob, hidden, title="x").json() == {"detail": "not found"}
    assert patch(client, as_bob, hidden, title="x").status_code == 404
    assert patch(client, as_bob, seen, title="x").status_code == 403
    unready, _ = seed(db_session, ana, visibility="everyone", stage="parsed")
    assert patch(client, as_bob, unready, title="x").status_code == 404  # R16
    assert fes.calls == []
    assert db_session.get(Document, seen.id).title == "Doc"


def test_admin_can_patch(client, admin, ana, db_session, fes):
    d, _ = seed(db_session, ana, visibility="private")
    assert patch(client, admin, d, visibility="everyone").status_code == 200
    assert fes.calls[0]["script"]["params"]["owner_id"] == str(ana)


def test_missing_index_skipped(client, as_ana, ana, db_session, app):
    from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
    from elasticsearch import NotFoundError

    class Missing:
        def update_by_query(self, **kw):
            meta = ApiResponseMeta(404, "1.1", HttpHeaders(), 0.0, NodeConfig("http", "x", 9200))
            raise NotFoundError("index_not_found_exception", meta, {})

    app.dependency_overrides[get_es] = Missing
    d, _ = seed(db_session, ana)
    assert patch(client, as_ana, d, title="T").status_code == 200


class SeqES(FakeES):
    """Returns the queued responses in order (then {}), like update_by_query would."""

    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)

    def update_by_query(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0) if self.responses else {}


def test_es_failure_503_db_unchanged_and_job_queued(app, client, as_ana, ana, bob, db_session):
    f = FakeES(fail=True)
    app.dependency_overrides[get_es] = lambda: f
    d, _ = seed(db_session, ana, title="Keep", visibility="everyone")
    db_session.commit()
    r = patch(client, as_ana, d, title="X", visibility="private", share_users=[str(bob)])
    assert r.status_code == 503 and r.json()["detail"] == "search index unavailable"
    db_session.expire_all()
    assert db_session.get(Document, d.id).title == "Keep"
    assert db_session.get(Document, d.id).visibility == "everyone"
    assert db_session.scalars(select(DocumentShare)).all() == []
    assert len(f.calls) == 2  # failed update, then re-sync of the committed state (also fails)
    assert f.calls[1]["script"]["params"]["visibility"] == "everyone"
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert [j.payload for j in jobs] == [{"doc_id": str(d.id)}]  # durable convergence, not silent


def test_success_enqueues_acl_sync_in_same_commit(client, as_ana, ana, db_session, fes):
    d, _ = seed(db_session, ana)
    patch(client, as_ana, d, title="T")
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert [j.payload for j in jobs] == [{"doc_id": str(d.id)}]


def test_version_conflicts_retry_then_succeed(app, client, as_ana, ana, db_session):
    f = SeqES({"version_conflicts": 2}, {"version_conflicts": 1}, {"version_conflicts": 0})
    app.dependency_overrides[get_es] = lambda: f
    d, _ = seed(db_session, ana)
    assert patch(client, as_ana, d, title="T").status_code == 200
    assert len(f.calls) == 3


@pytest.mark.parametrize("resp", [{"failures": [{"cause": "x"}]}, {"version_conflicts": 1}])
def test_persistent_failures_or_conflicts_503(app, client, as_ana, ana, db_session, resp):
    f = SeqES(*[resp] * (1 if "failures" in resp else 3))  # recovery sync then succeeds
    app.dependency_overrides[get_es] = lambda: f
    d, _ = seed(db_session, ana, title="Keep")
    db_session.commit()
    assert patch(client, as_ana, d, title="X").status_code == 503
    db_session.expire_all()
    assert db_session.get(Document, d.id).title == "Keep"
    assert len(f.calls) == (2 if "failures" in resp else 4)  # failures raise at once; conflicts retry 3x
    assert db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all() == []


def test_commit_fails_after_es_success_resyncs(app, client, as_ana, ana, db_session, monkeypatch):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    d, _ = seed(db_session, ana, title="Keep", visibility="everyone")
    db_session.commit()
    real = db_session.commit
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("commit failed")
        real()

    monkeypatch.setattr(db_session, "commit", flaky)
    assert patch(client, as_ana, d, visibility="private").status_code == 503
    assert [c["script"]["params"]["visibility"] for c in f.calls] == ["private", "everyone"]
    db_session.expire_all()
    assert db_session.get(Document, d.id).visibility == "everyone"


def test_acl_sync_handler_recomputes_from_pg(session_factory, ana, db_session):
    from roracle.acl_sync import handle_acl_sync

    d, _ = seed(db_session, ana, title="Real", visibility="private")
    db_session.commit()
    f = FakeES()
    handle_acl_sync(db_session, {"doc_id": str(d.id)}, es=f, session_factory=session_factory)
    (c,) = f.calls
    assert c["query"] == {"term": {"doc_id": str(d.id)}}
    assert c["script"]["params"]["visibility"] == "private" and c["script"]["params"]["title"] == "Real"
    d2, _ = seed(db_session, ana, title=None)
    db_session.commit()
    handle_acl_sync(db_session, {"doc_id": str(d2.id)}, es=f, session_factory=session_factory)
    assert f.calls[-1]["script"]["params"]["title"] == "none"
    f2 = FakeES(fail=True)
    with pytest.raises(RuntimeError):
        handle_acl_sync(db_session, {"doc_id": str(d.id)}, es=f2, session_factory=session_factory)
