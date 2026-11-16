from mcp_helpers import call, rpc
from sqlalchemy import select
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401

from roracle.models import AuditLog
from roracle.models_writing import DraftVersion


def test_listed_and_needs_write(mc, ana, db_session, fes):  # noqa: F811
    h = pat(db_session, ana, ["read"])
    assert "save_draft" in {t["name"] for t in rpc(mc, h, "tools/list").json()["result"]["tools"]}
    err, text = call(mc, h, "save_draft", content="x", title="T")
    assert err and "insufficient scope" in text
    assert db_session.scalars(select(DraftVersion)).all() == []


def test_create_then_save_and_acl(mc, ana, bob, db_session, fes):  # noqa: F811
    h = pat(db_session, ana, ["read", "write"])
    err, out = call(mc, h, "save_draft", content="SECRETBODY", title="T")
    assert not err and out["version"] == 1 and out["url"] == f"/drafts/{out['draft_id']}"
    err, out2 = call(mc, h, "save_draft", content="v2", draft_id=out["draft_id"], base_version=1)
    assert not err and out2["version"] == 2
    err, text = call(mc, h, "save_draft", content="v3", draft_id=out["draft_id"], base_version=1)
    assert err and "draft_changed" in text and "{" not in text and "version=2" in text  # clean, not a repr
    err, text = call(mc, h, "save_draft", content="x")
    assert err and "title" in text
    err, text = call(mc, h, "save_draft", content="x", title="T", profile="nope")
    assert err and "unknown profile" in text
    hb = pat(db_session, bob, ["read", "write"])
    err, text = call(mc, hb, "save_draft", content="evil", draft_id=out["draft_id"])
    assert err and "not found" in text
    assert call(mc, hb, "save_draft", content="x", draft_id="nonsense")[0]
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.save_draft")).all()
    assert out["draft_id"] in {r.target for r in rows} and all("SECRETBODY" not in r.target for r in rows)
    assert db_session.scalars(select(DraftVersion).where(DraftVersion.content == "evil")).all() == []
