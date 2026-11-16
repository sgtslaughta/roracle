import uuid

import pytest
from conftest import bearer
from sqlalchemy import select

from roracle.models import AuditLog, Document, DocumentShare, DocumentVersion, Job, Notification
from roracle.models_writing import Draft, DraftVersion, WritingProfile
from roracle.writing import build, profiles

pytestmark = pytest.mark.usefixtures("_blob_store_override")

MD = """---
title: T
authors: [Ana]
keywords: [a]
ai_assistance:
  used: true
  tools: [claude]
  description: edited prose
---
# Abstract
Hi.
"""


class FakeBuilder:
    def __init__(self, calls, err=None):
        self.calls, self.err = calls, err

    def build(self, markdown, refs, profile):
        self.calls.append(markdown)
        if self.err:
            raise self.err
        return b"%PDF-1.4 fake"


@pytest.fixture
def built(monkeypatch):
    calls = []
    monkeypatch.setattr(build, "get_builder", lambda settings, fmt: FakeBuilder(calls))
    return calls


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


@pytest.fixture
def cara(make_token):
    return bearer(make_token(sub="cara", username="cara", groups=("/Chem",)))


def mk(client, h, content=MD, **kw):
    r = client.post("/drafts", headers=h, json={"title": "My Paper", "content": content, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def reports(db, doc_id, by, validation_ok=True, citations_ok=True):
    v = db.scalars(
        select(DraftVersion)
        .where(DraftVersion.document_id == uuid.UUID(doc_id))
        .order_by(DraftVersion.version.desc())
    ).first()
    v.validation = {
        "version": v.version,
        "checked_by": str(by),
        "ok": validation_ok,
        "blocking": [],
        "warnings": [],
    }
    v.citations = {"version": v.version, "checked_by": str(by), "ok": citations_ok, "items": []}
    db.commit()


def sub(client, h, i, vis="everyone", **kw):
    return client.post(f"/drafts/{i}/submit", headers=h, json={"visibility": vis, **kw})


def code(r):
    return r.json()["detail"]["code"]


def test_gates(client, as_ana, ana, db_session, built):
    i = mk(client, as_ana)
    assert code(sub(client, as_ana, i)) == "checks_required"
    reports(db_session, i, ana, validation_ok=False)
    assert code(sub(client, as_ana, i)) == "blocking_findings"
    reports(db_session, i, ana, citations_ok=False)
    assert code(sub(client, as_ana, i)) == "citations_unresolved"
    reports(db_session, i, ana)
    client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": MD})  # stale reports
    assert code(sub(client, as_ana, i)) == "checks_required"
    assert built == [] and db_session.scalars(select(DocumentVersion)).all() == []


def test_missing_disclosure(client, as_ana, ana, db_session, built):
    i = mk(client, as_ana, content="---\ntitle: T\n---\nbody\n")
    reports(db_session, i, ana)
    r = sub(client, as_ana, i)
    assert r.status_code == 409 and code(r) == "ai_disclosure_required"


def test_coauthor_reports_do_not_count(client, as_ana, ana, as_bob, bob, db_session, built):
    i = mk(client, as_ana)
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    reports(db_session, i, bob)
    assert code(sub(client, as_ana, i)) == "checks_required"


def test_happy_path(client, as_ana, ana, as_bob, bob, db_session, built, s3):
    i = mk(client, as_ana)
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)], "groups": ["/Chem/"]})
    reports(db_session, i, ana)
    r = sub(client, as_ana, i, "org_unit")
    assert r.status_code == 200 and r.json()["document_version"] == 1, r.text
    v = db_session.scalars(select(DocumentVersion)).one()
    assert v.stage == "uploaded" and v.filename == "my-paper.pdf" and v.content_type == "application/pdf"
    assert {j.kind for j in db_session.scalars(select(Job))} >= {"ingest", "acl_sync"}
    doc = db_session.get(Document, uuid.UUID(i))
    assert doc.visibility == "org_unit"
    shares = {(s.principal_kind, s.principal) for s in db_session.scalars(select(DocumentShare))}
    assert shares == {("user", str(bob)), ("group", "/Chem")}
    d = db_session.get(Draft, doc.id)
    assert d.status == "submitted" and d.submitted_version == 1
    assert client.get(f"/documents/{i}", headers=as_ana).json()["ai_disclosure"] == {
        "used": True, "tools": ["claude"], "description": "edited prose"
    }  # fmt: skip
    assert code(sub(client, as_ana, i)) == "nothing_to_submit"


def test_save_after_submit_then_version_2(client, as_ana, ana, db_session, built):
    i = mk(client, as_ana)
    reports(db_session, i, ana)
    assert sub(client, as_ana, i).status_code == 200
    assert (
        client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": MD + "more\n"}).status_code == 200
    )
    reports(db_session, i, ana)
    r = sub(client, as_ana, i, "private")
    assert r.status_code == 200 and r.json()["document_version"] == 2
    assert db_session.get(Draft, uuid.UUID(i)).status == "submitted"
    assert db_session.get(Document, uuid.UUID(i)).visibility == "private"


def test_unknown_share_user_422(client, as_ana, ana, db_session, built):
    i = mk(client, as_ana)
    reports(db_session, i, ana)
    r = sub(client, as_ana, i, "shared", share_users=[str(uuid.uuid4())])
    assert r.status_code == 422 and built == []
    r = sub(client, as_ana, i, "shared", share_groups=["/Phys/"])
    assert r.status_code == 200
    assert {s.principal for s in db_session.scalars(select(DocumentShare))} == {"/Phys"}


@pytest.mark.parametrize("status", [422, 501, 503])
def test_build_failure_leaves_draft_unchanged(client, as_ana, ana, db_session, monkeypatch, status):
    i = mk(client, as_ana)
    reports(db_session, i, ana)
    err = build.BuildError("nope", status)
    monkeypatch.setattr(build, "get_builder", lambda s, f: FakeBuilder([], err))
    r = sub(client, as_ana, i)
    assert r.status_code == status
    d = db_session.get(Draft, uuid.UUID(i))
    assert d.status == "draft" and d.submitted_version is None and d.ai_disclosure is None
    assert db_session.scalars(select(DocumentVersion)).all() == []
    assert db_session.scalars(select(Job)).all() == []
    assert db_session.get(Document, uuid.UUID(i)).visibility == "private"


def test_non_owner(client, as_ana, ana, as_bob, bob, db_session, built):
    i = mk(client, as_ana)
    assert sub(client, as_bob, i).status_code == 404
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    assert sub(client, as_bob, i).status_code == 403


def test_review_flow(client, as_ana, ana, admin, db_session, built):
    db_session.add(
        WritingProfile(
            name="rev",
            version=1,
            yaml="",
            spec=profiles.Profile(name="rev", title="R", review_required=True).model_dump(),
        )
    )
    db_session.commit()
    client.get("/me", headers=admin)
    i = mk(client, as_ana, profile="rev")
    reports(db_session, i, ana)
    r = sub(client, as_ana, i)
    assert r.json()["status"] == "in_review" and built == []
    assert db_session.scalars(select(DocumentVersion)).all() == []
    assert [n.kind for n in db_session.scalars(select(Notification))] == ["draft.review_requested"]
    assert client.put(f"/drafts/{i}/content", headers=as_ana, json={"content": "x"}).status_code == 409
    assert code(sub(client, as_ana, i)) == "in_review"
    assert client.get("/admin/drafts/reviews", headers=as_ana).status_code == 403
    assert (
        client.post(f"/admin/drafts/{i}/review", headers=as_ana, json={"decision": "approve"}).status_code
        == 403
    )
    assert [x["id"] for x in client.get("/admin/drafts/reviews", headers=admin).json()["items"]] == [i]
    r = client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "reject", "note": "fix"})
    assert r.status_code == 200
    d = db_session.get(Draft, uuid.UUID(i))
    db_session.refresh(d)
    assert d.status == "draft" and d.review_note == "fix"
    assert "draft.rejected" in [n.kind for n in db_session.scalars(select(Notification))]
    assert (
        client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "approve"}).status_code
        == 409
    )
    assert sub(client, as_ana, i).json()["status"] == "in_review"
    r = client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    assert built == [MD]
    assert db_session.scalars(select(DocumentVersion)).one().version == 1
    kinds = [n.kind for n in db_session.scalars(select(Notification))]
    assert "draft.approved" in kinds
    acts = {a.action for a in db_session.scalars(select(AuditLog))}
    assert {"draft.review", "draft.submit", "draft.publish", "admin.reviews"} <= acts


def test_reject_after_publication_stays_submitted(client, as_ana, ana, admin, db_session, built):
    i = mk(client, as_ana)
    reports(db_session, i, ana)
    assert sub(client, as_ana, i).status_code == 200
    d = db_session.get(Draft, uuid.UUID(i))
    d.status = "in_review"
    d.target = {"version": 1}
    db_session.commit()
    client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "reject"})
    db_session.refresh(d)
    assert d.status == "submitted"


def test_approve_build_error_keeps_in_review(client, as_ana, ana, admin, db_session, monkeypatch, built):
    db_session.add(
        WritingProfile(
            name="rev",
            version=1,
            yaml="",
            spec=profiles.Profile(name="rev", title="R", review_required=True).model_dump(),
        )
    )
    db_session.commit()
    i = mk(client, as_ana, profile="rev")
    reports(db_session, i, ana)
    sub(client, as_ana, i)
    monkeypatch.setattr(build, "get_builder", lambda s, f: FakeBuilder([], build.BuildError("down", 503)))
    assert (
        client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "approve"}).status_code
        == 503
    )
    assert db_session.get(Draft, uuid.UUID(i)).status == "in_review"
    assert db_session.scalars(select(DocumentVersion)).all() == []


def test_coauthor_group_forms_grant_access(client, as_ana, cara, db_session):
    i = mk(client, as_ana)
    assert client.get(f"/drafts/{i}", headers=cara).status_code == 404
    r = client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"groups": ["/Chem/"]})
    assert r.json()["coauthors"]["groups"] == ["/Chem"]
    assert client.get(f"/drafts/{i}", headers=cara).status_code == 200
    s = db_session.scalars(select(DocumentShare)).all()
    assert [(x.principal_kind, x.principal) for x in s] == [("group", "/Chem")]


@pytest.fixture
def nogroup(make_token):
    return bearer(make_token(sub="eve", username="eve", groups=()))


def test_org_unit_fail_closed_at_submit(client, nogroup, db_session, built):
    eve = client.get("/me", headers=nogroup).json()["id"]
    i = mk(client, nogroup)
    reports(db_session, i, eve)
    r = sub(client, nogroup, i, "org_unit")
    assert r.status_code == 422 and "org unit" in r.json()["detail"] and built == []
    assert db_session.get(Draft, uuid.UUID(i)).status == "draft"
    assert sub(client, nogroup, i, "private").status_code == 200


def test_org_unit_gate_on_approval_and_patch(client, nogroup, admin, db_session, built, as_ana, ana):
    eve = client.get("/me", headers=nogroup).json()["id"]
    i = mk(client, nogroup)
    reports(db_session, i, eve)
    d = db_session.get(Draft, uuid.UUID(i))
    d.status, d.target = "in_review", {"visibility": "org_unit", "version": 1, "refs": [], "share_users": [],
                                       "share_groups": [], "ai_disclosure": {}}  # fmt: skip
    db_session.commit()
    r = client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "approve"})
    assert r.status_code == 422 and built == []
    # PATCH on a published draft document
    d.status, d.target = "draft", None
    db_session.commit()
    assert sub(client, nogroup, i, "private").status_code == 200
    r = client.patch(f"/documents/{i}", headers=nogroup, json={"visibility": "org_unit"})
    assert r.status_code == 422


def test_review_cannot_be_skipped_by_profile_choice(client, as_ana, ana, db_session, built):
    spec = profiles.Profile(
        name="orgrev", title="R", review_required=True, doc_types=["journal-article"],
        org_units=["/Mathematics"],
    ).model_dump()  # fmt: skip
    db_session.add(WritingProfile(name="orgrev", version=1, yaml="", spec=spec))
    db_session.commit()
    i = mk(
        client,
        as_ana,
        content=MD.replace("title: T", "title: T\ndoc_type: journal-article"),
        profile="journal-article",
    )
    reports(db_session, i, ana)
    assert sub(client, as_ana, i).json()["status"] == "in_review" and built == []


def test_unsupported_citation_blocks(client, as_ana, ana, db_session, built):
    i = mk(client, as_ana)
    reports(db_session, i, ana)
    v = db_session.scalars(select(DraftVersion)).one()
    v.citations = {**v.citations, "ok": False, "items": [{"key": "doc:x", "verdict": "unsupported"}]}
    db_session.commit()
    assert code(sub(client, as_ana, i)) == "citations_unresolved"


def test_visibility_union_and_current_coauthors_at_approval(
    client, as_ana, ana, as_bob, bob, cara, admin, db_session, built
):
    db_session.add(
        WritingProfile(
            name="rev",
            version=1,
            yaml="",
            spec=profiles.Profile(name="rev", title="R", review_required=True).model_dump(),
        )
    )
    db_session.commit()
    client.get("/me", headers=admin)
    i = mk(client, as_ana, profile="rev")
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    reports(db_session, i, ana)
    carau = client.get("/me", headers=cara).json()["id"]
    assert sub(client, as_ana, i, "private", share_users=[carau]).status_code == 200
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": []})  # change before approval
    client.post(f"/admin/drafts/{i}/review", headers=admin, json={"decision": "approve", "note": "ok"})
    doc = db_session.get(Document, uuid.UUID(i))
    db_session.refresh(doc)
    assert doc.visibility == "shared"
    assert {s.principal for s in db_session.scalars(select(DocumentShare))} == {carau}
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "draft.review")).one()
    assert row.detail == {"decision": "approve", "note_len": 2, "visibility": "private"}
