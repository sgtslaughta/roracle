"""Gate (a): exclude_llm content never reaches an LLM prompt. ES is faked and ignores labels, so every
assertion here also proves Postgres (current label) is the authority when ES lags behind a raise."""

# ruff: noqa: F811
import json

import pytest
from api_seed import seed
from conftest import bearer
from mcp_helpers import call
from sqlalchemy import select
from test_chat_retrieve import FakeES, FakeLLM, me, other, run  # noqa: F401
from test_chat_retrieve import hit as chit
from test_chat_stream import FakeStreamLLM, new_chat, setup, sse  # noqa: F401
from test_cite_check import cara, check, cite, es, hit, llm, mk  # noqa: F401
from test_mcp_tools import fes as mfes  # noqa: F401
from test_mcp_tools import llm as mllm  # noqa: F401
from test_mcp_tools import mc, pat  # noqa: F401

from roracle.chat.retrieve import Selection, Tab, Viewer
from roracle.mcp.tools_write import NO_PASSAGES
from roracle.models import Document
from roracle.models_labels import Label
from roracle.notes.community import community_notes

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def mklabel(client, admin, name, rank, **gates):
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


@pytest.fixture
def ex(client, admin):
    return mklabel(client, admin, "NoLLM", 3, exclude_llm=True)


def texts(ps):
    return " ".join(x.text for x in ps)


# ---- chat retrieval ----
def test_ranked_tab_selection_pin_all_excluded_and_reported(db_session, settings, me, other, ex):
    ok, okv = seed(db_session, me.user_id, title="Plain")
    bad, badv = seed(db_session, me.user_id, title="Locked")
    label(db_session, bad, ex)
    es = FakeES([chit(bad, badv, text="LOCKED-RANKED"), chit(ok, okv, text="plain text")])
    v = Viewer(
        tabs=[Tab(doc_id=str(bad.id))], selection=Selection(doc_id=str(bad.id), page=1, text="LOCKED-SEL")
    )
    excluded: list = []
    from roracle.chat.retrieve import retrieve_for_chat

    out = retrieve_for_chat(
        db_session, es, FakeLLM(), settings, me, "alpha", v, "all", [str(bad.id)], [], excluded=excluded
    )
    assert texts(out) == "plain text"
    assert excluded == [{"doc_id": str(bad.id), "label": "NoLLM"}]
    # ES filter carries must_not label ids too
    flt = es.calls[0]["query"]["bool"]["filter"]
    assert {"bool": {"must_not": [{"terms": {"label_id": [ex]}}]}} in flt


def test_invisible_doc_not_reported_as_excluded(db_session, settings, me, other, ex):
    secret, sv = seed(db_session, other.id, visibility="private")
    label(db_session, secret, ex)
    excluded: list = []
    from roracle.chat.retrieve import retrieve_for_chat

    retrieve_for_chat(
        db_session, FakeES([chit(secret, sv)]), FakeLLM(), settings, me, "q",
        Viewer(tabs=[Tab(doc_id=str(secret.id))]), "all", [str(secret.id)], [], excluded=excluded,
    )  # fmt: skip
    assert excluded == []  # no label hint for documents the caller cannot see


def test_chat_stream_prompt_and_meta_frame(client, as_ana, ana, db_session, setup, ex):
    ok, okv = seed(db_session, ana, title="Plain")
    bad, badv = seed(db_session, ana, title="Locked")
    label(db_session, bad, ex)
    from test_chat_stream import hit as shit

    llm = setup(hits=[shit(bad, badv, "LOCKED-TEXT"), shit(ok, okv, "plain words")])
    cid = new_chat(client, as_ana, pins=[str(bad.id)])
    r = client.post(
        f"/chats/{cid}/messages", headers=as_ana,
        json={"content": "q", "viewer": {"tabs": [{"doc_id": str(bad.id)}]}},
    )  # fmt: skip
    assert r.status_code == 200
    ev = sse(r)
    assert ev[0] == ("meta", {"excluded_by_label": [{"doc_id": str(bad.id), "label": "NoLLM"}]})
    sent = json.dumps(llm.received)
    assert "LOCKED-TEXT" not in sent and "plain words" in sent


def test_mcp_ask_excludes_even_when_es_lags(mc, as_ana, ana, db_session, mfes, mllm, ex):
    from test_search_api import hit as ahit

    d, v = seed(db_session, ana, title="Locked")
    mfes.hits = [ahit(d, v, "LOCKED-TEXT")]
    label(db_session, d, ex)
    err, out = call(mc, as_ana, "ask", question="alpha?")
    assert not err and out["answer"] == NO_PASSAGES and not mllm.sent


def test_community_notes_strict_skips_excluded_doc(db_session, me, ana, ex):
    d, _ = seed(db_session, me.user_id)
    label(db_session, d, ex)
    assert community_notes(db_session, me, {str(d.id): {1}}, strict=(0, 0.0)) == {}


# ---- cite_check / validate ----
def test_cite_excluded_doc_is_unchecked_judge_not_called(client, as_ana, ana, db_session, es, llm, ex):
    d, v = seed(db_session, ana, title="Locked")
    es.hits = [hit(d, v)]
    label(db_session, d, ex)
    i = mk(client, as_ana, cite(f"doc:{d.id}", "Sentence one"))
    r = check(client, as_ana, i)
    assert r.status_code == 200, r.text
    it = r.json()["items"][0]
    assert (it["status"], it["verdict"], it["reason"]) == (
        "resolved",
        "unchecked",
        "label excludes LLM review",
    )
    assert r.json()["ok"] is False and llm.calls == []


def test_unchecked_is_ackable_not_blocking(client, as_ana, ana, db_session, es, llm, ex):
    d, _ = seed(db_session, ana)
    label(db_session, d, ex)
    i = mk(client, as_ana, cite(f"doc:{d.id}", "Sentence one"))
    it = check(client, as_ana, i).json()["items"][0]
    r = client.post(
        f"/drafts/{i}/citations/ack", headers=as_ana,
        json={"key": it["key"], "sentence_hash": it["sentence_hash"], "reason": "reviewed by hand"},
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["ok"] is True


def test_draft_with_excluded_label_no_llm_in_cite_or_validate(client, as_ana, ana, db_session, es, llm, ex):
    d, v = seed(db_session, ana, title="Plain")
    es.hits = [hit(d, v)]
    i = mk(client, as_ana, cite(f"doc:{d.id}", "Sentence one"))
    draft = db_session.get(Document, __import__("uuid").UUID(i))
    label(db_session, draft, ex)
    r = check(client, as_ana, i)
    assert r.json()["items"][0]["verdict"] == "unchecked" and llm.calls == []
    r = client.post(f"/drafts/{i}/validate", headers=as_ana)
    assert r.status_code == 200, r.text
    warns = [w["message"] for w in r.json()["warnings"] if w["rule"] == "label"]
    assert len(warns) == 1 and "excludes LLM" in warns[0]
    assert llm.calls == []  # no style check either


def test_validate_warns_on_unknown_profile_default_label(client, as_ana, ana, es, llm, monkeypatch):
    from roracle.writing import profiles, validate

    prof = profiles.builtin()["journal-article"]
    prof = prof.model_copy(update={"default_label": "nope", "rules": {**prof.rules, "style": "off"}})
    monkeypatch.setattr(validate.drafts, "profile_for", lambda *a, **k: (prof, 1, "builtin"))
    i = mk(client, as_ana, "Body.\n")
    r = client.post(f"/drafts/{i}/validate", headers=as_ana)
    assert r.status_code == 200, r.text
    assert any(w["rule"] == "label" and "nope" in w["message"] for w in r.json()["warnings"])
    monkeypatch.setattr(
        validate.drafts,
        "profile_for",
        lambda *a, **k: (prof.model_copy(update={"default_label": "general"}), 1, "builtin"),
    )
    r = client.post(f"/drafts/{i}/validate", headers=as_ana)
    assert not [w for w in r.json()["warnings"] if w["rule"] == "label"]  # case-insensitive known name


def test_label_facet_filter_counts(client, as_ana, ana, db_session, fes_search, ex):
    seed(db_session, ana, title="A")
    b, _ = seed(db_session, ana, title="B")
    label(db_session, b, ex)
    body = client.get("/search", params={"label": ex}, headers=as_ana).json()
    assert [r["title"] for r in body["results"]] == ["B"]
    vals = {f["value"]: f["count"] for f in body["facets"]["label"]}
    assert vals[ex] == 1 and sum(vals.values()) == 2  # disjunctive: other labels still counted
    assert db_session.scalar(select(Label.id).where(Label.name == "NoLLM")) is not None


@pytest.fixture
def fes_search(app):
    from api_seed import FakeES as F

    from roracle.api.routes.library import get_es

    app.dependency_overrides[get_es] = lambda: F()
