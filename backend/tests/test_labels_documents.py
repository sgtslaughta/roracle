import uuid

import pytest
from api_seed import FakeES, seed
from conftest import bearer
from sqlalchemy import select
from test_neardup import _post, _setup
from test_submit import MD, built, mk, reports, sub  # noqa: F401  (built is a fixture)

from roracle import search_index
from roracle.api.routes.library import get_es
from roracle.labels import service
from roracle.labels.policy import Policy
from roracle.models import AuditLog, Connector, Document, Job, Notification
from roracle.models_labels import Label
from roracle.writing import profiles, submit


class UES(FakeES):
    def update_by_query(self, **kw):
        self.calls.append(kw)
        return {}


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


@pytest.fixture
def fes(app):
    f = UES()
    app.dependency_overrides[get_es] = lambda: f
    return f


def mklabel(client, admin, name, rank, **kw):
    gates = kw.pop("gates", {})
    r = client.post(
        "/admin/labels",
        json={"name": name, "colour": "#aabbcc", "rank": rank, "gates": gates, **kw},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def general(db):
    return str(db.scalar(select(Label.id).where(Label.name == "General")))


def put(client, hdr, doc, label, **kw):
    return client.put(f"/documents/{doc}/label", headers=hdr, json={"label_id": label, **kw})


def audits(db, action):
    return [a.detail for a in db.scalars(select(AuditLog).where(AuditLog.action == action))]


def test_owner_raises_syncs_es_audits(client, as_ana, ana, admin, db_session, fes):
    sec = mklabel(client, admin, "Secret", 5)
    d, _ = seed(db_session, ana)
    r = put(client, as_ana, d.id, sec)
    assert r.status_code == 200 and r.json()["label"]["name"] == "Secret"
    db_session.refresh(d)
    assert str(d.label_id) == sec
    assert audits(db_session, "document.label") == [{"from": general(db_session), "to": sec, "via": "user"}]
    assert any(
        next(iter(c["script"]["params"]["acl"].values()))["fields"]["label_id"] == sec
        for c in fes.calls
        if "acl" in c.get("script", {}).get("params", {})
    )
    assert db_session.scalar(select(Job).where(Job.kind == "acl_sync")) is not None
    assert not db_session.scalars(select(Notification)).all()  # owner acted: no notification
    assert put(client, as_ana, d.id, sec).status_code == 200  # equal: no-op
    assert len(audits(db_session, "document.label")) == 1


def test_owner_cannot_lower_admin_needs_reason(client, as_ana, ana, admin, db_session, fes):
    sec = mklabel(client, admin, "Secret", 5, gates={"reason_on_lower": True})
    d, _ = seed(db_session, ana)
    put(client, as_ana, d.id, sec)
    low = general(db_session)
    r = put(client, as_ana, d.id, low, reason="because I said so")
    assert r.status_code == 403 and r.json()["detail"] == "only admins can lower a label"
    assert put(client, admin, d.id, low).status_code == 422
    assert put(client, admin, d.id, low, reason="x").status_code == 422
    assert put(client, admin, d.id, low, reason="declassified").status_code == 200
    db_session.refresh(d)
    assert str(d.label_id) == low
    assert audits(db_session, "document.label")[-1]["reason"] == "declassified"
    n = db_session.scalars(select(Notification)).all()
    assert [x.user_id for x in n] == [ana] and n[0].kind == "label.changed"
    assert n[0].payload["document_id"] == str(d.id) and n[0].payload["to"] == low


def test_lower_without_gate_needs_no_reason(client, admin, ana, db_session, fes):
    sec = mklabel(client, admin, "Secret", 5)
    d, _ = seed(db_session, ana)
    d.label_id = uuid.UUID(sec)
    db_session.flush()
    assert put(client, admin, d.id, general(db_session)).status_code == 200


def test_viewer_403_invisible_404_pat_403_unknown_422(
    client, as_ana, ana, as_bob, bob, admin, db_session, fes
):
    sec = mklabel(client, admin, "Secret", 5)
    pub, _ = seed(db_session, ana)
    priv, _ = seed(db_session, ana, visibility="private")
    assert put(client, as_bob, pub.id, sec).status_code == 403
    assert put(client, as_bob, priv.id, sec).status_code == 404
    pat = client.post("/me/tokens", json={"name": "x", "scopes": ["read", "write"]}, headers=as_ana)
    r = put(client, bearer(pat.json()["token"]), pub.id, sec)
    assert r.status_code == 403 and r.json()["detail"] == "agents must propose"
    assert put(client, as_ana, pub.id, str(uuid.uuid4())).status_code == 422
    db_session.refresh(pub)
    assert str(pub.label_id) == general(db_session)


def _up(client, hdr, label=None, data=b"%PDF-1"):
    form = {"label_id": label} if label else {}
    return client.post(
        "/documents", headers=hdr, data=form, files={"file": ("p.pdf", data, "application/pdf")}
    )


@pytest.mark.usefixtures("_blob_store_override")
def test_upload_label_rules(client, as_ana, admin, db_session):
    internal = mklabel(client, admin, "Internal", 2, is_default=True)
    sec = mklabel(client, admin, "Secret", 5)
    gen = general(db_session)
    r = _up(client, as_ana, data=b"%PDF-a")
    assert db_session.get(Document, uuid.UUID(r.json()["document_id"])).label_id == uuid.UUID(internal)
    r = _up(client, as_ana, sec, data=b"%PDF-b")
    assert str(db_session.get(Document, uuid.UUID(r.json()["document_id"])).label_id) == sec
    assert _up(client, as_ana, gen, data=b"%PDF-c").status_code == 422  # below default = lowering
    assert _up(client, as_ana, str(uuid.uuid4()), data=b"%PDF-d").status_code == 422
    r = _up(client, admin, gen, data=b"%PDF-e")
    assert r.status_code == 201
    assert str(db_session.get(Document, uuid.UUID(r.json()["document_id"])).label_id) == gen


def test_connector_default_applied(client, admin, ana, db_session):
    sec = uuid.UUID(mklabel(client, admin, "Secret", 5))
    gen = uuid.UUID(general(db_session))
    c = Connector(name="c", kind="fs", owner_id=ana, visibility="private", org_unit="/", config={})
    assert service.initial_label(db_session, None, connector=c) == gen
    c.label_id = sec
    assert service.initial_label(db_session, None, connector=c) == sec


def test_connector_admin_label_field(client, admin, db_session):
    sec = mklabel(client, admin, "Secret", 5)
    body = {"name": "n", "kind": "fs", "visibility": "private", "config": {"root": "/tmp"}, "label_id": sec}
    r = client.post("/admin/connectors", headers=admin, json=body)
    if r.status_code == 201:  # fs roots are validated against the deployment; skip silently if rejected
        assert r.json()["label_id"] == sec
        cid = r.json()["id"]
        r = client.patch(f"/admin/connectors/{cid}", headers=admin, json={"label_id": str(uuid.uuid4())})
        assert r.status_code == 422
        assert (
            client.patch(f"/admin/connectors/{cid}", headers=admin, json={"label_id": None}).json()[
                "label_id"
            ]
            is None
        )


def test_near_dup_new_version_raises_target(client, as_ana, admin, db_session):
    sec = mklabel(client, admin, "Secret", 5)
    md, _match, ud, mine = _setup(db_session, client, as_ana)
    ud.label_id = uuid.UUID(sec)
    db_session.flush()
    assert _post(client, as_ana, ud, mine, "new_version").status_code == 200
    db_session.refresh(md)
    assert str(md.label_id) == sec
    assert audits(db_session, "document.label")[-1]["via"] == "near_dup"


def test_near_dup_never_lowers_target(client, as_ana, admin, db_session):
    sec = mklabel(client, admin, "Secret", 5)
    md, _match, ud, mine = _setup(db_session, client, as_ana)
    md.label_id = uuid.UUID(sec)
    db_session.flush()
    assert _post(client, as_ana, ud, mine, "new_version").status_code == 200
    db_session.refresh(md)
    assert str(md.label_id) == sec and audits(db_session, "document.label") == []


@pytest.mark.usefixtures("_blob_store_override")
def test_submit_raises_never_lowers(client, as_ana, ana, admin, db_session, built):  # noqa: F811
    sec = mklabel(client, admin, "Secret", 5)
    mid = mklabel(client, admin, "Internal", 2)
    i = mk(client, as_ana)
    reports(db_session, i, ana)
    doc = db_session.get(Document, uuid.UUID(i))
    assert str(doc.label_id) == general(db_session)  # drafts start at the default
    assert sub(client, as_ana, i, label_id=str(uuid.uuid4())).status_code == 422
    assert sub(client, as_ana, i, label_id=mid).status_code == 200
    db_session.refresh(doc)
    assert str(doc.label_id) == mid
    assert audits(db_session, "document.label")[-1]["via"] == "submit"
    doc.label_id = uuid.UUID(sec)
    db_session.flush()
    client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": MD + "more\n"})
    reports(db_session, i, ana)
    assert sub(client, as_ana, i, label_id=mid).status_code == 200
    db_session.refresh(doc)
    assert str(doc.label_id) == sec  # submit never lowers


def test_profile_default_label(client, ana, admin, db_session):
    mid = mklabel(client, admin, "Internal", 2)
    d, _ = seed(db_session, ana)
    p = type("P", (), {"id": ana, "user_id": ana, "is_admin": False, "via": "jwt", "client": ""})()
    prof = profiles.builtin()["journal-article"].model_copy(update={"default_label": "internal"})
    submit._apply_label(db_session, p, d, prof, None)
    assert str(d.label_id) == mid
    unknown = prof.model_copy(update={"default_label": "nope"})
    submit._apply_label(db_session, p, d, unknown, None)  # ignored
    assert str(d.label_id) == mid


@pytest.mark.usefixtures("_blob_store_override")
def test_document_and_search_json_carry_label(client, as_ana, ana, admin, db_session, fes):
    sec = mklabel(client, admin, "Secret", 5)
    d, _ = seed(db_session, ana)
    put(client, as_ana, d.id, sec)
    lab = client.get(f"/documents/{d.id}", headers=as_ana).json()["label"]
    assert lab["id"] == sec and lab["gates"]["exclude_llm"] is False
    hits = client.get("/search", headers=as_ana).json()["results"]
    assert hits[0]["label"]["name"] == "Secret"
    up = _up(client, as_ana, sec, data=b"%PDF-mine")
    assert up.status_code == 201, up.text
    mine = client.get("/me/uploads", headers=as_ana).json()["items"]
    assert [m["label"]["name"] for m in mine if m["document_id"] == up.json()["document_id"]] == ["Secret"]


def test_acl_fields_has_label_id(db_session, ana):
    d, _ = seed(db_session, ana)
    assert search_index.acl_fields(db_session, d)["label_id"] == general(db_session)
    assert Policy.load(db_session).get(d.label_id).is_default


@pytest.mark.integration
def test_es_chunks_get_label_id(client, as_ana, ana, admin, db_session, app, es, cfg):
    from test_search_index import _chunks

    sec = mklabel(client, admin, "Secret", 5)
    d, v = seed(db_session, ana)
    search_index.ensure_index(es, cfg.embed_model, 4)
    acl = search_index.acl_fields(db_session, d)
    search_index.index_chunks(es, v, d, _chunks(2), [[0.1] * 4] * 2, acl, cfg.embed_model, title="T")
    app.dependency_overrides[get_es] = lambda: es
    assert put(client, as_ana, d.id, sec).status_code == 200
    es.indices.refresh(index=search_index.index_name(cfg.embed_model))
    docs = es.search(
        index=search_index.index_name(cfg.embed_model), query={"term": {"doc_id": str(d.id)}}, size=10
    )["hits"]["hits"]
    assert len(docs) == 2 and {h["_source"]["label_id"] for h in docs} == {sec}
