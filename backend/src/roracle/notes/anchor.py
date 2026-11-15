"""Lazy re-anchoring of notes onto a document's latest searchable version (R2)."""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from roracle.models import Annotation
from roracle.search_index import ALIAS

log = logging.getLogger(__name__)
CAP = 50


def find_quote(es, version_id: UUID, quote: str) -> tuple[int | None, dict | None] | None:
    """(page, bbox) of the first chunk of `version_id` containing `quote`; None when absent. Raises on ES errors."""
    res = es.search(
        index=ALIAS,
        query={
            "bool": {
                "filter": [{"term": {"version_id": str(version_id)}}],
                "must": [{"match_phrase": {"text": quote}}],
            }
        },
        size=1,
        _source=["pages", "bboxes"],
    )
    hits = res["hits"]["hits"]
    if not hits:
        return None
    src = hits[0].get("_source") or {}
    pages = src.get("pages") or []
    page = pages[0] if pages else None
    box = next((b for b in src.get("bboxes") or [] if b.get("page") == page), None)
    bbox = {"x0": box["l"], "y0": box["t"], "x1": box["r"], "y1": box["b"]} if box and "l" in box else None
    return page, bbox


def reanchor(session: Session, es, latest, notes: list[Annotation]) -> None:
    """Move root notes (<= CAP) not yet on `latest` (a DocumentVersion). ES failure leaves a note untouched."""
    todo = [
        n
        for n in notes
        if n.parent_id is None and n.version_id != latest.id and n.stale_version != latest.version
    ][:CAP]
    for n in todo:
        if n.anchor is None:
            n.version_id, n.stale_version = latest.id, None
            continue
        try:
            found = find_quote(es, latest.id, n.anchor["quote"])
        except Exception:
            log.warning("re-anchor lookup failed for note %s", n.id, exc_info=True)
            continue
        if found is None:
            n.stale_version = latest.version
            continue
        page, bbox = found
        a = dict(n.anchor)
        if page is not None:
            a["page"], a["bbox"] = page, bbox
        n.anchor, n.version_id, n.stale_version = a, latest.id, None
    if todo:
        session.commit()
