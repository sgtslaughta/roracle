# ruff: noqa: F811
"""Gates (b) agents/handoffs and (c) export. Postgres is the authority: ES is faked and ignores labels."""

import ast
import json
import pathlib

import pytest
from api_seed import seed
from conftest import bearer
from mcp_helpers import call
from sqlalchemy import select
from test_documents_read import put
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401
from test_search_api import hit

from roracle.models import AuditLog, Document
from roracle.models_labels import Label

pytestmark = pytest.mark.usefixtures("_blob_store_override")


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


# ---------------- (b) agents ----------------
def test_pat_never_sees_block_agents_doc_jwt_does(client, as_ana, ana, admin, db_session, fes):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, _ = seed(db_session, ana, title="Hidden")
    ok, _ = seed(db_session, ana, title="Plain")
    label(db_session, d, sec)
    tok = pat(db_session, ana, ["read"])
    assert client.get(f"/documents/{d.id}", headers=as_ana).status_code == 200
    assert client.get(f"/documents/{d.id}", headers=tok).status_code == 404
    assert client.get(f"/documents/{ok.id}", headers=tok).status_code == 200
    jwt = client.get("/search", headers=as_ana).json()
    agent = client.get("/search", headers=tok).json()
    assert {r["title"] for r in jwt["results"]} == {"Hidden", "Plain"}
    assert [r["title"] for r in agent["results"]] == ["Plain"] and agent["total"] == 1
    assert [f["value"] for f in agent["facets"]["label"]] == [
        str(db_session.scalar(select(Label.id).where(Label.name == "General")))
    ]
    assert sec in [f["value"] for f in jwt["facets"]["label"]]
    # admin PAT is blocked too; admin JWT is not
    for h in (tok,):
        for sub in ("versions/1/file", "versions/1/pdf"):
            assert client.get(f"/documents/{d.id}/{sub}", headers=h).status_code == 404


def test_removing_gate_needs_no_reindex(client, as_ana, ana, admin, db_session, fes):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, _ = seed(db_session, ana)
    label(db_session, d, sec)
    tok = pat(db_session, ana, ["read"])
    assert client.get(f"/documents/{d.id}", headers=tok).status_code == 404
    r = client.patch(f"/admin/labels/{sec}", json={"gates": {"block_agents": False}}, headers=admin)
    assert r.status_code == 200
    assert client.get(f"/documents/{d.id}", headers=tok).status_code == 200


def test_es_lag_pat_search_and_ask_use_current_pg_label(mc, client, as_ana, ana, admin, db_session, fes, llm):
    """ES chunk still carries the OLD label (fake ES ignores labels); PG already raised it."""
    sec = mk(client, admin, "NoAgent", 3, block_agents=True, exclude_llm=False)
    d, v = seed(db_session, ana, title="Lagging")
    fes.hits = [hit(d, v, "lagging text")]
    h = pat(db_session, ana, ["read", "write"])
    assert call(mc, h, "search", query="alpha")[1]["results"][0]["title"] == "Lagging"
    label(db_session, d, sec)
    assert call(mc, h, "search", query="alpha")[1]["results"] == []
    err, out = call(mc, h, "ask", question="alpha?")
    assert not err and out["citations"] == [] and not llm.sent
    err, out = call(mc, as_ana, "search", query="alpha")  # human JWT still sees it
    assert out["results"][0]["title"] == "Lagging"


def test_mcp_passages_and_handoff_gate(mc, client, as_ana, ana, admin, db_session, fes):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, v = seed(db_session, ana, title="Hidden")
    ok, _ = seed(db_session, ana, title="Plain")
    fes.hits = [hit(d, v, "hidden text")]
    label(db_session, d, sec)
    h = pat(db_session, ana, ["read", "write"])
    err, out = call(mc, h, "get_passages", chunk_ids=[f"{v.id}:0"])
    assert not err and out == {"result": []}
    # handoff: dropped at create for the agent AND for the human creator; withheld for every loader
    err, made = call(mc, h, "create_handoff", title="t", brief="b", doc_ids=[str(d.id), str(ok.id)])
    assert made["dropped"] == 1
    r = client.post(
        "/handoffs",
        json={"kind": "agent", "doc_ids": [str(d.id), str(ok.id)], "audience": "authenticated"},
        headers=as_ana,
    )
    assert r.status_code == 201
    m = r.json()
    assert m["doc_ids"] == [str(ok.id)] and m["withheld"] == 0  # never stored => nothing to count
    # a handoff created BEFORE the label was raised: every loader (human, creator) sees it withheld
    label(db_session, d, db_session.scalar(select(Label.id).where(Label.name == "General")))
    r = client.post(
        "/handoffs",
        json={"kind": "agent", "doc_ids": [str(d.id), str(ok.id)], "audience": "authenticated"},
        headers=as_ana,
    )
    code = r.json()["code"]
    assert str(d.id) in r.json()["doc_ids"]
    label(db_session, d, sec)
    seen = client.get(f"/handoffs/{code}", headers=as_ana).json()
    assert seen["doc_ids"] == [str(ok.id)] and seen["withheld"] == 1
    assert "Hidden" not in json.dumps(seen)


# ---------------- (c) export ----------------
@pytest.fixture
def gated(client, as_ana, ana, admin, db_session, store):
    sec = mk(client, admin, "NoExport", 3, block_export=True)
    d, v = seed(db_session, ana, title="Locked", authors=("Zed Q",))
    put(store, v, b"%PDF-bytes")
    label(db_session, d, sec)
    return d, v


def test_file_download_blocked_non_owner_owner_admin_not(client, as_ana, as_bob, admin, gated, db_session):
    d, _ = gated
    url = f"/documents/{d.id}/versions/1/file"
    r = client.get(url, headers=as_bob)
    assert r.status_code == 403 and r.json() == {"detail": "export blocked by label", "label": "NoExport"}
    audits = db_session.scalars(select(AuditLog).where(AuditLog.action == "label.export_blocked")).all()
    assert audits[-1].target == str(d.id) and audits[-1].detail == {"route": "document.file"}
    assert "NoExport" not in json.dumps(audits[-1].detail)
    assert client.get(url, headers=as_ana).status_code == 200
    assert client.get(url, headers=admin).status_code == 200
    assert client.get(url, params={"inline": "false"}, headers=as_bob).status_code == 403


def test_inline_viewing_stays_allowed_for_non_owner(client, as_bob, gated, db_session):
    """User decision: reader viewing (inline file, pdf, docling) is not a download."""
    d, _ = gated
    r = client.get(f"/documents/{d.id}/versions/1/file", params={"inline": "true"}, headers=as_bob)
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline")
    assert client.get(f"/documents/{d.id}/versions/1/pdf", headers=as_bob).status_code == 200
    assert client.get(f"/documents/{d.id}/versions/1/docling", headers=as_bob).status_code != 403
    assert not db_session.scalars(select(AuditLog).where(AuditLog.action == "label.export_blocked")).all()


def test_inline_views_audited_once_per_day_non_owner_only(client, as_ana, as_bob, admin, gated, db_session):
    d, _ = gated
    f, pdf = f"/documents/{d.id}/versions/1/file", f"/documents/{d.id}/versions/1/pdf"
    for _ in range(2):
        assert client.get(f, params={"inline": "true"}, headers=as_bob).status_code == 200
        assert client.get(pdf, headers=as_bob).status_code == 200
    client.get(f"/documents/{d.id}/versions/1/docling", headers=as_bob)
    assert client.get(pdf, headers=as_ana).status_code == 200  # owner
    assert client.get(pdf, headers=admin).status_code == 200
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "label.view")).all()
    assert len(rows) == 1 and rows[0].target == str(d.id) and rows[0].detail == {"route": "document.file"}


def test_invisible_is_404_not_403(client, as_bob, ana, admin, db_session):
    sec = mk(client, admin, "NoExport", 3, block_export=True)
    d, _ = seed(db_session, ana, visibility="private")
    label(db_session, d, sec)
    assert client.get(f"/documents/{d.id}/versions/1/file", headers=as_bob).status_code == 404


def test_bibtex_omits_gated_for_non_owner(client, as_ana, as_bob, admin, gated):
    d, _ = gated
    t = client.get("/export/bibtex", params=[("doc_id", str(d.id))], headers=as_bob)
    assert t.status_code == 200 and t.text == ""
    assert "Locked" in client.get("/export/bibtex", params=[("doc_id", str(d.id))], headers=as_ana).text
    assert "Locked" in client.get("/export/bibtex", params=[("doc_id", str(d.id))], headers=admin).text


def test_mcp_bibtex_omits_gated(mc, as_bob, gated, fes):
    d, _ = gated
    err, out = call(mc, as_bob, "export_bibtex", doc_ids=[str(d.id)])
    assert not err and out["count"] == 0


def test_chat_export_withholds_gated_reference(client, as_ana, as_bob, ana, bob, gated, db_session):
    from roracle.models import Chat, ChatMessage

    d, v = gated
    c = Chat(owner_id=bob, title="t", scope="all")
    db_session.add(c)
    db_session.flush()
    cit = {"n": 1, "doc_id": str(d.id), "version_id": str(v.id), "title": "Locked", "authors": ["Zed Q"],
           "year": 2020, "page": 1, "quote": "secret quote", "available": True}  # fmt: skip
    db_session.add(
        ChatMessage(
            chat_id=c.id, role="assistant", content="A [1]", citations=[cit], retrieved=[], status="complete"
        )
    )
    db_session.commit()
    md = client.get(f"/chats/{c.id}/export.md", headers=as_bob).text
    assert "[1] [withheld: NoExport]" in md and "Locked" not in md and "Zed" not in md
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "label.export_blocked")
    ).one().detail == {"route": "chat.export"}


def test_evidence_file_uses_effective_label(client, as_ana, as_bob, ana, admin, db_session, s3):
    from test_evidence import post

    sec = mk(client, admin, "NoExport", 3, block_export=True)
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    nid = client.post(
        f"/documents/{d.id}/annotations", json={"body": "hi", "kind": "finding"}, headers=as_ana
    ).json()["id"]
    ev = post(client, as_ana, nid).json()["id"]
    label(db_session, d, sec)
    r = client.get(f"/evidence/{ev}/file", headers=as_bob)
    assert r.status_code == 403 and r.json()["label"] == "NoExport"
    assert client.get(f"/evidence/{ev}/file", headers=as_ana).status_code != 403


def test_draft_build_gate_helper_owner_exempt(db_session, ana, bob, admin, client):
    from roracle.auth import Principal
    from roracle.labels.policy import ExportBlocked, require_exportable

    sec = mk(client, admin, "NoExport", 3, block_export=True)
    d, _ = seed(db_session, ana)
    label(db_session, d, sec)
    P = lambda uid, adm=False: Principal(uid, "s", "u", (), adm)
    require_exportable(db_session, P(ana), d, "draft.build")
    require_exportable(db_session, P(bob, True), d, "draft.build")
    with pytest.raises(ExportBlocked):
        require_exportable(db_session, P(bob), d, "draft.build")


# ---------------- structural ----------------
SRC = pathlib.Path(__file__).parent.parent / "src" / "roracle"


def test_every_acl_filter_call_passes_policy():
    calls = 0
    for f in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "acl_filter":
                calls += 1
                assert len(n.args) + len(n.keywords) == 2, f"{f}:{n.lineno}"
    assert calls >= 3


@pytest.mark.parametrize(
    "mod", ["api/routes/documents_read.py", "api/routes/evidence.py", "api/routes/drafts_build.py"]
)
def test_byte_serving_routes_call_require_exportable(mod):
    assert "require_exportable(" in (SRC / mod).read_text()


def test_doc_label_is_not_nullable_for_gate_sql():
    assert Document.__table__.c.label_id.nullable is False


# ---------------- SQL / ES parity incl. the agent gate ----------------
@pytest.fixture
def pes(settings):
    from elasticsearch import Elasticsearch

    c = Elasticsearch(settings.elastic_url)
    yield c
    c.close()


@pytest.fixture
def pmodel(pes):
    import uuid

    from roracle.search_index import index_name

    m = f"Test Model:{uuid.uuid4().hex[:8]}"
    yield m
    pes.indices.delete(index=index_name(m), ignore_unavailable=True)


@pytest.mark.integration
def test_sql_es_parity_agent_gate(pes, pmodel, db_session, blob):
    import dataclasses
    import uuid

    from roracle.access import visible_documents
    from roracle.auth import Principal
    from roracle.ingest.chunking import Chunk
    from roracle.labels.policy import Policy
    from roracle.models import DocumentShare, DocumentVersion, User
    from roracle.search_acl import acl_filter
    from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name

    ana, eve, bob = (
        User(id=uuid.uuid4(), sub=n, username=n, email=f"{n}@x.org") for n in ["ana", "eve", "bob"]
    )
    db_session.add_all([ana, eve, bob])
    blocked = Label(name="NoAgent", colour="#aabbcc", rank=7, block_agents=True)
    plain = Label(name="Mid", colour="#aabbcc", rank=5)
    db_session.add_all([blocked, plain])
    db_session.flush()
    specs = {  # name -> (owner, visibility, label)
        "everyone_plain": (eve, "everyone", None),
        "everyone_blocked": (eve, "everyone", blocked),
        "everyone_mid": (eve, "everyone", plain),
        "owner_blocked": (ana, "private", blocked),
        "owner_plain": (ana, "private", plain),
        "private_other_blocked": (eve, "private", blocked),
        "shared_blocked": (eve, "shared", blocked),
        "shared_plain": (eve, "shared", None),
    }
    docs = {}
    for name, (owner, vis, lab) in specs.items():
        d = Document(owner_id=owner.id, title=name, visibility=vis, org_unit="/")
        if lab:
            d.label_id = lab.id
        db_session.add(d)
        db_session.flush()
        if vis == "shared":
            db_session.add(DocumentShare(document_id=d.id, principal_kind="user", principal=str(bob.id)))
        docs[name] = d
    db_session.flush()
    ensure_index(pes, pmodel, 4)
    for name, d in docs.items():
        v = DocumentVersion(
            id=uuid.uuid4(), document_id=d.id, version=1, blob_sha256=blob.sha256, filename="a",
            content_type="x", size=1, is_latest=True,
        )  # fmt: skip
        db_session.add(v)
        db_session.flush()
        index_chunks(
            pes, v, d, [Chunk(0, "t", "c", ["H"], [1], [{"page": 1}])], [[0.1] * 4],
            acl_fields(db_session, d), pmodel, title=name,
        )  # fmt: skip
    pes.indices.refresh(index=index_name(pmodel))
    policy = Policy.load(db_session)
    base = {u.username: Principal(u.id, "s", u.username, (), False) for u in (ana, eve, bob)}
    base["admin"] = Principal(uuid.uuid4(), "s", "root", (), True)
    principals = {
        f"{k}-{via}": dataclasses.replace(p, via=via) for k, p in base.items() for via in ("jwt", "pat")
    }
    for who, p in principals.items():
        sql = {str(i) for i in db_session.scalars(select(Document.id).where(visible_documents(p)))}
        hits = pes.search(
            index=index_name(pmodel),
            query={"bool": {"filter": [acl_filter(p, policy), {"term": {"is_latest": True}}]}},
            size=100,
        )["hits"]["hits"]
        assert {h["_source"]["doc_id"] for h in hits} == sql, who
        names = {d.id: n for n, d in docs.items()}
        got = {names[uuid.UUID(i)] for i in sql}
        if p.via == "pat":
            assert not {n for n in got if "blocked" in n}, who  # agents never see blocked, admin included
        elif who == "ana-jwt":
            assert "owner_blocked" in got and "everyone_blocked" in got  # humans keep access
        elif who == "admin-jwt":
            assert got == set(docs)
    assert acl_filter(base["admin"], policy) == {"match_all": {}}  # JWT admin: no extra clause


def test_pat_cannot_read_or_list_blocked_draft_nor_uploads(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    i = client.post("/drafts", headers=as_ana, json={"title": "T", "content": "x"}).json()["id"]
    doc = db_session.get(Document, __import__("uuid").UUID(i))
    label(db_session, doc, sec)
    tok = pat(db_session, ana, ["read"])
    assert client.get(f"/drafts/{i}", headers=as_ana).status_code == 200
    assert client.get(f"/drafts/{i}", headers=tok).status_code == 404
    assert i in [x["id"] for x in client.get("/drafts", headers=as_ana).json()["items"]]
    assert i not in [x["id"] for x in client.get("/drafts", headers=tok).json()["items"]]
    up, _ = seed(db_session, ana, title="Up")
    label(db_session, up, sec)
    mine = lambda h: {x["document_id"] for x in client.get("/me/uploads", headers=h).json()["items"]}
    assert str(up.id) in mine(as_ana) and str(up.id) not in mine(tok)
