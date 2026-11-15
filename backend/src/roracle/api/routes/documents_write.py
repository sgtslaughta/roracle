"""PATCH /documents/{id}: title, visibility, sharing, with immediate Elasticsearch ACL sync (P3 task 6)."""

import re
from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch, NotFoundError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from roracle.api.routes.documents_read import Session_, Who, _doc, _privileged, detail
from roracle.api.routes.library import get_es
from roracle.models import Document, DocumentShare, DocumentVersion, User
from roracle.search_index import ALIAS, acl_fields

router = APIRouter()
_SCRIPT = (
    "ctx._source.owner_id = params.owner_id; ctx._source.visibility = params.visibility; "
    "ctx._source.org_unit = params.org_unit; ctx._source.share_users = params.share_users; "
    "ctx._source.share_groups = params.share_groups; "
    "if (params.title != null) { ctx._source.title = params.title; }"
)


def normalize_path(s: str) -> str:
    """R7: leading "/", no "//", no trailing "/" (except root)."""
    s = re.sub(r"/{2,}", "/", s.strip())
    if not s.startswith("/"):
        raise ValueError("path must start with '/'")
    return s.rstrip("/") or "/"


Path_ = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(normalize_path)]


class PatchBody(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    visibility: Literal["private", "shared", "org_unit", "everyone"] | None = None
    org_unit: Path_ | None = None
    share_users: list[UUID] | None = None
    share_groups: list[Path_] | None = None
    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def _some(self):
        if not self.model_fields_set or all(getattr(self, f) is None for f in self.model_fields_set):
            raise ValueError("at least one field is required")
        return self


def _sync_es(session: Session, es: Elasticsearch, doc: Document, title: str | None) -> None:
    try:
        es.update_by_query(
            index=ALIAS,
            query={"term": {"doc_id": str(doc.id)}},
            script={
                "source": _SCRIPT,
                "lang": "painless",
                "params": {**acl_fields(session, doc), "title": title},
            },
            refresh=True,
            conflicts="proceed",
        )
    except NotFoundError:  # no index yet: nothing ingested, ingest reads ACL from PG
        pass


def _set_shares(session: Session, doc: Document, kind: str, principals: list[str]) -> None:
    session.execute(
        delete(DocumentShare).where(DocumentShare.document_id == doc.id, DocumentShare.principal_kind == kind)
    )
    session.add_all(
        DocumentShare(document_id=doc.id, principal_kind=kind, principal=x) for x in sorted(set(principals))
    )


@router.patch("/documents/{doc_id}")
def patch_document(
    doc_id: UUID, body: PatchBody, p: Who, session: Session_, es: Annotated[Elasticsearch, Depends(get_es)]
) -> dict:
    doc = _doc(session, p, doc_id)
    if not _privileged(doc, p):
        if not session.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == doc_id, DocumentVersion.stage == "searchable"
            )
        ):
            raise HTTPException(404, "not found")  # R16
        raise HTTPException(403, "only the document owner can change this document")
    users: list[str] | None = None
    if body.share_users is not None:
        ids = {u for u in body.share_users if u != doc.owner_id}
        found = set(session.scalars(select(User.id).where(User.id.in_(ids)))) if ids else set()
        if found != ids:
            raise HTTPException(422, "unknown user")
        users = [str(u) for u in ids]
    if body.title is not None:
        doc.title = body.title
    if body.visibility is not None:
        doc.visibility = body.visibility
    if body.org_unit is not None:
        doc.org_unit = body.org_unit
    if users is not None:
        _set_shares(session, doc, "user", users)
    if body.share_groups is not None:
        _set_shares(session, doc, "group", body.share_groups)
    session.flush()
    # Ordering: ES first, then commit. If ES fails we roll back and best-effort re-sync ES from the
    # old committed state (a partial update_by_query must not leave ES more permissive than PG).
    # If the commit fails after ES succeeded, the same re-sync restores ES to the committed state.
    try:
        _sync_es(session, es, doc, body.title)
        session.commit()
    except Exception:
        session.rollback()
        try:
            _sync_es(session, es, doc, doc.title)
        except Exception:  # noqa: BLE001, S110 - original error is what matters
            pass
        raise
    return detail(doc_id, p, session)
