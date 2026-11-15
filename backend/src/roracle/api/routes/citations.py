"""In-library references of a document and the documents that cite it (ACL applied at read time)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.api.routes.documents_read import HIDDEN_STAGES, _doc, _privileged
from roracle.auth import Principal, current_principal
from roracle.citations import _year as _int_or_none
from roracle.db import get_session
from roracle.models import DocCitation, Document, DocumentVersion

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Session_ = Annotated[Session, Depends(get_session)]


def _readable(session: Session, p: Principal, doc_id: UUID) -> DocumentVersion:
    """Version whose references are shown: latest; non-owners only a searchable one (R16)."""
    doc = _doc(session, p, doc_id)
    versions = list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id, DocumentVersion.stage.not_in(HIDDEN_STAGES))
            .order_by(DocumentVersion.version.desc())
        )
    )
    if not _privileged(doc, p):
        versions = [v for v in versions if v.stage == "searchable"]
    if not versions:
        raise HTTPException(404, "not found")
    return next((v for v in versions if v.is_latest), versions[0])


def _format(ref: dict) -> str:
    authors = ", ".join(str(a) for a in ref.get("authors") or [])
    if ref.get("year"):
        authors = f"{authors} ({ref['year']})".strip()
    parts = [authors, str(ref.get("title") or ""), str(ref.get("doi") or "")]
    return ". ".join(x for x in parts if x)


@router.get("/documents/{doc_id}/references")
def references(doc_id: UUID, p: Who, session: Session_) -> list[dict]:
    v = _readable(session, p, doc_id)
    rows = {
        c.ref_no: c for c in session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id))
    }
    ids = {c.target_document_id for c in rows.values() if c.target_document_id}
    seen = (  # a target the caller cannot see stays unresolved
        {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(ids), visible_documents(p)))}
        if ids
        else {}
    )
    out = []
    for n, ref in enumerate(v.references or [], 1):
        ref = ref if isinstance(ref, dict) else {}
        row = rows.get(n)
        t = seen.get(row.target_document_id) if row else None
        out.append(
            {
                "ref_no": n,
                "raw": _format(ref),
                "target": {"doc_id": str(t.id), "title": t.title or ""} if t else None,
            }
        )
    return out


@router.get("/documents/{doc_id}/cited-by")
def cited_by(doc_id: UUID, p: Who, session: Session_) -> list[dict]:
    _readable(session, p, doc_id)
    rows = session.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .join(DocCitation, DocCitation.src_version_id == DocumentVersion.id)
        .where(
            DocCitation.target_document_id == doc_id,
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
            visible_documents(p),
        )
        .order_by(DocumentVersion.created_at.desc(), Document.id)
    ).all()
    out: dict[UUID, dict] = {}
    for d, v in rows:
        m = v.meta or {}
        out.setdefault(
            d.id,
            {
                "doc_id": str(d.id),
                "title": d.title or m.get("title") or v.filename,
                "authors": list(m.get("authors") or []),
                "year": _int_or_none(m.get("year")),
                **({"venue": m["venue"]} if m.get("venue") else {}),
            },
        )
    return list(out.values())
