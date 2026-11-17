import pytest
from conftest import bearer
from sqlalchemy import select

from roracle.models import AuditLog
from roracle.models_labels import Label

NOT_MONO = "gates must not decrease with rank"


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


def mk(client, adm, name, rank, **kw):
    gates = kw.pop("gates", {})
    return client.post(
        "/admin/labels",
        json={"name": name, "colour": "#aabbcc", "rank": rank, "gates": gates, **kw},
        headers=adm,
    )


def items(client, adm):
    return client.get("/labels", headers=adm).json()["items"]


def test_get_labels_any_authenticated(client, adm, as_ana):
    assert client.get("/labels").status_code == 401
    out = client.get("/labels", headers=as_ana).json()["items"]
    assert [x["name"] for x in out] == ["General"]
    g = out[0]
    assert set(g) == {"id", "name", "colour", "rank", "gates", "description", "is_default"}
    assert g["gates"] == {
        k: False for k in ("exclude_llm", "block_agents", "block_export", "reason_on_lower")
    }
    pat = client.post("/me/tokens", json={"name": "x", "scopes": ["read"]}, headers=as_ana).json()["token"]
    assert client.get("/labels", headers=bearer(pat)).status_code == 200


def test_create_sorted_and_audited(client, adm, db_session):
    r = mk(client, adm, "Secret", 5, gates={"exclude_llm": True})
    assert r.status_code == 201 and r.json()["gates"]["exclude_llm"] is True
    mk(client, adm, "Internal", 2)
    assert [x["name"] for x in items(client, adm)] == ["General", "Internal", "Secret"]
    acts = [a.action for a in db_session.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert acts.count("label.create") == 2


@pytest.mark.parametrize(
    "kw",
    [
        {"colour": "red"},
        {"colour": "#abc"},
        {"name": ""},
        {"name": "x" * 41},
        {"name": "GENERAL"},
        {"rank": 0},
        {"description": "d" * 501},
    ],
)
def test_create_validation(client, adm, kw):
    body = {"name": "New", "colour": "#aabbcc", "rank": 3, **kw}
    assert client.post("/admin/labels", json=body, headers=adm).status_code == 422
    assert len(items(client, adm)) == 1


def test_max_20(client, adm):
    for i in range(1, 20):
        assert mk(client, adm, f"L{i}", i).status_code == 201
    r = mk(client, adm, "L20", 20)
    assert r.status_code == 422


def test_non_monotonic_create_patch_reorder(client, adm):
    top = mk(client, adm, "Top", 5, gates={"block_export": True}).json()
    r = mk(client, adm, "Mid", 8)  # above Top without the gate
    assert (r.status_code, r.json()["detail"]) == (422, NOT_MONO)
    low = mk(client, adm, "Low", 2).json()
    r = client.patch(f"/admin/labels/{low['id']}", json={"gates": {"block_export": True}}, headers=adm)
    assert r.status_code == 200  # lower label with gate, higher also has it
    r = client.patch(f"/admin/labels/{top['id']}", json={"gates": {"block_export": False}}, headers=adm)
    assert (r.status_code, r.json()["detail"]) == (422, NOT_MONO)
    r = client.patch(f"/admin/labels/{top['id']}", json={"rank": 1}, headers=adm)  # now under Low
    assert r.status_code == 200  # both have the gate: still monotone
    ids = [x["id"] for x in items(client, adm)]
    gen = next(x["id"] for x in items(client, adm) if x["name"] == "General")
    r = client.put("/admin/labels/order", json={"ids": [low["id"], gen, top["id"]]}, headers=adm)
    assert (r.status_code, r.json()["detail"]) == (422, NOT_MONO)
    assert [x["id"] for x in items(client, adm)] == ids


def test_reorder(client, adm):
    a = mk(client, adm, "A", 1).json()["id"]
    b = mk(client, adm, "B", 2).json()["id"]
    gen = items(client, adm)[0]["id"]
    r = client.put("/admin/labels/order", json={"ids": [b, a, gen]}, headers=adm)
    assert r.status_code == 200
    assert [(x["name"], x["rank"]) for x in items(client, adm)] == [("B", 0), ("A", 1), ("General", 2)]
    assert client.put("/admin/labels/order", json={"ids": [a, b]}, headers=adm).status_code == 422
    assert client.put("/admin/labels/order", json={"ids": [a, a, b, gen]}, headers=adm).status_code == 422


def test_default_swap_and_guard(client, adm):
    new = mk(client, adm, "Open", 1, is_default=True).json()
    out = items(client, adm)
    assert [x["is_default"] for x in out] == [False, True]
    gen = out[0]["id"]
    r = client.patch(f"/admin/labels/{new['id']}", json={"is_default": False}, headers=adm)
    assert r.status_code == 422  # would leave no default
    r = client.patch(f"/admin/labels/{gen}", json={"is_default": True, "name": "Base"}, headers=adm)
    assert r.status_code == 200
    assert {x["name"]: x["is_default"] for x in items(client, adm)} == {"Base": True, "Open": False}


def test_patch_audit_and_unknown(client, adm, db_session):
    gen = items(client, adm)[0]["id"]
    r = client.patch(f"/admin/labels/{gen}", json={"name": "Public", "colour": "#000000"}, headers=adm)
    assert r.json()["name"] == "Public"
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "label.update")).one()
    assert row.detail["changes"]["name"] == {"old": "General", "new": "Public"}
    bad = "00000000-0000-0000-0000-000000000000"
    assert client.patch(f"/admin/labels/{bad}", json={"name": "x"}, headers=adm).status_code == 404
    assert client.patch(f"/admin/labels/{gen}", json={"colour": "nope"}, headers=adm).status_code == 422
    assert client.patch(f"/admin/labels/{gen}", json={"bogus": 1}, headers=adm).status_code == 422


def test_delete_rules(client, adm, db_session, user):
    from api_seed import seed

    gen = items(client, adm)[0]["id"]
    assert client.delete(f"/admin/labels/{gen}", headers=adm).status_code == 409  # default
    used = mk(client, adm, "Used", 1).json()["id"]
    free = mk(client, adm, "Free", 2).json()["id"]
    doc, _ = seed(db_session, user.id)
    doc.label_id = used
    db_session.flush()
    r = client.delete(f"/admin/labels/{used}", headers=adm)
    assert (r.status_code, r.json()["detail"]) == (409, "label in use")
    assert client.delete(f"/admin/labels/{free}", headers=adm).status_code == 204
    assert db_session.get(Label, free) is None
    acts = {a.action for a in db_session.scalars(select(AuditLog))}
    assert {"label.delete"} <= acts


def test_delete_blocked_by_note_and_connector(client, adm, db_session, user):
    from roracle.models import Connector

    a = mk(client, adm, "A", 1).json()["id"]
    db_session.add(Connector(name="c", kind="fs", owner_id=user.id, visibility="everyone", label_id=a))
    db_session.flush()
    assert client.delete(f"/admin/labels/{a}", headers=adm).status_code == 409


def test_non_admin_pat_anonymous(client, adm, as_ana):
    gen = items(client, adm)[0]["id"]
    calls = [
        ("post", "/admin/labels", {"name": "x", "colour": "#aabbcc", "rank": 3}),
        ("patch", f"/admin/labels/{gen}", {"name": "y"}),
        ("delete", f"/admin/labels/{gen}", None),
        ("put", "/admin/labels/order", {"ids": [gen]}),
    ]
    pat = client.post("/me/tokens", json={"name": "t", "scopes": ["read", "write"]}, headers=adm).json()[
        "token"
    ]
    for method, url, body in calls:
        kw = {"json": body} if body is not None else {}
        assert getattr(client, method)(url, **kw).status_code == 401
        assert getattr(client, method)(url, headers=as_ana, **kw).status_code == 403
        assert getattr(client, method)(url, headers=bearer(pat), **kw).status_code == 403
