import uuid

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import select

from roracle.models import Annotation, AnnotationShare, AnnotationVote, DocumentVersion, Evidence

NF = {"detail": "not found"}


@pytest.fixture
def doc(db_session, ana):
    d, v = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    return d, v


def post(client, h, doc_id, **kw):
    return client.post(f"/documents/{doc_id}/annotations", json={"body": "hi", **kw}, headers=h)


def ids(client, h, doc_id):
    r = client.get(f"/documents/{doc_id}/annotations", headers=h)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_create_json_shape_and_version(client, as_ana, ana, doc):
    d, v = doc
    r = post(client, as_ana, d.id, kind="finding", anchor={"quote": "q", "page": 2})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["document_id"] == str(d.id) and j["version_id"] == str(v.id) and j["version"] == 1
    assert j["visibility"] == "everyone" and j["status"] == "visible" and j["parent_id"] is None
    assert j["author"] == {"id": str(ana), "username": "ana"}
    assert j["votes"] == {"up": 0, "down": 0, "mine": 0, "score": 0.5}
    assert j["evidence"] == {"supports": 0, "refutes": 0, "inconclusive": 0}
    assert j["relation"] is None and j["may_not_apply_to_version"] is None and j["shares"] == []
    assert j["anchor"]["quote"] == "q" and j["anchor"]["prefix"] == "" and j["agent_assisted"] is False


def test_votes_and_evidence_counts(client, as_ana, ana, bob, doc, db_session):
    d, _ = doc
    n = uuid.UUID(post(client, as_ana, d.id, kind="finding").json()["id"])
    db_session.add_all(
        [
            AnnotationVote(annotation_id=n, user_id=ana, value=1),
            AnnotationVote(annotation_id=n, user_id=bob, value=-1),
        ]
    )
    db_session.add(Evidence(annotation_id=n, creator_id=ana, stance="refutes", crate={}))
    db_session.commit()
    j = client.get(f"/annotations/{n}", headers=as_ana).json()
    assert j["votes"] == {"up": 1, "down": 1, "mine": 1, "score": 0.5}
    assert j["evidence"]["refutes"] == 1


def test_no_doc_access_404(client, as_ana, as_bob, ana, bob, db_session):
    d, _ = seed(db_session, ana, visibility="private")
    db_session.commit()
    assert client.get(f"/documents/{d.id}/annotations", headers=as_bob).status_code == 404
    assert post(client, as_bob, d.id).status_code == 404
    nid = post(client, as_ana, d.id).json()["id"]
    assert client.get(f"/annotations/{nid}", headers=as_bob).json() == NF


def test_everyone_note_hidden_when_doc_becomes_private(client, as_ana, as_bob, ana, bob, doc, db_session):
    d, _ = doc
    nid = post(client, as_ana, d.id).json()["id"]
    assert client.get(f"/annotations/{nid}", headers=as_bob).status_code == 200
    d.visibility = "private"
    db_session.commit()
    assert client.get(f"/annotations/{nid}", headers=as_bob).status_code == 404
    assert client.get(f"/annotations/{nid}", headers=as_ana).status_code == 200


def test_non_owner_needs_searchable(client, as_ana, as_bob, ana, bob, db_session):
    d, _ = seed(db_session, ana, visibility="everyone", stage="parsed")
    db_session.commit()
    assert client.get(f"/documents/{d.id}/annotations", headers=as_bob).status_code == 404
    assert post(client, as_ana, d.id).status_code == 201  # owner may annotate


def test_private_note_visibility(client, as_ana, as_bob, make_token, ana, bob, doc):
    d, _ = doc
    nid = post(client, as_ana, d.id, visibility="private").json()["id"]
    assert ids(client, as_bob, d.id) == []
    assert client.get(f"/annotations/{nid}", headers=as_bob).json() == NF
    assert ids(client, as_ana, d.id) == [nid]
    admin = bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))
    assert ids(client, admin, d.id) == [nid]


def test_shared_with_user_and_group(client, as_ana, as_bob, make_token, ana, bob, doc):
    d, _ = doc
    n1 = post(
        client, as_ana, d.id, visibility="shared", shares=[{"kind": "user", "principal": str(bob)}]
    ).json()
    assert n1["shares"] == [{"kind": "user", "principal": str(bob)}]
    n2 = post(
        client, as_ana, d.id, visibility="shared", shares=[{"kind": "group", "principal": "/Physics"}]
    ).json()
    n3 = post(
        client, as_ana, d.id, visibility="shared", shares=[{"kind": "group", "principal": "/Other"}]
    ).json()
    got = client.get(f"/documents/{d.id}/annotations", headers=as_bob).json()["items"]
    assert {i["id"] for i in got} == {n1["id"], n2["id"]}
    assert all(i["shares"] == [] for i in got)  # shares only for author
    assert n3["id"] not in {i["id"] for i in got}


def test_relation_target_invisible_hides_note(client, as_ana, as_bob, ana, bob, doc, db_session):
    d, _ = doc
    t, _ = seed(db_session, ana, visibility="private")
    db_session.commit()
    rel = {"type": "extends", "target_document_id": str(t.id)}
    assert post(client, as_bob, d.id, kind="relation", relation=rel).status_code == 404
    r = post(client, as_ana, d.id, kind="relation", relation=rel)
    assert r.status_code == 201 and r.json()["relation"] == rel
    assert ids(client, as_bob, d.id) == []
    assert client.get(f"/annotations/{r.json()['id']}", headers=as_bob).json() == NF
    # target == doc rejected
    bad = {"type": "extends", "target_document_id": str(d.id)}
    assert post(client, as_ana, d.id, kind="relation", relation=bad).status_code == 422


def test_validation_422(client, as_ana, doc):
    d, _ = doc
    assert post(client, as_ana, d.id, kind="bogus").status_code == 422
    assert post(client, as_ana, d.id, kind="relation").status_code == 422
    rel = {"type": "extends", "target_document_id": str(d.id)}
    assert post(client, as_ana, d.id, kind="comment", relation=rel).status_code == 422
    assert post(client, as_ana, d.id, body="x" * 10001).status_code == 422
    assert post(client, as_ana, d.id, anchor={"quote": "x" * 1001}).status_code == 422
    assert post(client, as_ana, d.id, anchor={"quote": "x", "page": 0}).status_code == 422
    sh = [{"kind": "user", "principal": str(i)} for i in range(51)]
    assert post(client, as_ana, d.id, visibility="shared", shares=sh).status_code == 422


def test_replies(client, as_ana, as_bob, ana, bob, doc):
    d, _ = doc
    root = post(client, as_ana, d.id, anchor={"quote": "q"}).json()
    r1 = client.post(f"/annotations/{root['id']}/replies", json={"body": "r1"}, headers=as_bob)
    assert r1.status_code == 201
    r1 = r1.json()
    assert r1["parent_id"] == root["id"] and r1["anchor"] is None and r1["relation"] is None
    r2 = client.post(f"/annotations/{r1['id']}/replies", json={"body": "r2"}, headers=as_ana).json()
    assert r2["parent_id"] == root["id"]  # reply to reply attaches to root
    assert ids(client, as_bob, d.id) == [root["id"], r1["id"], r2["id"]]


def test_reply_follows_root_readability(client, as_ana, as_bob, ana, bob, doc, db_session):
    d, _ = doc
    root = post(client, as_ana, d.id).json()
    rid = client.post(f"/annotations/{root['id']}/replies", json={"body": "r"}, headers=as_bob).json()["id"]
    n = db_session.get(Annotation, uuid.UUID(root["id"]))
    n.visibility = "private"
    db_session.commit()
    assert client.get(f"/annotations/{rid}", headers=as_bob).json() == NF  # root unreadable
    assert (
        client.post(f"/annotations/{root['id']}/replies", json={"body": "x"}, headers=as_bob).status_code
        == 404
    )
    assert client.get(f"/annotations/{rid}", headers=as_ana).status_code == 200


def test_reply_own_status_rule(client, as_ana, as_bob, ana, bob, doc, db_session):
    d, _ = doc
    root = post(client, as_ana, d.id).json()
    rid = client.post(f"/annotations/{root['id']}/replies", json={"body": "r"}, headers=as_bob).json()["id"]
    n = db_session.get(Annotation, uuid.UUID(rid))
    n.status = "hidden"
    db_session.commit()
    assert rid in ids(client, as_bob, d.id)  # author
    assert rid in ids(client, as_ana, d.id)  # doc owner
    n.status = "removed"
    db_session.commit()
    assert rid not in ids(client, as_bob, d.id)
    assert rid not in ids(client, as_ana, d.id)


def test_hidden_root_only_for_author_owner_admin(
    client, as_ana, as_bob, make_token, ana, bob, doc, db_session
):
    d, _ = doc
    nid = post(client, as_bob, d.id).json()["id"]
    db_session.get(Annotation, uuid.UUID(nid)).status = "hidden"
    db_session.commit()
    carol = bearer(make_token(sub="carol", username="carol"))
    assert ids(client, carol, d.id) == []
    assert ids(client, as_bob, d.id) == [nid] and ids(client, as_ana, d.id) == [nid]


def test_pat_cannot_write(client, as_ana, doc):
    d, _ = doc
    nid = post(client, as_ana, d.id).json()["id"]
    tok = bearer(
        client.post("/me/tokens", json={"name": "c", "scopes": ["read", "write"]}, headers=as_ana).json()[
            "token"
        ]
    )
    for r in (
        client.post(f"/documents/{d.id}/annotations", json={"body": "x"}, headers=tok),
        client.patch(f"/annotations/{nid}", json={"body": "y"}, headers=tok),
        client.delete(f"/annotations/{nid}", headers=tok),
        client.post(f"/annotations/{nid}/replies", json={"body": "x"}, headers=tok),
    ):
        assert r.status_code == 403 and r.json()["detail"] == "agents must propose"
    assert client.get(f"/annotations/{nid}", headers=tok).status_code == 200


def test_patch_and_delete_permissions(client, as_ana, as_bob, ana, bob, doc, db_session):
    d, _ = doc
    nid = post(client, as_ana, d.id).json()["id"]
    assert client.patch(f"/annotations/{nid}", json={"body": "z"}, headers=as_bob).status_code == 403
    assert client.delete(f"/annotations/{nid}", headers=as_bob).status_code == 403
    priv = post(client, as_ana, d.id, visibility="private").json()["id"]
    assert client.patch(f"/annotations/{priv}", json={"body": "z"}, headers=as_bob).status_code == 404
    assert client.delete(f"/annotations/{priv}", headers=as_bob).status_code == 404
    r = client.patch(
        f"/annotations/{nid}",
        json={"body": "new", "visibility": "shared", "shares": [{"kind": "user", "principal": str(bob)}]},
        headers=as_ana,
    )
    assert r.status_code == 200 and r.json()["body"] == "new" and r.json()["visibility"] == "shared"
    assert len(r.json()["shares"]) == 1
    r = client.patch(f"/annotations/{nid}", json={"visibility": "everyone"}, headers=as_ana)
    assert r.json()["shares"] == []
    assert db_session.scalar(select(AnnotationShare)) is None
    assert client.patch(f"/annotations/{nid}", json={"body": ""}, headers=as_ana).status_code == 422
    assert client.delete(f"/annotations/{nid}", headers=as_ana).status_code == 204
    assert client.get(f"/annotations/{nid}", headers=as_ana).status_code == 404


def test_latest_searchable_version_for_non_owner(client, as_ana, as_bob, ana, bob, db_session):
    d, v2 = seed(db_session, ana, visibility="everyone", versions=2)
    v2.stage = "parsed"
    db_session.commit()
    v1 = db_session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == d.id, DocumentVersion.version == 1)
    )
    assert post(client, as_bob, d.id).json()["version_id"] == str(v1.id)
    assert post(client, as_ana, d.id).json()["version_id"] == str(v2.id)


def test_unauth_401(client, doc):
    assert client.get(f"/documents/{doc[0].id}/annotations").status_code == 401
