import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from mcp_helpers import call, rpc
from sqlalchemy import select
from test_search_api import FakeLLM, hit

from roracle import pats
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.mcp.tools_write import NO_PASSAGES
from roracle.models import AuditLog, Collection, CollectionItem, Document, Handoff, Job

B64 = base64.b64encode(b"%PDF-1 hello").decode()


class AskLLM(FakeLLM):
    def __init__(self, text="Answer [1]"):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda model, messages: self._reply(model, messages))
        )
        self.text, self.sent = text, []

    def _reply(self, model, messages):
        self.sent.append((model, messages))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))])


@pytest.fixture
def llm(app):
    x = AskLLM()
    app.dependency_overrides[get_llm] = lambda: x
    return x


@pytest.fixture
def fes(app, llm):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    return f


@pytest.fixture
def mc(app):
    with TestClient(app) as c:
        yield c


def pat(db_session, uid, scopes):
    _, token = pats.create(db_session, uid, "t", scopes, 30)
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_read_pat_cannot_write(mc, ana, db_session, fes):
    h = pat(db_session, ana, ["read"])
    d, _ = seed(db_session, ana)
    code = call(mc, {**pat(db_session, ana, ["write"])}, "create_handoff", title="t", brief="b")[1]["code"]
    for tool, args in [
        ("upload", {"filename": "a.pdf", "content_base64": B64}),
        ("create_handoff", {"title": "t", "brief": "b", "doc_ids": [str(d.id)]}),
        ("append_to_handoff", {"code": code, "note": "x"}),
    ]:
        err, text = call(mc, h, tool, **args)
        assert err and "insufficient scope" in text, tool
    assert db_session.scalars(select(Document)).all() == [d]
    assert db_session.scalars(select(Job)).all() == []
    assert db_session.scalars(select(Handoff)).one().notes == []
    # read tools still work with the read PAT
    assert not call(mc, h, "load_handoff", code=code)[0]
    assert call(mc, h, "upload", filename="a.pdf", content_base64=B64)[0]
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.upload")).first()
    assert row.detail == {"error": True}


@pytest.mark.usefixtures("_blob_store_override")
def test_write_pat_upload_private_and_duplicate(mc, ana, db_session, fes):
    h = pat(db_session, ana, ["write"])
    err, out = call(mc, h, "upload", filename="a.pdf", content_base64=B64, title="T")
    assert not err
    doc = db_session.scalar(select(Document))
    assert str(doc.id) == out["document_id"] and doc.visibility == "private" and doc.title == "T"
    assert db_session.scalar(select(Job)).kind == "ingest"
    err, out2 = call(mc, h, "upload", filename="a.pdf", content_base64=B64)
    assert not err and out2 == {"duplicate_of": out["document_id"]}
    assert call(mc, h, "upload", filename="a.pdf", content_base64="!!notb64")[1].endswith("invalid base64")


def test_upload_too_large(app, settings, ana, db_session, fes):
    from roracle.api.app import create_app

    small = create_app(settings.model_copy(update={"mcp_max_upload_bytes": 8}))
    small.dependency_overrides = app.dependency_overrides
    h = pat(db_session, ana, ["write"])
    with TestClient(small) as c:
        err, text = call(c, h, "upload", filename="a.pdf", content_base64=B64)
    assert err and "file too large" in text


def test_ask_cites_visible_never_invisible(mc, as_ana, ana, bob, db_session, fes, llm):
    d1, v1 = seed(db_session, ana, title="Mine")
    d2, v2 = seed(db_session, bob, title="Secret", visibility="private")
    fes.hits = [hit(d2, v2, "secret text"), hit(d1, v1, "mine text")]
    llm.text = "A [1], also [2] and [9]."
    err, out = call(mc, as_ana, "ask", question="alpha?")
    assert not err and out["answer"] == llm.text
    assert [c["doc_id"] for c in out["citations"]] == [str(d1.id)] and out["citations"][0]["n"] == 1
    sent = json.dumps(llm.sent[0][1])
    assert "secret text" not in sent and "mine text" in sent
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.ask")).one().target == "alpha?"


def test_ask_no_passages_skips_llm(mc, as_ana, fes, llm):
    err, out = call(mc, as_ana, "ask", question="nothing")
    assert not err and out["citations"] == [] and not llm.sent
    assert call(mc, as_ana, "ask", question="")[0]


def test_ask_inference_down(mc, as_ana, ana, db_session, fes, llm):
    import httpx

    d, v = seed(db_session, ana)
    fes.hits = [hit(d, v)]
    llm.chat.completions.create = lambda **kw: (_ for _ in ()).throw(httpx.ConnectError("x"))
    err, text = call(mc, as_ana, "ask", question="q")
    assert err and "inference unavailable" in text


def test_handoff_roundtrip_mcp_rest_and_uri(mc, client, as_ana, as_bob, ana, bob, db_session, fes):
    d1, _ = seed(db_session, ana, title="Mine")
    d2, _ = seed(db_session, bob, title="Secret", visibility="private")
    h = pat(db_session, ana, ["write"])
    err, out = call(
        mc, h, "create_handoff", title="Plan", brief="do it", doc_ids=[str(d1.id), str(d2.id)], goal="g"
    )
    assert not err and out["dropped"] == 1 and out["url"].startswith("/h/")
    code = out["code"]
    assert out["uri"] == f"roracle://handoff/{code}" and out["display"] == f"{code[:4]}-{code[4:]}"
    row = db_session.get(Handoff, code)
    assert row.kind == "agent" and row.created_via == "mcp" and row.doc_ids == [str(d1.id)]
    assert [x["code"] for x in client.get("/handoffs", headers=as_ana).json()] == [code]
    assert client.get(f"/handoffs/{code}", headers=as_ana).json()["brief"] == "do it"
    err, m = call(mc, h, "load_handoff", code=out["uri"])
    assert not err and m["code"] == code and m["parts"] == {"transcript": 0, "notes": 0}
    assert call(mc, h, "load_handoff", code="/h/" + out["display"])[1]["code"] == code
    err, note = call(mc, h, "append_to_handoff", code=code, note="progress")
    assert not err and note["via"] == "mcp" and note["text"] == "progress"
    err, part = call(mc, h, "get_handoff_part", code=code, part="notes")
    assert not err and [i["text"] for i in part["items"]] == ["progress"]
    assert call(mc, as_bob, "load_handoff", code=code)[0]  # creator-only audience: 404 for bob
    assert call(mc, h, "get_handoff_part", code=code, part="bogus")[0]
    assert call(mc, h, "create_handoff", title="t", brief="b", expires_in_days=91)[0]
    actions = set(db_session.scalars(select(AuditLog.action)))
    assert {"mcp.create_handoff", "mcp.load_handoff", "handoff.load", "mcp.append_to_handoff"} <= actions


def test_withheld_counts_doc_hidden_from_loader(mc, as_ana, as_bob, ana, bob, db_session, fes):
    d, _ = seed(db_session, bob, title="Secret", visibility="everyone")
    out = call(
        mc, as_bob, "create_handoff", title="t", brief="b", doc_ids=[str(d.id)], audience="authenticated"
    )
    assert out[1]["dropped"] == 0
    code = out[1]["code"]
    assert call(mc, as_ana, "load_handoff", code=code)[1]["doc_ids"] == [str(d.id)]
    d.visibility = "private"
    db_session.flush()
    m = call(mc, as_ana, "load_handoff", code=code)[1]
    assert m["doc_ids"] == [] and m["withheld"] == 1
    assert "Secret" not in json.dumps(m)


def test_ask_foreign_collection_canned_no_llm(mc, as_ana, ana, bob, db_session, fes, llm):
    d, v = seed(db_session, bob)
    col = Collection(owner_id=bob, name="Bobs")
    db_session.add(col)
    db_session.flush()
    db_session.add(CollectionItem(collection_id=col.id, document_id=d.id))
    db_session.flush()
    fes.hits = [hit(d, v)]
    err, out = call(mc, as_ana, "ask", question="q", collection=str(col.id))
    assert not err and out == {"answer": NO_PASSAGES, "citations": []} and not llm.sent


def test_ask_prompt_omits_invisible_title(mc, as_ana, ana, bob, db_session, fes, llm):
    d1, v1 = seed(db_session, ana, title="Visible One")
    d2, v2 = seed(db_session, bob, title="Hidden Title Zed", visibility="private")
    fes.hits = [hit(d2, v2), hit(d1, v1)]
    call(mc, as_ana, "ask", question="q")
    sent = json.dumps(llm.sent[0][1])
    assert "Visible One" in sent and "Hidden Title Zed" not in sent


def test_read_pat_append_unknown_code_is_scope_error(mc, ana, db_session, fes):
    err, text = call(mc, pat(db_session, ana, ["read"]), "append_to_handoff", code="ABCDEFGH", note="x")
    assert err and "insufficient scope" in text


def test_expired_and_revoked_handoff_over_mcp(mc, as_ana, ana, db_session, fes):
    c1 = call(mc, as_ana, "create_handoff", title="a", brief="b")[1]["code"]
    c2 = call(mc, as_ana, "create_handoff", title="c", brief="d")[1]["code"]
    db_session.get(Handoff, c1).expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.get(Handoff, c2).revoked_at = datetime.now(UTC)
    db_session.flush()
    for c, word in ((c1, "expired"), (c2, "revoked")):
        for tool in ("load_handoff", "get_handoff_part"):
            err, text = call(mc, as_ana, tool, code=c)
            assert err and f"handoff {word}" in text, (tool, word)


def test_public_base_url_makes_handoff_url_absolute(app, settings, ana, db_session, fes):
    from roracle.api.app import create_app

    pub = create_app(settings.model_copy(update={"public_base_url": "https://r.example.org/"}))
    pub.dependency_overrides = app.dependency_overrides
    h = pat(db_session, ana, ["write"])
    with TestClient(pub) as c:
        out = call(c, h, "create_handoff", title="t", brief="b")[1]
    assert out["url"] == f"https://r.example.org/h/{out['display']}"


def test_handoff_resource_template(mc, as_ana, as_bob, ana, db_session, fes):
    code = call(mc, as_ana, "create_handoff", title="R", brief="b")[1]["code"]
    r = rpc(mc, as_ana, "resources/templates/list").json()["result"]
    assert "roracle://handoff/{code}" in [t["uriTemplate"] for t in r["resourceTemplates"]]
    r = rpc(mc, as_ana, "resources/read", {"uri": f"roracle://handoff/{code}"}).json()["result"]
    assert json.loads(r["contents"][0]["text"])["title"] == "R"
    bad = rpc(mc, as_bob, "resources/read", {"uri": f"roracle://handoff/{code}"}).json()
    assert "error" in bad and code not in json.dumps(bad).replace(f"roracle://handoff/{code}", "")


def test_internal_error_is_audited(mc, as_ana, db_session, fes):
    fes.search = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    assert call(mc, as_ana, "ask", question="q")[0]
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.ask")).one()
    assert row.detail == {"error": "internal"}


def test_base64_with_newlines_accepted(mc, ana, db_session, fes, _blob_store_override):
    wrapped = "\n".join(B64[i : i + 8] for i in range(0, len(B64), 8))
    assert not call(mc, pat(db_session, ana, ["write"]), "upload", filename="w.pdf", content_base64=wrapped)[
        0
    ]
