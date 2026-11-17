from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, exists, func, or_, select, true
from sqlalchemy.orm import Session

from roracle.access import agent_visible, readable_documents
from roracle.auth import Principal, current_principal
from roracle.cursor import make_cursor, parse_cursor
from roracle.db import get_session
from roracle.labels.policy import Policy, label_json
from roracle.models import Document, DocumentVersion, Job
from roracle.queue import USER_PREFIX

router = APIRouter()
TOTAL_CAP = 10_000

STAGE_LABELS = {
    "uploaded": "Uploaded",
    "scanned": "Scanned",
    "parsed": "Parsed",
    "summarized": "Parsed",
    "deduped": "Parsed",
    "chunked": "Chunked",
    "embedded": "Embedded",
    "searchable": "Searchable",
    "review": "Needs your decision",
    "needs_title": "Needs a title",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
}


def _public_error(status: str | None, last_error: str | None) -> str | None:
    """Never expose raw last_error: only NonRetryable (user:-prefixed) text passes through."""
    if last_error is None or status in ("done", "running"):
        return None
    if last_error.startswith(USER_PREFIX):
        return last_error[len(USER_PREFIX) :]
    return "Processing failed" if status == "failed" else "Processing failed \u2014 retrying"


@router.get("/me")
def me(p: Annotated[Principal, Depends(current_principal)]) -> dict:
    return {"id": str(p.user_id), "username": p.username, "groups": list(p.groups), "is_admin": p.is_admin}


@router.get("/me/uploads")
def my_uploads(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    after: Annotated[str | None, Query(max_length=100)] = None,
) -> dict:
    """Newest first. `after` (the `next` of the previous page) is keyset paging: O(page), not O(offset).
    `total` is exact up to TOTAL_CAP, then a cap (`total_capped`): an exact count is O(owned documents)."""
    cursor = None
    if after:
        try:
            cursor = parse_cursor(after)
        except ValueError:
            raise HTTPException(422, "malformed after cursor") from None
    owned = (
        Document.owner_id == p.user_id,
        Document.deleted_at.is_(None),
        *((agent_visible(),) if p.via == "pat" else ()),
    )  # gate (b)
    has_ver = exists().where(DocumentVersion.document_id == Document.id)  # drafts have none
    total = session.scalar(
        select(func.count()).select_from(
            select(Document.id).where(*owned, has_ver).limit(TOTAL_CAP + 1).subquery()
        )
    )
    # latest version per document, then its most recent job (both lateral: no N+1)
    ver = (
        select(DocumentVersion)
        .where(DocumentVersion.document_id == Document.id)
        .order_by(DocumentVersion.version.desc())
        .limit(1)
        .lateral("ver")
    )
    job = (
        select(Job.status, Job.last_error)
        .where(Job.payload["version_id"].astext == cast(ver.c.id, String))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
        .lateral("job")
    )
    rows = session.execute(
        select(
            Document.id,
            Document.title,
            Document.visibility,
            Document.label_id,
            ver.c.id.label("version_id"),
            ver.c.filename,
            ver.c.size,
            ver.c.stage,
            ver.c.page_count,
            ver.c.near_dup_of,
            ver.c.created_at,
            job.c.status,
            job.c.last_error,
        )
        .select_from(Document)
        .join(ver, true())
        .outerjoin(job, true())
        .where(*owned)
        .where(
            true()
            if cursor is None
            else or_(
                ver.c.created_at < cursor[0], and_(ver.c.created_at == cursor[0], Document.id < cursor[1])
            )
        )
        .order_by(ver.c.created_at.desc(), Document.id.desc())
        .limit(limit + 1)
        .offset(offset)
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    # near-dup match is exposed only if the caller can see it now (no existence leak)
    match_ids = [r.near_dup_of for r in rows if r.near_dup_of]
    visible = {}
    if match_ids:
        visible = {
            vid: {"document_id": str(did), "title": title}
            for vid, did, title in session.execute(
                select(DocumentVersion.id, Document.id, Document.title)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(DocumentVersion.id.in_(match_ids), readable_documents(p))
            )
        }
    policy = Policy.load(session)
    items = [
        {
            "document_id": str(r.id),
            "version_id": str(r.version_id),
            "title": r.title,
            "filename": r.filename,
            "size": r.size,
            "visibility": r.visibility,
            "label": label_json(policy.get(r.label_id)),
            "status": r.status or "queued",
            "stage": r.stage,
            "stage_label": STAGE_LABELS.get(r.stage, r.stage),
            "page_count": r.page_count,
            "near_dup_of": visible.get(r.near_dup_of),
            "error": _public_error(r.status, r.last_error),
            "uploaded_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": min(total, TOTAL_CAP),
        "total_capped": total > TOTAL_CAP,
        "next": make_cursor(rows[-1].created_at, rows[-1].id) if more else None,
    }
