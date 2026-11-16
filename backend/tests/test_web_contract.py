"""P11b task 4: web contract additions (handoffs, notes, proposals, notifications, admin)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from api_seed import seed
from conftest import bearer
from ingest_helpers import make_version
from sqlalchemy import select
from test_evidence import PROV

from roracle.auth import Principal
from roracle.models import (
    Annotation,
    AnnotationShare,
    Connector,
    ConnectorRun,
    Job,
    Notification,
    Proposal,
    Tag,
    User,
)
from roracle.notes import proposals
from roracle.notes.community import attach_notes

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


def mk_note(db, d, v, author, **kw):
    kw.setdefault("kind", "finding")
    kw.setdefault("visibility", "everyone")
    a = Annotation(document_id=d.id, version_id=v.id, author_id=author, body="b", **kw)
    db.add(a)
    db.flush()
    return a


# --- handoffs -------------------------------------------------------------------------------------


def test_handoff_documents_visible_only_and_shares_creator_only(client, as_ana, as_bob, db_session, ana, bob):
    pub = seed(db_session, ana, title="PubT", visibility="everyone")
    priv = seed(db_session, ana, title="SecretT", visibility="private")
    client.get("/me", headers=as_bob)
    db_session.commit()
    body = {
        "kind": "agent", "audience": "shared", "doc_ids": [str(pub[0].id), str(priv[0].id)],
        "shares": [{"kind": "user", "principal": str(bob)}],
    }  # fmt: skip
    m = client.post("/handoffs", json=body, headers=as_ana).json()
    assert m["documents"] == [
        {"id": str(pub[0].id), "title": "PubT"},
        {"id": str(priv[0].id), "title": "SecretT"},
    ]
    assert m["shares"] == [{"kind": "user", "principal": str(bob)}]
    b = client.get(f"/handoffs/{m['code']}", headers=as_bob).json()
    assert b["documents"] == [{"id": str(pub[0].id), "title": "PubT"}]
    assert "shares" not in b and "SecretT" not in json.dumps(b) and str(priv[0].id) not in json.dumps(b)


def test_handoff_shares_visible_to_admin(client, as_ana, adm, db_session, ana, bob):
    m = client.post(
        "/handoffs",
        json={"kind": "agent", "audience": "shared", "shares": [{"kind": "group", "principal": "g"}]},
        headers=as_ana,
    ).json()
    from roracle import handoff
    from roracle.models import Handoff

    adm_p = Principal(ana, "a", "adm", (), True)
    assert handoff.view(db_session, adm_p, db_session.get(Handoff, m["code"]))["shares"] == [
        {"kind": "group", "principal": "g"}
    ]


def test_handoff_url_absolute_when_base_set(client, app, as_ana):
    m = client.post("/handoffs", json={"kind": "agent"}, headers=as_ana).json()
    assert m["url"] == f"/h/{m['display']}"
    app.state.settings = app.state.settings.model_copy(update={"public_base_url": "https://r.example.org/"})
    m2 = client.post("/handoffs", json={"kind": "agent"}, headers=as_ana).json()
    assert m2["url"] == f"https://r.example.org/h/{m2['display']}"
    assert (
        client.get(f"/handoffs/{m2['code']}", headers=as_ana)
        .json()["url"]
        .startswith("https://r.example.org/h/")
    )
    assert all(
        i["url"].startswith("https://r.example.org/h/")
        for i in client.get("/handoffs", headers=as_ana).json()
    )


# --- annotations / proposals ----------------------------------------------------------------------


def test_annotation_list_paging_total(client, as_ana, as_bob, db_session, ana, bob):
    d, v = seed(db_session, ana, visibility="everyone")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        mk_note(db_session, d, v, ana, created_at=base + timedelta(minutes=i))
    mk_note(db_session, d, v, ana, visibility="private")  # not readable by bob
    db_session.commit()
    r = client.get(f"/documents/{d.id}/annotations?limit=2&offset=1", headers=as_bob).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    assert client.get(f"/documents/{d.id}/annotations", headers=as_ana).json()["total"] == 4
    assert client.get(f"/documents/{d.id}/annotations?limit=500", headers=as_bob).status_code == 422
    priv, _ = seed(db_session, ana, visibility="private")
    db_session.commit()
    assert client.get(f"/documents/{priv.id}/annotations?limit=2", headers=as_bob).json() == {
        "detail": "not found"
    }


def _prop(db, uid, doc_id):
    p = Principal(user_id=uid, sub="x", username="x", groups=(), is_admin=False, client="c", via="pat")
    row = proposals.propose(db, p, "note", {"document_id": str(doc_id), "body": "b"})
    db.commit()
    return row


def test_proposals_paging_and_count(client, as_ana, as_bob, db_session, ana, bob):
    d, _ = seed(db_session, ana, visibility="everyone")
    rows = [_prop(db_session, ana, d.id) for _ in range(3)]
    rows[0].status = "approved"
    db_session.commit()
    r = client.get("/me/proposals?limit=2&offset=1", headers=as_ana).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    assert client.get("/me/proposals/count", headers=as_ana).json() == {"pending": 2}
    assert client.get("/me/proposals/count", headers=as_bob).json() == {"pending": 0}
    assert db_session.scalar(select(Proposal.id).limit(1)) is not None


# --- search notes_total ---------------------------------------------------------------------------


def test_notes_total_counts_only_readable(db_session, ana, bob):
    d, v = seed(db_session, ana, visibility="everyone")
    priv_doc, pv = seed(db_session, ana, visibility="private")
    mk_note(db_session, d, v, ana)
    mk_note(db_session, d, v, ana)
    mk_note(db_session, d, v, ana, visibility="private")
    shared = mk_note(db_session, d, v, ana, visibility="shared")
    db_session.add(AnnotationShare(annotation_id=shared.id, principal_kind="user", principal=str(bob)))
    mk_note(db_session, d, v, ana, status="removed")
    mk_note(db_session, priv_doc, pv, ana)
    db_session.flush()
    hits = [
        {"doc_id": str(d.id), "passages": []},
        {"doc_id": str(priv_doc.id), "passages": []},
        {"doc_id": "junk", "passages": []},
    ]
    attach_notes(db_session, Principal(bob, "b", "bob", (), False), hits, False)
    assert [h["notes_total"] for h in hits] == [3, 0, 0]
    hits = [{"doc_id": str(d.id), "passages": []}]
    attach_notes(db_session, Principal(ana, "a", "ana", (), False), hits, False)
    assert hits[0]["notes_total"] == 4  # own private + shared + 2 everyone; removed excluded


# --- evidence / notifications ---------------------------------------------------------------------


def test_evidence_created_by_name(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    nid = client.post(
        f"/documents/{d.id}/annotations", json={"body": "x", "kind": "finding"}, headers=as_ana
    ).json()["id"]
    meta = {**PROV, "stance": "supports"}
    r = client.post(
        f"/annotations/{nid}/evidence",
        headers=as_ana,
        data={"meta": json.dumps(meta)},
        files={"file": ("r.txt", b"x")},
    )
    assert r.status_code in (200, 201), r.text
    j = r.json()
    assert j["created_by"] == str(ana) and j["created_by_name"] == "ana"
    assert (
        client.get(f"/annotations/{nid}/evidence", headers=as_ana).json()["items"][0]["created_by_name"]
        == "ana"
    )


def test_notifications_since_id_and_max_id(client, as_ana, ana, db_session, bob):
    for i in range(3):
        db_session.add(Notification(user_id=ana, kind="k", payload={"i": i}))
    db_session.add(Notification(user_id=bob, kind="k", payload={}))
    db_session.commit()
    allr = client.get("/me/notifications", headers=as_ana).json()
    ids = [n["id"] for n in allr["items"]]
    assert ids == sorted(ids, reverse=True) and allr["max_id"] == ids[0]
    r = client.get(f"/me/notifications?since_id={ids[2]}", headers=as_ana).json()
    assert [n["id"] for n in r["items"]] == ids[:2] and r["max_id"] == ids[0]
    r = client.get(f"/me/notifications?since_id={ids[0]}", headers=as_ana).json()
    assert r["items"] == [] and r["max_id"] == ids[0]


# --- admin ----------------------------------------------------------------------------------------


def test_admin_tags_groups_paged(client, adm, db_session, user, store):
    docs = [make_version(db_session, store, user).document_id for _ in range(3)]
    for i, d in enumerate(docs):
        db_session.add(Tag(document_id=d, tag=f"t{i}"))
    db_session.add(Tag(document_id=docs[0], tag="t1"))
    db_session.commit()
    r = client.get("/admin/tags", headers=adm).json()
    assert r["total"] == 3 and r["items"][0] == {"tag": "t1", "documents": 2}
    r = client.get("/admin/tags?q=T2&limit=1", headers=adm).json()
    assert r == {"items": [{"tag": "t2", "documents": 1}], "total": 1}
    assert len(client.get("/admin/tags?limit=2&offset=2", headers=adm).json()["items"]) == 1
    db_session.add_all(
        [User(sub="g1", username="g1", groups=["/A", "/B"]), User(sub="g2", username="g2", groups=["/B"])]
    )
    db_session.commit()
    g = client.get("/admin/groups", headers=adm).json()
    assert g["total"] == 2 and g["items"] == [{"group": "/A", "members": 1}, {"group": "/B", "members": 2}]
    assert client.get("/admin/groups?q=b&limit=1", headers=adm).json() == {
        "items": [{"group": "/B", "members": 2}],
        "total": 1,
    }
    assert client.get("/admin/groups?q=zzzz", headers=adm).json() == {"items": [], "total": 0}
    assert client.get("/admin/groups?limit=0", headers=adm).status_code == 422


def test_admin_stats_top(client, adm, db_session, user, store):
    for i in range(3):
        u = User(sub=f"s{i}", username=f"u{i}")
        db_session.add(u)
        db_session.flush()
        make_version(db_session, store, u)
    db_session.commit()
    assert len(client.get("/admin/stats", headers=adm).json()["top_uploaders"]) == 3
    assert len(client.get("/admin/stats?top=2", headers=adm).json()["top_uploaders"]) == 2
    assert client.get("/admin/stats?top=51", headers=adm).status_code == 422


def test_admin_settings_schema(client, adm, as_ana):
    from roracle import runtime

    assert client.get("/admin/settings/schema", headers=adm).json() == runtime.SettingsIn.model_json_schema()
    assert client.get("/admin/settings/schema", headers=as_ana).status_code == 403


def test_tag_merge_returns_ids_and_target(client, adm, db_session, user, store):
    a, b = (make_version(db_session, store, user).document_id for _ in range(2))
    db_session.add_all([Tag(document_id=a, tag="ml"), Tag(document_id=b, tag="ml")])
    db_session.commit()
    r = client.post("/admin/tags/merge", json={"sources": ["ml"], "target": "ai"}, headers=adm).json()
    assert (
        r["documents"] == 2 and r["target"] == "ai" and sorted(r["document_ids"]) == sorted([str(a), str(b)])
    )


def test_connector_last_run_queued(client, adm, db_session, user):
    c = Connector(name="c1", kind="s3", owner_id=user.id, visibility="org_unit", org_unit="/", config={})
    db_session.add(c)
    db_session.commit()
    assert client.get(f"/admin/connectors/{c.id}", headers=adm).json()["last_run"] is None
    db_session.add(
        ConnectorRun(connector_id=c.id, status="ok", finished_at=datetime.now(UTC), stats={"n": 1})
    )
    j = Job(kind="connector_run", status="queued", payload={"connector_id": str(c.id)})
    db_session.add(j)
    db_session.commit()
    lr = client.get(f"/admin/connectors/{c.id}", headers=adm).json()["last_run"]
    assert lr["status"] == "queued" and lr["job_id"] == j.id
    assert lr["finished_at"] is None and lr["stats"] == {}  # the previous run's result must not leak
    lr = client.get("/admin/connectors", headers=adm).json()["items"][0]["last_run"]
    assert lr["status"] == "queued" and lr["job_id"] == j.id
    j.status = "running"
    db_session.commit()
    assert client.get(f"/admin/connectors/{c.id}", headers=adm).json()["last_run"]["status"] == "ok"
