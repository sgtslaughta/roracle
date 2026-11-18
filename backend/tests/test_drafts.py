import pytest
from api_seed import FakeES, seed
from conftest import bearer
from sqlalchemy import select

from roracle.models import AuditLog, Document, DocumentShare
from roracle.models_writing import Draft, DraftVersion


@pytest.fixture
def cara(make_token):  # same group as ana
    return bearer(make_token(sub="cara", username="cara", groups=("/Mathematics/Spectral Theory",)))


@pytest.fixture
def dan(make_token):  # co-author via group only
    return bearer(make_token(sub="dan", username="dan", groups=("/Chem",)))


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def mk(client, h, title="T", **kw):
    r = client.post("/drafts", headers=h, json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def uid(client, h):
    return client.get("/me", headers=h).json()["id"]


def test_create_and_read(client, as_ana, ana):
    r = client.post("/drafts", headers=as_ana, json={"title": " Hello ", "content": ""})
    assert r.status_code == 201
    s = r.json()
    assert s["title"] == "Hello" and s["status"] == "draft" and s["latest_version"] == 1
    assert s["owner"]["id"] == str(ana) and s["coauthors"] == {"users": [], "groups": []}
    g = client.get(f"/drafts/{s['id']}", headers=as_ana).json()
    assert g["content"] == "" and [v["version"] for v in g["versions"]] == [1]
    assert client.get(f"/drafts/{s['id']}/versions/1", headers=as_ana).json()["author"]["id"] == str(ana)
    assert client.get(f"/drafts/{s['id']}/versions/2", headers=as_ana).status_code == 404


def test_create_template_profile_validation(client, as_ana, db_session):
    i = mk(client, as_ana, title='Spectral "gaps": a note', profile="dissertation")
    body = client.get(f"/drafts/{i}", headers=as_ana).json()["content"]
    assert body.startswith("---")
    assert '\ntitle: "Spectral \\"gaps\\": a note"\n' in body  # the title is filled in, YAML-quoted
    assert client.post("/drafts", headers=as_ana, json={"title": "x", "profile": "nope"}).status_code == 422
    for bad in ("", "x" * 501):
        assert client.post("/drafts", headers=as_ana, json={"title": bad}).status_code == 422
    d = db_session.scalars(select(Document)).all()
    assert all(x.visibility == "private" and x.org_unit == "/Mathematics/Spectral Theory" for x in d)


def test_stranger_404_everywhere(client, as_ana, as_bob, bob):
    i = mk(client, as_ana)
    for m, u, kw in [
        ("get", f"/drafts/{i}", {}),
        ("get", f"/drafts/{i}/versions/1", {}),
        ("put", f"/drafts/{i}/content", {"json": {"content": "x"}}),
        ("put", f"/drafts/{i}/coauthors", {"json": {"users": []}}),
        ("delete", f"/drafts/{i}", {}),
    ]:
        r = getattr(client, m)(u, headers=as_bob, **kw)
        assert r.status_code == 404 and r.json() == {"detail": "not found"}, u
    assert client.get("/drafts", headers=as_bob).json() == {"items": []}


def test_coauthor_user_and_group(client, as_ana, as_bob, bob, dan, db_session):
    i = mk(client, as_ana)
    assert client.get("/drafts", headers=dan).status_code == 200
    r = client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)], "groups": ["Chem/"]})
    assert r.status_code == 422  # group must start with "/"
    r = client.put(
        f"/drafts/{i}/coauthors",
        headers=as_ana,
        json={"users": [str(bob), uid(client, as_ana)], "groups": ["/Chem/"]},
    )
    assert r.status_code == 200 and r.json()["coauthors"] == {"users": [str(bob)], "groups": ["/Chem"]}
    doc = db_session.get(Document, i)
    assert doc.visibility == "shared"
    shares = {(s.principal_kind, s.principal) for s in db_session.scalars(select(DocumentShare))}
    assert shares == {("user", str(bob)), ("group", "/Chem")}
    for h in (as_bob, dan):
        assert client.get(f"/drafts/{i}", headers=h).status_code == 200
        r = client.put(f"/drafts/{i}/content", headers=h, json={"content": "by co"})
        assert r.status_code == 200
        assert client.put(f"/drafts/{i}/coauthors", headers=h, json={"users": []}).status_code == 403
        assert client.delete(f"/drafts/{i}", headers=h).status_code == 403
        assert len(client.get("/drafts", headers=h).json()["items"]) == 1
    v = db_session.scalars(select(DraftVersion).where(DraftVersion.version == 3)).one()
    assert v.author_id != db_session.get(Document, i).owner_id
    # remove all: back to private, shares gone, co-author loses access
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={})
    db_session.expire_all()
    assert db_session.get(Document, i).visibility == "private"
    assert db_session.scalars(select(DocumentShare)).all() == []
    assert client.get(f"/drafts/{i}", headers=as_bob).status_code == 404


def test_unknown_coauthor_422(client, as_ana):
    i = mk(client, as_ana)
    r = client.put(
        f"/drafts/{i}/coauthors", headers=as_ana, json={"users": ["00000000-0000-0000-0000-000000000001"]}
    )
    assert r.status_code == 422 and r.json()["detail"] == "unknown user"


def test_admin_reads_saves_not_owner_actions(client, as_ana, admin):
    i = mk(client, as_ana)
    assert client.get(f"/drafts/{i}", headers=admin).status_code == 200
    assert client.put(f"/drafts/{i}/content", headers=admin, json={"content": "a"}).status_code == 200
    assert client.delete(f"/drafts/{i}", headers=admin).status_code == 403
    assert client.get("/drafts", headers=admin).json() == {"items": []}  # not admin-everything


def test_save_versions_stale_status_size(client, as_ana, db_session, settings):
    i = mk(client, as_ana)
    r = client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "a", "base_version": 1})
    assert r.json()["version"] == 2
    r = client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "b", "base_version": 1})
    assert r.status_code == 409 and r.json()["detail"] == {"code": "draft_changed", "version": 2}
    assert client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "c"}).json()["version"] == 3
    big = "é" * (settings.draft_max_bytes // 2 + 1)  # 2 bytes each in UTF-8
    assert client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": big}).status_code == 413
    assert client.post("/drafts", headers=as_ana, json={"title": "t", "content": big}).status_code == 413
    d = db_session.get(Draft, i)
    d.status = "in_review"
    db_session.flush()
    assert client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "d"}).status_code == 409
    d.status = "submitted"
    db_session.flush()
    assert client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "d"}).json()["version"] == 4
    assert client.delete(f"/drafts/{i}", headers=as_ana).status_code == 409


def test_delete_hides(client, as_ana, db_session):
    i = mk(client, as_ana)
    assert client.delete(f"/drafts/{i}", headers=as_ana).status_code == 204
    assert client.get(f"/drafts/{i}", headers=as_ana).status_code == 404
    assert client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "x"}).status_code == 404
    assert client.get("/drafts", headers=as_ana).json() == {"items": []}


def test_list_order_and_updated_at(client, as_ana):
    a, b = mk(client, as_ana, "A"), mk(client, as_ana, "B")
    assert [x["id"] for x in client.get("/drafts", headers=as_ana).json()["items"]] == [b, a]
    client.put(f"/drafts/{a}/content", headers=as_ana, json={"content": "x"})
    assert [x["id"] for x in client.get("/drafts", headers=as_ana).json()["items"]] == [a, b]


def test_audit_has_no_content(client, as_ana, db_session):
    i = mk(client, as_ana)
    client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "TOPSECRETBODY"})
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action.like("draft.%"))).all()
    assert {r.action for r in rows} == {"draft.create", "draft.save"}
    assert all(r.target == i and "TOPSECRETBODY" not in str(r.detail) for r in rows)


def test_patch_document_409_after_visibility_checks(client, as_ana, as_bob, bob, db_session):
    i = mk(client, as_ana)
    assert client.patch(f"/documents/{i}", headers=as_bob, json={"title": "x"}).status_code == 404
    r = client.patch(f"/documents/{i}", headers=as_ana, json={"title": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "use the /drafts endpoints"
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    assert client.patch(f"/documents/{i}", headers=as_bob, json={"title": "x"}).status_code == 404


@pytest.mark.usefixtures("_blob_store_override")
def test_drafts_absent_from_lists(client, as_ana, as_bob, ana, bob, cara, db_session):
    from test_search_api import FakeLLM

    from roracle.api.routes.library import get_es
    from roracle.api.routes.search import get_llm

    client.app.dependency_overrides[get_es] = lambda: FakeES()
    client.app.dependency_overrides[get_llm] = lambda: FakeLLM()
    real, _ = seed(db_session, ana, title="real", visibility="everyone")
    i = mk(client, as_ana, "secretdraft")
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    for h in (as_ana, as_bob, cara):
        up = client.get("/me/uploads", headers=h)
        assert up.status_code == 200 and i not in up.text
        assert i not in client.get("/collections", headers=h).text
        s = client.get("/search", params={"q": "secretdraft"}, headers=h)
        assert i not in s.text and "secretdraft" not in s.text
    assert str(real.id) in client.get("/me/uploads", headers=as_ana).text
    assert client.get(f"/export/bibtex?doc_id={i}", headers=as_bob).status_code in (200, 404)
    assert "secretdraft" not in client.get(f"/export/bibtex?doc_id={i}", headers=as_bob).text
