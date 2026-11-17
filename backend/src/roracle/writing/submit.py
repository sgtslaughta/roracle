"""Submit a draft: gate on the owner's reports, build the PDF, add it as the next document version.

Nothing changes before the build succeeded: a BuildError leaves the draft untouched."""

import io
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from roracle import notify
from roracle.access import org_unit_error, org_units
from roracle.audit import audit
from roracle.auth import Principal
from roracle.config import Settings
from roracle.ingest import intake
from roracle.labels import service
from roracle.labels.policy import Policy
from roracle.models import Document, DocumentCoauthor, DocumentShare, User
from roracle.models_writing import Draft, DraftVersion
from roracle.queue import enqueue
from roracle.storage import BlobStore, stage
from roracle.writing import build, checks, drafts, profiles
from roracle.writing.ast_sanitize import Ref
from roracle.writing.markdown import Parsed, parse


def _409(code: str) -> HTTPException:
    return HTTPException(409, {"code": code})


def _org_gate(doc: Document, visibility: str) -> None:
    if err := org_unit_error(visibility, doc.org_unit):
        raise HTTPException(422, err)


def _needs_review(session: Session, p: Principal, doc: Document, prof: profiles.Profile) -> bool:
    """The chosen profile, or any review-requiring profile scoped to the draft's or the submitter's org
    units (token groups: not owner-editable), whatever the doc_type: no way to opt out."""
    return prof.review_required or profiles.org_requires_review(
        session, [doc.org_unit, *org_units(list(p.groups))]
    )


def disclosure(parsed: Parsed, prof: profiles.Profile) -> dict:
    """The validated `ai_assistance` block (409 ai_disclosure_required)."""
    if checks.check_ai_disclosure(parsed, prof):
        raise _409("ai_disclosure_required")
    a = parsed.meta["ai_assistance"]
    tools = [str(t)[:100] for t in a.get("tools") or [] if isinstance(t, str)][:20]
    return {"used": a["used"], "tools": tools, "description": str(a.get("description") or "")[:2000]}


def _gates(p: Principal, v: DraftVersion) -> None:
    """Reports of the latest version computed by the submitter (reports are per principal)."""
    for rep in (v.validation, v.citations):
        if not rep or rep.get("version") != v.version or rep.get("checked_by") != str(p.user_id):
            raise _409("checks_required")
    if not v.validation["ok"]:
        raise _409("blocking_findings")
    if not v.citations["ok"]:
        raise _409("citations_unresolved")


def _slug(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60] or "paper"


def submit(
    session: Session,
    store: BlobStore,
    settings: Settings,
    p: Principal,
    doc_id: UUID,
    target: dict,
) -> dict:
    """`target` = {visibility, share_users: [UUID], share_groups: [str]} (owner only)."""
    doc, d = drafts.owned_draft(session, p, doc_id, lock=True)
    if d.status == "in_review":
        raise _409("in_review")
    v = drafts.latest_version(session, doc_id)
    if v is None or d.submitted_version == v.version:
        raise _409("nothing_to_submit")
    _gates(p, v)
    parsed = parse(v.content)
    prof = drafts.profile_for(session, doc, d, parsed.meta)[0]
    disc = disclosure(parsed, prof)
    if target.get("label_id"):
        service.resolve(Policy.load(session), target["label_id"])  # 422 on unknown
    _org_gate(doc, target["visibility"])
    ids = {u for u in target["share_users"] if u != doc.owner_id}
    if ids and set(session.scalars(select(User.id).where(User.id.in_(ids)))) != ids:
        raise HTTPException(422, "unknown user")
    frozen = {
        "visibility": target["visibility"],
        "share_users": sorted(str(u) for u in ids),
        "share_groups": sorted(set(target["share_groups"])),
        "version": v.version,
        "ai_disclosure": disc,
        "label_id": str(target["label_id"]) if target.get("label_id") else None,
        # the owner's view of the library now: an approving admin must not widen it
        "refs": [
            asdict(r)
            for r in build.load_refs(session, p, v.content, settings.writing_max_citations, public_only=True)
        ],
    }
    if _needs_review(session, p, doc, prof):
        d.target, d.status = frozen, "in_review"
        d.updated_at = datetime.now(UTC)
        audit(session, p, "draft.submit", str(doc_id), {"version": v.version, "review": True})
        notify.emit_admins(session, "draft.review_requested", {"draft_id": str(doc_id), "title": doc.title})
        return {"status": "in_review", "version": v.version}
    n = publish(session, store, settings, p, doc, d, v.content, frozen)
    audit(session, p, "draft.submit", str(doc_id), {"version": v.version, "document_version": n})
    return {"status": "submitted", "version": v.version, "document_version": n}


def publish(
    session: Session, store: BlobStore, settings: Settings, p: Principal, doc: Document, d: Draft,
    content: str, target: dict,
) -> int:  # fmt: skip
    """Build, add the PDF as the next document version, apply `target` ∪ co-authors. Returns the version."""
    meta = parse(content).meta
    prof = drafts.profile_for(session, doc, d, meta)[0]
    _org_gate(doc, target["visibility"])
    try:
        builder = build.get_builder(settings, "latex" if meta.get("format") == "latex" else "markdown")
        pdf = builder.build(content, [Ref(**r) for r in target["refs"]], prof)
    except build.BuildError as e:
        raise HTTPException(e.status, e.message) from None
    staged = stage(io.BytesIO(pdf), len(pdf) + 1)  # non-empty: the builder checked %PDF-
    try:
        ver = intake.add_version(
            session, store, staged, doc, filename=f"{_slug(doc.title)}.pdf", content_type="application/pdf"
        )
    finally:
        Path(staged.path).unlink(missing_ok=True)
    co = d.coauthors or {}
    users = sorted(set(target["share_users"]) | set(co.get("users", [])))
    groups = sorted(set(target["share_groups"]) | set(co.get("groups", [])))
    vis = target["visibility"]
    if vis in ("private", "shared"):
        vis = "shared" if users or groups else "private"
    doc.visibility = vis
    session.execute(delete(DocumentShare).where(DocumentShare.document_id == doc.id))
    # co-authors (named users only; groups never) keep read access to the paper whatever its visibility
    session.execute(delete(DocumentCoauthor).where(DocumentCoauthor.document_id == doc.id))
    session.add_all(
        DocumentCoauthor(document_id=doc.id, user_id=UUID(u))
        for u in sorted(set(co.get("users", [])) - {str(doc.owner_id)})
    )
    if (
        vis == "shared"
    ):  # shares are inert otherwise; never store them (a later PATCH->shared must not revive)
        session.add_all(DocumentShare(document_id=doc.id, principal_kind="user", principal=u) for u in users)
        session.add_all(
            DocumentShare(document_id=doc.id, principal_kind="group", principal=g) for g in groups
        )
    _apply_label(session, p, doc, prof, target.get("label_id"))
    d.status, d.submitted_version = "submitted", target["version"]
    d.target, d.ai_disclosure = target, target["ai_disclosure"]
    d.updated_at = datetime.now(UTC)
    enqueue(session, "acl_sync", {"doc_id": str(doc.id)})
    audit(session, p, "draft.publish", str(doc.id), {"version": target["version"], "visibility": vis})
    session.flush()
    return ver.version


def _apply_label(session: Session, p: Principal, doc: Document, prof: profiles.Profile, requested) -> None:
    """R3: label = max(current, requested, profile default); submit never lowers."""
    policy = Policy.load(session)
    prof_default = next(
        (x.id for x in policy.labels if prof.default_label and x.name.lower() == prof.default_label.lower()),
        None,
    )
    for cand in (requested, prof_default):
        if cand and str(cand) in {x.id for x in policy.labels}:
            service.raise_document_label(session, p, doc, cand, via="submit")


def review(
    session: Session,
    store: BlobStore,
    settings: Settings,
    p: Principal,
    doc_id: UUID,
    decision: str,
    note: str | None,
) -> dict:
    """Admin decision on an in_review draft; approval rebuilds from the frozen submitted version."""
    doc, d = drafts.readable_draft(session, p, doc_id, lock=True)
    if d.status != "in_review" or not d.target:
        raise _409("not_in_review")
    title = {"draft_id": str(doc_id), "title": doc.title}
    vis = (d.target or {}).get("visibility")
    if decision == "approve":
        v = session.scalar(
            select(DraftVersion).where(
                DraftVersion.document_id == doc_id, DraftVersion.version == d.target["version"]
            )
        )
        publish(session, store, settings, p, doc, d, v.content, d.target)
        d.review_note = note
        notify.emit(session, doc.owner_id, "draft.approved", {**title, "note": note})
    else:
        d.status = "submitted" if d.submitted_version else "draft"  # an earlier publication stays published
        d.target = None
        d.review_note = note
        d.updated_at = datetime.now(UTC)
        notify.emit(session, doc.owner_id, "draft.rejected", {**title, "note": note})
    audit(
        session,
        p,
        "draft.review",
        str(doc_id),
        {
            "decision": decision,
            "note_len": len(note or ""),
            "visibility": vis,
        },
    )
    session.flush()
    return {"status": d.status}
