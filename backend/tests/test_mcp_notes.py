import pytest
from api_seed import seed
from mcp_helpers import call, rpc
from sqlalchemy import select
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401

from roracle.models import Annotation, AuditLog, Proposal

TOOLS = {"list_annotations", "propose_annotation", "reply_to_annotation", "export_bibtex"}


@pytest.fixture
def doc(db_session, ana):
    d, _ = seed(db_session, ana, visibility="everyone", title="T")
    db_session.commit()
    return d


def test_tools_listed(mc, ana, db_session, fes):  # noqa: F811
    r = rpc(mc, pat(db_session, ana, ["read"]), "tools/list")
    assert TOOLS <= {t["name"] for t in r.json()["result"]["tools"]}


def test_read_pat_cannot_propose(mc, ana, db_session, fes, doc):  # noqa: F811
    h = pat(db_session, ana, ["read"])
    err, text = call(mc, h, "propose_annotation", doc_id=str(doc.id), kind="comment", body="x")
    assert err and "insufficient scope" in text
    assert db_session.scalars(select(Proposal)).all() == []
    assert not call(mc, h, "list_annotations", doc_id=str(doc.id))[0]


def test_propose_reply_list_export(mc, ana, db_session, fes, doc):  # noqa: F811
    h = pat(db_session, ana, ["read", "write"])
    err, out = call(
        mc, h, "propose_annotation", doc_id=str(doc.id), kind="finding", body="secret body", quote="q", page=1
    )
    assert not err and out["status"] == "pending"
    row = db_session.scalars(select(Proposal)).one()
    assert str(row.id) == out["proposal_id"] and row.payload["visibility"] == "private" and row.client
    assert db_session.scalars(select(Annotation)).all() == []
    audits = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp.propose_annotation")).all()
    assert audits and "secret body" not in str(audits[0].target) + str(audits[0].detail)
    n = Annotation(
        document_id=doc.id, version_id=doc.id, author_id=ana, kind="comment", body="r", visibility="everyone"
    )
    from roracle.models import DocumentVersion

    n.version_id = db_session.scalar(select(DocumentVersion.id))
    db_session.add(n)
    db_session.commit()
    items = call(mc, h, "list_annotations", doc_id=str(doc.id))[1]["items"]
    assert [i["body"] for i in items] == ["r"]
    err, out = call(mc, h, "reply_to_annotation", annotation_id=str(n.id), body="re")
    assert not err and db_session.scalars(select(Proposal)).all()[-1].payload["parent_id"] == str(n.id)
    err, out = call(mc, h, "export_bibtex", doc_ids=[str(doc.id)])
    assert not err and out["count"] == 1 and "@article" in out["bibtex"]
    assert call(mc, h, "list_annotations", doc_id="nope")[0]


def test_cap_error_and_bad_input(mc, ana, db_session, fes, doc):  # noqa: F811
    h = pat(db_session, ana, ["write"])
    for _ in range(50):
        assert not call(mc, h, "propose_annotation", doc_id=str(doc.id), kind="comment", body="x")[0]
    err, text = call(mc, h, "propose_annotation", doc_id=str(doc.id), kind="comment", body="x")
    assert err and "too many pending proposals" in text
    err, text = call(mc, h, "propose_annotation", doc_id=str(doc.id), kind="relation", body="x")
    assert err
