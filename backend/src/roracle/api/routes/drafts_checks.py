"""Draft citation verification + acknowledgements (P9 task 4). Authors only (owner/co-author)."""

from typing import Annotated
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import Principal, current_principal, get_app_settings, require_human
from roracle.config import Settings
from roracle.db import get_session
from roracle.writing import cite_check

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Human = Annotated[Principal, Depends(require_human)]  # agents must not ack their own citations
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
Hash = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


class AckIn(BaseModel):
    key: Annotated[str, Field(max_length=200)]
    sentence_hash: Annotated[str, Hash]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class UnackIn(BaseModel):
    key: Annotated[str, Field(max_length=200)]
    sentence_hash: Annotated[str, Hash]


@router.post("/drafts/{doc_id}/check-citations")
def check_citations(
    doc_id: UUID,
    p: Who,
    session: Db,
    cfg: Cfg,
    es: Annotated[Elasticsearch, Depends(get_es)],
    llm: Annotated[object, Depends(get_llm)],
) -> dict:
    return cite_check.check(session, es, llm, cfg, p, doc_id)


@router.post("/drafts/{doc_id}/citations/ack")
def ack(doc_id: UUID, body: AckIn, p: Human, session: Db) -> dict:
    return cite_check.acknowledge(session, p, doc_id, body.key, body.sentence_hash, body.reason)


@router.delete("/drafts/{doc_id}/citations/ack")
def unack(doc_id: UUID, body: UnackIn, p: Human, session: Db) -> dict:
    return cite_check.acknowledge(session, p, doc_id, body.key, body.sentence_hash, None)
