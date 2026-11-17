# ruff: noqa: F811
"""Task 5: label fields in MCP output, propose_label, handoff manifest labels and withheld reasons."""

import pytest
from api_seed import seed
from conftest import bearer
from mcp_helpers import call, rpc
from sqlalchemy import select
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401
from test_search_api import hit

from roracle.models import Collection, CollectionItem, Proposal

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def mk(client, admin, name, rank, **gates):
    r = client.post(
        "/admin/labels", json={"name": name, "colour": "#aabbcc", "rank": rank, "gates": gates}, headers=admin
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def label(db, d, lid):
    d.label_id = lid
    db.commit()


def test_tools_list_has_propose_label(mc, ana, db_session, fes):
    h = pat(db_session, ana, ["read"])
    names = {t["name"] for t in rpc(mc, h, "tools/list").json()["result"]["tools"]}
    assert "propose_label" in names


def test_read_pat_cannot_propose_label(mc, ana, db_session, fes):
    d, _ = seed(db_session, ana)
    err, text = call(mc, pat(db_session, ana, ["read"]), "propose_label", doc_id=str(d.id), label="General")
    assert err and "insufficient scope" in text


def test_labels_in_search_get_passages_collections_and_brief(client, mc, ana, admin, db_session, fes):
    sec = mk(client, admin, "Sec", 3, exclude_llm=True)
    d, v = seed(db_session, ana, title="Plain")
    d2, v2 = seed(db_session, ana, title="Sec doc")
    label(db_session, d2, sec)
    col = Collection(owner_id=ana, name="c")
    db_session.add(col)
    db_session.flush()
    db_session.add_all([CollectionItem(collection_id=col.id, document_id=x.id) for x in (d, d2)])
    db_session.commit()
    fes.hits = [hit(d, v, "t1"), hit(d2, v2, "t2")]
    h = pat(db_session, ana, ["read"])
    res = call(mc, h, "search", query="alpha")[1]["results"]
    labs = {r["title"]: r["label"] for r in res}
    assert labs["Plain"] == {"name": "General", "rank": 0} and labs["Sec doc"] == {"name": "Sec", "rank": 3}
    got = call(mc, h, "get_document", doc_id=str(d2.id))[1]["label"]
    assert got == {"name": "Sec", "rank": 3}
    e, ps = call(mc, h, "get_passages", chunk_ids=[f"{v.id}:0"])
    assert not e, ps
    ps = ps["result"]
    assert ps[0]["label"] == {"name": "General", "rank": 0}
    cols = call(mc, h, "list_collections")[1]["result"]
    assert cols[0]["max_label"] == {"name": "Sec", "rank": 3}


def test_blocked_doc_invisible_to_mcp(client, mc, ana, admin, db_session, fes):
    sec = mk(client, admin, "NoAg", 3, block_agents=True)
    d, v = seed(db_session, ana, title="Hidden")
    label(db_session, d, sec)
    fes.hits = [hit(d, v, "SECRET")]
    h = pat(db_session, ana, ["read"])
    assert call(mc, h, "search", query="alpha")[1]["results"] == []
    assert call(mc, h, "get_passages", chunk_ids=[f"{v.id}:0"])[1]["result"] == []
    err, text = call(mc, h, "get_document", doc_id=str(d.id))
    err2, text2 = call(mc, h, "get_document", doc_id="00000000-0000-0000-0000-000000000000")
    assert err and err2 and text == text2
    assert call(mc, h, "list_collections")[0] is False


def test_propose_label_by_name(client, mc, ana, admin, db_session, fes):
    mk(client, admin, "Sec", 3)
    d, _ = seed(db_session, ana)
    h = pat(db_session, ana, ["read", "write"])
    err, out = call(mc, h, "propose_label", doc_id=str(d.id), label="sec", reason="why")
    assert not err and out["status"] == "pending"
    row = db_session.scalars(select(Proposal)).one()
    assert row.kind == "label" and row.payload["document_id"] == str(d.id)
    assert "reason" not in row.payload  # agent reason is dropped: the approver supplies it
    err, text = call(mc, h, "propose_label", doc_id=str(d.id), label="nope")
    assert err and "General" in text and "Sec" in text


def test_handoff_manifest_labels_and_withheld_reasons(client, mc, as_ana, ana, bob, admin, db_session, fes):
    nollm = mk(client, admin, "NoLLM", 2, exclude_llm=True)
    noag = mk(client, admin, "NoAg", 3, exclude_llm=True, block_agents=True)
    plain, _ = seed(db_session, ana, title="Plain")
    a, _ = seed(db_session, ana, title="Excl")
    b, _ = seed(db_session, ana, title="Blocked")
    label(db_session, a, nollm)
    label(db_session, b, noag)
    for d in (plain, a, b):
        d.visibility = "everyone"
    db_session.commit()
    r = client.post(
        "/handoffs",
        json={"kind": "search", "title": "h", "doc_ids": [str(x.id) for x in (plain, a, b)],
              "audience": "authenticated"},
        headers=as_ana,
    )  # fmt: skip
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    human = client.get(f"/handoffs/{code}", headers=as_ana).json()
    assert {d["title"]: d["label"]["name"] for d in human["documents"]} == {
        "Plain": "General",
        "Excl": "NoLLM",
    }  # R10: block_agents dropped for every loader
    assert human["withheld"] == 0 and "Blocked" not in str(human)
    agent = client.get(f"/handoffs/{code}", headers=pat(db_session, ana, ["read"])).json()
    titles = {d["title"]: d for d in agent["documents"]}
    assert set(titles) == {"Plain", "Excl"} and "Blocked" not in str(agent)
    assert titles["Excl"]["text_withheld"] and not titles["Plain"]["text_withheld"]
    assert agent["withheld_by_reason"] == {"unavailable": 0, "label_text": 1}  # blocked doc dropped at create
    err, out = call(mc, pat(db_session, ana, ["read"]), "load_handoff", code=code)
    assert not err and out["documents"][0]["label"].keys() == {"name", "rank"}
