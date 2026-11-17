# ruff: noqa: F811
"""Final fix wave: draft text for agents, PAT label floor, inline text, handoff block_agents, minors."""

import base64
import uuid

import pytest
from api_seed import FakeES, seed
from conftest import bearer
from mcp_helpers import call
from sqlalchemy import select
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401

from roracle.api.routes.library import get_es
from roracle.models import AuditLog, Document, Job
from roracle.models_labels import Label

pytestmark = pytest.mark.usefixtures("_blob_store_override")
B64 = base64.b64encode(b"%PDF-1 hello").decode()


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def mk(client, admin, name, rank, **gates):
    r = client.post(
        "/admin/labels",
        json={"name": name, "colour": "#aabbcc", "rank": rank, "gates": gates},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def label(db, d, lid):
    d.label_id = lid
    db.commit()


def general(db):
    return str(db.scalar(select(Label.id).where(Label.name == "General")))


# ---- T5-M1: exclude_llm drafts are text; agents never get it ----
def test_pat_gets_no_exclude_llm_draft_text(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoLLM", 3, exclude_llm=True)
    r = client.post("/drafts", json={"title": "D", "content": "SECRET-DRAFT"}, headers=as_ana)
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    label(db_session, db_session.get(Document, uuid.UUID(did)), sec)
    tok = pat(db_session, ana, ["read", "write"])
    for url in (f"/drafts/{did}", f"/drafts/{did}/versions/1"):
        assert client.get(url, headers=tok).status_code == 403, url
        assert "SECRET-DRAFT" in client.get(url, headers=as_ana).text
    assert client.post(f"/drafts/{did}/build", headers=tok).status_code == 403
    # metadata list stays available
    assert [x["id"] for x in client.get("/drafts", headers=tok).json()["items"]] == [did]


def test_pat_reads_plain_draft(client, as_ana, ana, db_session):
    did = client.post("/drafts", json={"title": "D", "content": "ok"}, headers=as_ana).json()["id"]
    assert client.get(f"/drafts/{did}", headers=pat(db_session, ana, ["read"])).status_code == 200


# ---- T6-M3: draft summary carries label ----
def test_draft_list_carries_label(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "Sec", 3)
    did = client.post("/drafts", json={"title": "D", "content": "x"}, headers=as_ana).json()["id"]
    assert client.get("/drafts", headers=as_ana).json()["items"][0]["label"]["name"] == "General"
    label(db_session, db_session.get(Document, uuid.UUID(did)), sec)
    assert client.get("/drafts", headers=as_ana).json()["items"][0]["label"]["name"] == "Sec"
    assert client.get(f"/drafts/{did}", headers=as_ana).json()["label"]["name"] == "Sec"


# ---- T5-M3: a PAT (admin too) cannot choose a label below the default ----
def _up(client, hdr, label=None, data=b"%PDF-1"):
    form = {"label_id": label} if label else {}
    return client.post(
        "/documents", headers=hdr, data=form, files={"file": ("p.pdf", data, "application/pdf")}
    )


def test_pat_principal_never_gets_admin_floor_exemption(client, admin, ana, db_session):
    """PATs are never admin today (pats.py), but the label floor must not depend on that."""
    from fastapi import HTTPException

    from roracle.auth import Principal
    from roracle.labels import service

    r = client.post(
        "/admin/labels",
        json={"name": "Internal", "colour": "#aabbcc", "rank": 2, "is_default": True, "gates": {}},
        headers=admin,
    )
    assert r.status_code == 201
    gen = general(db_session)
    pat_admin = Principal(ana, "ana", "ana", (), True, via="pat")
    with pytest.raises(HTTPException) as e:
        service.initial_label(db_session, gen, p=pat_admin)
    assert e.value.status_code == 422 and "agents" in e.value.detail
    assert str(service.initial_label(db_session, gen, p=Principal(ana, "a", "a", (), True))) == gen


# ---- T5-M4: MCP upload checks scope before resolving the label name ----
def test_mcp_upload_scope_before_label(mc, ana, db_session, fes):
    err, text = call(
        mc, pat(db_session, ana, ["read"]), "upload", filename="a.pdf", content_base64=B64, label="nope"
    )
    assert err and "insufficient scope" in text


# ---- T2: PAT upload with label_id via MCP name; 503 recover; /me/uploads ----
def test_mcp_upload_with_label_name(mc, client, ana, admin, db_session, fes):
    sec = mk(client, admin, "Sec", 3)
    err, out = call(
        mc, pat(db_session, ana, ["write"]), "upload", filename="a.pdf", content_base64=B64, label="sec"
    )
    assert not err, out
    assert str(db_session.get(Document, uuid.UUID(out["document_id"])).label_id) == sec


class DownES(FakeES):
    def update_by_query(self, **kw):
        self.calls.append(kw)
        raise RuntimeError("es down")


def test_set_label_503_rolls_back_and_recovers(client, as_ana, ana, admin, app, db_session):
    sec = mk(client, admin, "Sec", 3)
    d, _ = seed(db_session, ana)
    db_session.commit()
    es = DownES()
    app.dependency_overrides[get_es] = lambda: es
    r = client.put(f"/documents/{d.id}/label", json={"label_id": sec}, headers=as_ana)
    assert r.status_code == 503
    db_session.expire_all()
    assert str(db_session.get(Document, d.id).label_id) == general(db_session)  # rolled back
    assert len(es.calls) >= 2  # initial sync + _recover re-sync
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert any(j.payload.get("doc_id") == str(d.id) for j in jobs)


# ---- T1 minors ----
def test_rank_out_of_range_is_422(client, admin):
    for rank in (-1, 10**12):
        r = client.post(
            "/admin/labels",
            json={"name": "X", "colour": "#aabbcc", "rank": rank, "gates": {}},
            headers=admin,
        )
        assert r.status_code == 422, rank
    lid = mk(client, admin, "Y", 1)
    assert client.patch(f"/admin/labels/{lid}", json={"rank": 10**12}, headers=admin).status_code == 422


def test_reorder_audited_and_delete_unknown_404(client, admin, db_session):
    a = mk(client, admin, "A", 1)
    gen = general(db_session)
    assert client.put("/admin/labels/order", json={"ids": [gen, a]}, headers=admin).status_code == 200
    assert "label.reorder" in {x.action for x in db_session.scalars(select(AuditLog))}
    assert client.delete(f"/admin/labels/{uuid.uuid4()}", headers=admin).status_code == 404


# ---- T6-I1: md/txt inline for non-owners under block_export ----
@pytest.fixture
def md_locked(client, ana, admin, db_session, store):
    sec = mk(client, admin, "NoExport", 3, block_export=True)
    d, v = seed(db_session, ana, title="Notes", filename="n.md", content_type="text/markdown")
    store.put_bytes(store.key(v.blob_sha256), b"# hi <script>x</script>", "text/markdown")
    label(db_session, d, sec)
    d.visibility = "everyone"
    db_session.commit()
    return d


def test_md_inline_for_non_owner_sandboxed_and_audited(client, as_bob, md_locked, db_session):
    url = f"/documents/{md_locked.id}/versions/1/file"
    assert client.get(url, headers=as_bob).status_code == 403  # download stays blocked
    r = client.get(url, params={"inline": "true"}, headers=as_bob)
    assert r.status_code == 200 and r.content.startswith(b"# hi")
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"].startswith("inline")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "label.view")).all()
    assert len(rows) == 1 and rows[0].target == str(md_locked.id)


def test_md_inline_never_for_pat(client, bob, md_locked, db_session):
    tok = pat(db_session, bob, ["read"])
    r = client.get(f"/documents/{md_locked.id}/versions/1/file", params={"inline": "true"}, headers=tok)
    assert r.status_code == 403  # a PAT is always a download
