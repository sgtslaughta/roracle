"""BibTeX export (P8 task 4)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from roracle import bibtex
from roracle.auth import Principal, current_principal
from roracle.db import get_session

router = APIRouter()


@router.get("/export/bibtex")
def export_bibtex(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    doc_id: Annotated[list[UUID] | None, Query(max_length=bibtex.MAX_IDS)] = None,
    collection: Annotated[str | None, Query(max_length=100)] = None,
) -> Response:
    if not doc_id and not collection:
        raise HTTPException(422, "doc_id or collection required")
    return Response(bibtex.export(session, p, doc_id or [], collection), media_type="text/x-bibtex")
