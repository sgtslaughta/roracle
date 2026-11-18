import uuid
from types import SimpleNamespace

import httpx
import pytest
from api_seed import seed
from mcp_helpers import call
from sqlalchemy import select
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401

from roracle.api.routes.search import get_llm
from roracle.models_writing import Draft, DraftVersion, WritingProfile
from roracle.writing import profiles

BASE = (
    "---\ntitle: T\nauthors: [a]\nkeywords: [k]\nai_assistance: {used: false}\n---\n"
    "## Abstract\n\nShort.\n\n## Introduction\n\nHello world.\n"
)


class Llm:
    def __init__(self, text='{"warnings": [{"message": "too passive", "quote": "Hello world"}]}', exc=None):
        self.text, self.exc, self.calls = text, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))])


@pytest.fixture
def fake(client):
    x = Llm()
    client.app.dependency_overrides[get_llm] = lambda: x
    return x


def mk(client, h, content=BASE, **kw):
    r = client.post("/drafts", headers=h, json={"title": "T", "content": content, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def profile(db, rules, **spec):
    spec = {"name": "p1", "title": "P1", "rules": rules, **spec}
    db.add(WritingProfile(name="p1", version=1, yaml="", spec=profiles.Profile(**spec).model_dump()))
    db.commit()


def test_report_shape_stored_and_style(client, as_ana, ana, db_session, fake):
    i = mk(client, as_ana)
    r = client.post(f"/drafts/{i}/validate", headers=as_ana)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["version"] == 1 and rep["profile"] == {
        "name": "journal-article",
        "version": 0,
        "source": "builtin",
    }
    assert [w["message"] for w in rep["warnings"] if w["rule"] == "style"] == ["too passive"]
    assert next(w for w in rep["warnings"] if w["rule"] == "style")["line"] == 13
    assert {f["rule"] for f in rep["blocking"]} == {"structure"} and rep["ok"] is False
    assert fake.calls[0]["model"] == "qwen2.5:14b" and fake.calls[0]["messages"][1]["content"] == BASE
    v = db_session.scalar(select(DraftVersion).where(DraftVersion.document_id == uuid.UUID(i)))
    assert v.validation == rep
    client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": BASE + "x"})
    assert client.post(f"/drafts/{i}/validate", headers=as_ana).json()["version"] == 2


def test_severity_off_and_warning(client, as_ana, db_session, fake):
    profile(
        db_session, {"structure": "warning", "style": "off", "metadata": "off"}, required_sections=["Nope"]
    )
    i = mk(client, as_ana, profile="p1")
    rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
    assert rep["profile"]["source"] == "db" and rep["ok"] is True
    assert [w["rule"] for w in rep["warnings"]] == ["structure"] and fake.calls == []


def test_style_blocking_and_outage(client, as_ana, db_session, fake):
    profile(db_session, {"style": "blocking", "citations": "off"})
    i = mk(client, as_ana, profile="p1")
    rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
    assert [b["message"] for b in rep["blocking"]] == ["too passive"]
    for bad in (Llm("garbage"), Llm('{"warnings": 3}'), Llm(exc=httpx.ConnectError("down"))):
        client.app.dependency_overrides[get_llm] = lambda bad=bad: bad
        rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
        assert rep["ok"] is True and [w["message"] for w in rep["warnings"]] == ["style check unavailable"]
        assert rep["style_unavailable"] is True


def test_citations_resolution_in_report(client, as_ana, as_bob, ana, bob, db_session, fake):
    mine, _ = seed(db_session, ana, title="MINETITLE", visibility="everyone")
    hidden, hv = seed(db_session, bob, title="SECRETTITLE", visibility="private")
    ghost = uuid.uuid4()
    _pend, pv = seed(db_session, bob, title="PEND", visibility="everyone", stage="embedded")
    db_session.commit()
    md = (
        BASE
        + f"\nA [@doc:{mine.id}] B [@doc:{hidden.id}] C [@doc:{ghost}] D [@psg:{hv.id}:1] E [@psg:{pv.id}:1] F [@doc:bad].\n"
    )
    profile(db_session, {"citations": "blocking", "style": "off"})
    i = mk(client, as_ana, md, profile="p1")
    rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
    msgs = [b["message"] for b in rep["blocking"]]
    assert (
        len(msgs) == 5
        and sum("does not resolve" in m for m in msgs) == 4
        and sum("malformed" in m for m in msgs) == 1
    )
    assert "SECRETTITLE" not in str(rep) and "PEND" not in str(rep) and "MINETITLE" not in str(rep)
    assert str(mine.id) not in " ".join(msgs)
    # owner of the hidden doc may cite it
    j = mk(client, as_bob, BASE + f"\nA [@doc:{hidden.id}] B [@psg:{pv.id}:1].\n", profile="p1")
    assert client.post(f"/drafts/{j}/validate", headers=as_bob).json()["ok"] is True


def test_draft_not_citable(client, as_ana, db_session, fake):
    profile(db_session, {"citations": "blocking", "style": "off"})
    other = mk(client, as_ana)
    i = mk(client, as_ana, BASE + f"\nA [@doc:{other}].\n", profile="p1")
    assert client.post(f"/drafts/{i}/validate", headers=as_ana).json()["ok"] is False


def test_unknown_stored_profile_falls_back(client, as_ana, db_session, fake):
    profile(db_session, {"style": "off"})
    i = mk(client, as_ana, profile="p1")
    db_session.get(WritingProfile, db_session.scalar(select(WritingProfile.id))).active = False
    db_session.commit()
    rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
    assert rep["profile"]["name"] == "journal-article"


def test_stranger_404_coauthor_ok(client, as_ana, as_bob, bob, fake):
    i = mk(client, as_ana)
    assert client.post(f"/drafts/{i}/validate", headers=as_bob).status_code == 404
    assert client.post(f"/drafts/{uuid.uuid4()}/validate", headers=as_bob).status_code == 404
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    assert client.post(f"/drafts/{i}/validate", headers=as_bob).status_code == 200


def test_mcp_validate(mc, ana, bob, db_session, fes, llm):  # noqa: F811
    h = pat(db_session, ana, ["read", "write"])
    _, out = call(mc, h, "save_draft", content=BASE, title="T")
    llm.text = '{"warnings": []}'
    err, rep = call(mc, pat(db_session, ana, ["read", "write"]), "validate_draft", draft_id=out["draft_id"])
    assert not err and rep["version"] == 1 and rep["profile"]["name"] == "journal-article"
    assert db_session.scalar(select(DraftVersion.validation)) == rep
    err, text = call(mc, pat(db_session, bob, ["read", "write"]), "validate_draft", draft_id=out["draft_id"])
    assert err and "not found" in text
    assert call(mc, h, "validate_draft", draft_id="zzz")[0]


def test_submitted_draft_is_citable(client, as_bob, as_ana, ana, db_session, fake):
    profile(db_session, {"citations": "blocking", "style": "off"})
    pub, _ = seed(db_session, ana, title="paper", visibility="everyone")  # a published paper with a draft row
    d = Draft(document_id=pub.id, status="draft")
    db_session.add(d)
    db_session.commit()
    j = mk(client, as_bob, BASE + f"\nA [@doc:{pub.id}].\n", profile="p1")
    assert client.post(f"/drafts/{j}/validate", headers=as_bob).json()["ok"] is False
    d.status, d.submitted_version = "submitted", 1
    db_session.commit()
    assert client.post(f"/drafts/{j}/validate", headers=as_bob).json()["ok"] is True


def test_create_keeps_content_verbatim(client, as_ana):
    i = mk(client, as_ana, "hello\n\n")
    assert client.get(f"/drafts/{i}", headers=as_ana).json()["content"] == "hello\n\n"


def test_too_many_citations_blocks(client, as_ana, db_session, fake):
    profile(db_session, {"citations": "warning", "style": "off", "structure": "off", "metadata": "off"})
    cites = " ".join(f"[@doc:{uuid.uuid4()}]" for _ in range(201))
    i = mk(client, as_ana, BASE + cites + "\n", profile="p1")
    rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
    assert [b["message"] for b in rep["blocking"]] == ["too many citations (max 200)"] and not rep["ok"]
    assert len(rep["warnings"]) == 100  # per-rule cap


def test_time_budget_fails_closed(client, as_ana, db_session, fake, monkeypatch):
    from roracle.writing import validate as v

    ticks = iter(range(0, 10000, 100))
    monkeypatch.setattr(v, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    i = mk(client, as_ana)
    rep = client.post(f"/drafts/{i}/validate", headers=as_ana).json()
    assert rep["ok"] is False and rep["blocking"] == [
        {"rule": "limits", "message": "validation exceeded time budget", "line": None}
    ]
    assert fake.calls == []


def test_style_check_has_its_own_timeout(client, as_ana, fake):
    import openai

    from roracle.writing import validate as v

    seen = []
    fake.with_options = lambda **kw: (seen.append(kw), fake)[1]
    did = mk(client, as_ana)
    client.post(f"/drafts/{did}/validate", headers=as_ana)
    assert seen == [{"timeout": 30.0}]  # writing_style_timeout_s, not the 120 s LLM timeout
    slow = Llm(exc=openai.APITimeoutError(request=httpx.Request("POST", "http://llm")))
    prof = profiles.Profile(name="p", title="P", rules={"style": "warning"})
    assert [f.message for f in v.check_style(slow, "m", "x", prof, 1.0)] == [v.UNAVAILABLE]
