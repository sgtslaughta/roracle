"""POST /drafts/{id}/build: sandboxed Markdown -> PDF (P9 task 5). Owner or co-author."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from roracle.audit import audit
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session
from roracle.writing import build, drafts, profiles
from roracle.writing.markdown import parse

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]


@router.post("/drafts/{doc_id}/build")
def build_pdf(doc_id: UUID, p: Who, session: Db, cfg: Cfg) -> Response:
    doc, d = drafts.owned_or_coauthored(session, p, doc_id)
    v = drafts.latest_version(session, doc_id)
    content = v.content if v else ""
    meta = parse(content).meta
    fmt = "latex" if meta.get("format") == "latex" else "markdown"
    try:
        builder = build.get_builder(cfg, fmt)
        try:
            prof, _, _ = profiles.resolve(session, d.profile, doc.org_unit, meta.get("doc_type"))
        except KeyError:  # stored profile removed: R4 selection
            prof, _, _ = profiles.resolve(session, None, doc.org_unit, meta.get("doc_type"))
        refs = build.load_refs(session, p, content, cfg.writing_max_citations)
        audit(session, p, "draft.build", str(doc_id), {"version": v.version if v else None, "format": fmt})
        session.commit()  # release the transaction before the slow build
        pdf = builder.build(content, refs, prof)
    except build.BuildError as e:
        raise HTTPException(e.status, e.message) from None
    return Response(pdf, media_type="application/pdf")
