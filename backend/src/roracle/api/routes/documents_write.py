"""PATCH /documents/{id}: title, visibility, sharing, with immediate Elasticsearch ACL sync (P3 task 6)."""

import logging
from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from roracle import search_index
from roracle.access import normalize_path, org_unit_error
from roracle.acl_sync import durable_job as _durable_job
from roracle.acl_sync import read_acl
from roracle.api.routes.documents_read import Session_, Who, detail, privileged, visible_doc
from roracle.api.routes.library import get_es, get_session_factory
from roracle.models import Document, DocumentCoauthor, DocumentShare, DocumentVersion, User
from roracle.models_writing import Draft
from roracle.queue import enqueue

router = APIRouter()
log = logging.getLogger(__name__)
ACL_JOB_DELAY_S = 30


Path_ = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(normalize_path)]


class PatchBody(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    visibility: Literal["private", "shared", "org_unit", "everyone"] | None = None
    org_unit: Path_ | None = None  # R20: any normalized path, no membership check (same as share_groups)
    share_users: Annotated[list[UUID], Field(max_length=500)] | None = None
    share_groups: Annotated[list[Path_], Field(max_length=500)] | None = None
    coauthors: Annotated[list[UUID], Field(max_length=100)] | None = None  # removal only: subset of current
    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def _some(self):
        if not self.model_fields_set or all(getattr(self, f) is None for f in self.model_fields_set):
            raise ValueError("at least one field is required")
        return self


def _set_shares(session: Session, doc: Document, kind: str, principals: list[str]) -> None:
    session.execute(
        delete(DocumentShare).where(DocumentShare.document_id == doc.id, DocumentShare.principal_kind == kind)
    )
    session.add_all(
        DocumentShare(document_id=doc.id, principal_kind=kind, principal=x) for x in sorted(set(principals))
    )


def _remove_coauthors(session: Session, doc: Document, keep: set[UUID]) -> None:
    """Owner/admin may drop co-authors (never add: they come from the draft at publish)."""
    have = set(
        session.scalars(select(DocumentCoauthor.user_id).where(DocumentCoauthor.document_id == doc.id))
    )
    if not keep <= have:
        raise HTTPException(422, "can only remove co-authors")
    session.execute(
        delete(DocumentCoauthor).where(
            DocumentCoauthor.document_id == doc.id, DocumentCoauthor.user_id.in_(have - keep)
        )
    )
    session.execute(  # a removed co-author must not keep access via a user share either
        delete(DocumentShare).where(
            DocumentShare.document_id == doc.id,
            DocumentShare.principal_kind == "user",
            DocumentShare.principal.in_([str(u) for u in have - keep]),
        )
    )
    if d := session.get(Draft, doc.id):  # a later re-publish must not bring them back
        d.coauthors = {
            **d.coauthors,
            "users": sorted(u for u in d.coauthors.get("users", []) if UUID(u) in keep),
        }


def recover(es: Elasticsearch, sf, doc_id: UUID) -> None:
    """After a rollback/failed commit: re-sync ES from committed Postgres; if that fails too, leave a
    durable acl_sync job (fresh session) so the index converges. Never silent."""
    try:
        search_index.sync_acl(es, doc_id, lambda: read_acl(sf, doc_id))
    except Exception:
        log.exception("ES re-sync failed for document %s; queueing acl_sync", doc_id)
        try:
            _durable_job(sf, doc_id, 0)
        except Exception:
            log.exception("could not queue acl_sync for document %s", doc_id)


@router.patch("/documents/{doc_id}")
def patch_document(
    doc_id: UUID,
    body: PatchBody,
    p: Who,
    session: Session_,
    es: Annotated[Elasticsearch, Depends(get_es)],
    sf: Annotated[object, Depends(get_session_factory)],
) -> dict:
    doc = visible_doc(session, p, doc_id)
    # Serialize concurrent PATCHes per document (flush -> ES -> commit runs under this row lock) and
    # authorize against the LOCKED owner: a transfer committing between load and lock must win.
    session.refresh(doc, with_for_update=True)
    if not privileged(doc, p):
        if not session.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == doc_id, DocumentVersion.stage == "searchable"
            )
        ):
            raise HTTPException(404, "not found")  # R16
        raise HTTPException(403, "only the document owner can change this document")
    if session.scalar(
        select(Draft.document_id).where(Draft.document_id == doc_id, Draft.status.in_(("draft", "in_review")))
    ):
        raise HTTPException(409, "use the /drafts endpoints")  # after the 404/403 checks: leaks nothing
    users: list[str] | None = None
    if body.share_users is not None:
        ids = {u for u in body.share_users if u != doc.owner_id}
        found = set(session.scalars(select(User.id).where(User.id.in_(ids)))) if ids else set()
        if found != ids:
            raise HTTPException(422, "unknown user")
        users = [str(u) for u in ids]
    if body.coauthors is not None:
        _remove_coauthors(session, doc, set(body.coauthors))
    if body.title is not None:
        doc.title = body.title
    if body.visibility is not None:
        if body.visibility == "shared" and doc.visibility != "shared":  # legacy/inert rows must not revive
            session.execute(delete(DocumentShare).where(DocumentShare.document_id == doc.id))
        doc.visibility = body.visibility
    if body.org_unit is not None:
        doc.org_unit = body.org_unit
    if (body.visibility is not None or body.org_unit is not None) and (
        err := org_unit_error(doc.visibility, doc.org_unit)
    ):
        raise HTTPException(422, err)  # same fail-closed rule as upload/submit
    if users is not None:
        _set_shares(session, doc, "user", users)
    if body.share_groups is not None:
        _set_shares(session, doc, "group", body.share_groups)
    if doc.visibility != "shared":  # shares are inert unless shared; drop them so re-sharing starts clean
        session.execute(delete(DocumentShare).where(DocumentShare.document_id == doc.id))
    session.flush()
    # Ordering: ES first, then commit. The acl_sync job is enqueued in the same transaction, so even
    # a crash after commit converges. If ES or the commit fails we roll back and re-sync ES from the
    # committed state (a partial update_by_query must not leave ES out of step with Postgres).
    # Crash safety: a committed delayed job exists before ES is touched, so a crash between the ES
    # write and our commit still converges (the job re-syncs ES from whatever Postgres holds).
    _durable_job(sf, doc_id, ACL_JOB_DELAY_S)
    try:
        search_index.sync_acl(es, doc_id, lambda: (search_index.acl_fields(session, doc), body.title))
        enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
        session.commit()
    except Exception:
        log.exception("document %s update failed; rolling back", doc_id)
        session.rollback()
        recover(es, sf, doc_id)
        raise HTTPException(503, "update failed, please retry") from None
    return detail(doc_id, p, session)
