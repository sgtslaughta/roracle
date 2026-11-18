"""Votes, tags and collections (P3 task 3). Collections stay in PG only (R12)."""

import logging
import re
import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle import clients
from roracle.access import readable_documents
from roracle.acl_sync import durable_job
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Collection, CollectionItem, Document, Tag, Vote
from roracle.onboarding import mark
from roracle.search_index import MUTATE_ALIAS, run_update_by_query
from roracle.search_signals import enqueue_signals

router = APIRouter()
log = logging.getLogger(__name__)
MAX_TAGS = 50  # R14
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]


def get_es(request: Request) -> Elasticsearch:
    app = request.app
    if not hasattr(app.state, "es"):
        app.state.es = clients.es(app.state.settings)
    return app.state.es


def get_session_factory(request: Request):
    return request.app.state.session_factory


def normalize_tag(s: str) -> str:
    t = re.sub(r"[\W_]+", "-", unicodedata.normalize("NFKC", s).casefold()).strip("-")
    if not 1 <= len(t) <= 40:
        raise ValueError("tag must be 1-40 characters after normalization")
    return t


def _visible(session: Session, p: Principal, doc_id: UUID) -> Document:
    """Readable document (visible + R16), else 404."""
    doc = session.scalar(select(Document).where(Document.id == doc_id, readable_documents(p)))
    if doc is None:
        raise HTTPException(404, "not found")
    return doc


# ---- votes
class VoteBody(BaseModel):
    value: Literal[1, -1, 0]
    reason: Literal["outdated", "wrong", "low_quality", "superseded"] | None = None
    superseded_by: UUID | None = None

    @model_validator(mode="after")
    def _rules(self):
        if self.reason and self.value != -1:
            raise ValueError("reason only allowed with a downvote")
        if self.reason == "superseded" and self.superseded_by is None:
            raise ValueError("superseded requires superseded_by")
        if self.superseded_by is not None and self.reason != "superseded":
            raise ValueError("superseded_by only allowed with reason superseded")
        return self


def vote_summary(session: Session, doc_id: UUID, user_id: UUID) -> dict:
    up, down = session.execute(
        select(func.count().filter(Vote.value == 1), func.count().filter(Vote.value == -1)).where(
            Vote.document_id == doc_id
        )
    ).one()
    mine = session.scalar(select(Vote.value).where(Vote.document_id == doc_id, Vote.user_id == user_id))
    return {"up": up, "down": down, "mine": mine or 0}


@router.put("/documents/{doc_id}/vote")
def put_vote(doc_id: UUID, body: VoteBody, p: Who, session: Session_) -> dict:
    _visible(session, p, doc_id)
    if body.value == 0:
        session.execute(delete(Vote).where(Vote.document_id == doc_id, Vote.user_id == p.user_id))
    else:
        if body.superseded_by is not None:
            target = session.scalar(
                select(Document.id).where(Document.id == body.superseded_by, readable_documents(p))
            )
            if target is None or target == doc_id:
                raise HTTPException(422, "superseded_by not found")
        vals = {"value": body.value, "reason": body.reason, "superseded_by": body.superseded_by}
        session.execute(
            insert(Vote)
            .values(user_id=p.user_id, document_id=doc_id, **vals)
            .on_conflict_do_update(
                index_elements=[Vote.user_id, Vote.document_id], set_={**vals, "created_at": func.now()}
            )
        )
    enqueue_signals(session, [doc_id])
    session.commit()
    return vote_summary(session, doc_id, p.user_id)


# ---- tags
class TagBody(BaseModel):
    tag: str

    @model_validator(mode="after")
    def _norm(self):
        self.tag = normalize_tag(self.tag)
        return self


def tags_of(session: Session, doc_id: UUID) -> list[str]:
    return list(session.scalars(select(Tag.tag).where(Tag.document_id == doc_id).order_by(Tag.tag)))


def _sync_es(session: Session, es: Elasticsearch, doc_id: UUID) -> list[str]:
    tags: list[str] = []

    def build() -> dict:
        tags[:] = tags_of(session, doc_id)  # re-read on every try to shrink the stale window
        return {
            "index": MUTATE_ALIAS,
            "query": {"term": {"doc_id": str(doc_id)}},
            "script": {
                "source": "ctx._source.tags = params.tags",
                "lang": "painless",
                "params": {"tags": list(tags)},
            },
        }

    run_update_by_query(es, build)  # missing index: no-op; P2 _index reads tags from PG
    return tags


def _sync_or_queue(session: Session, es: Elasticsearch, sf, doc_id: UUID) -> list[str]:
    """Tag change is committed: push to ES; on failure leave a durable acl_sync job and answer 503."""
    try:
        return _sync_es(session, es, doc_id)
    except Exception:
        log.exception("tag sync for document %s failed; queueing acl_sync", doc_id)
        try:
            durable_job(sf, doc_id)
        except Exception:
            log.exception("could not queue acl_sync for document %s", doc_id)
        raise HTTPException(503, "saved, but search index update is pending") from None


@router.post("/documents/{doc_id}/tags")
def add_tag(
    doc_id: UUID,
    body: TagBody,
    p: Who,
    session: Session_,
    es: Annotated[Elasticsearch, Depends(get_es)],
    sf: Annotated[object, Depends(get_session_factory)],
) -> dict:
    _visible(session, p, doc_id)
    session.scalar(select(Document.id).where(Document.id == doc_id).with_for_update())  # serialize cap check
    have = tags_of(session, doc_id)
    if body.tag not in have and len(have) >= MAX_TAGS:
        raise HTTPException(422, "too many tags")
    session.execute(insert(Tag).values(document_id=doc_id, tag=body.tag).on_conflict_do_nothing())
    session.commit()
    tags = _sync_or_queue(session, es, sf, doc_id)
    return {"tag": body.tag, "tags": tags}


@router.delete("/documents/{doc_id}/tags/{tag}")
def delete_tag(
    doc_id: UUID,
    tag: str,
    p: Who,
    session: Session_,
    es: Annotated[Elasticsearch, Depends(get_es)],
    sf: Annotated[object, Depends(get_session_factory)],
) -> dict:
    doc = _visible(session, p, doc_id)
    if not (doc.owner_id == p.user_id or p.is_admin):
        raise HTTPException(403, "only the document owner can remove tags")
    try:
        norm = normalize_tag(tag)
    except ValueError:
        return {"tags": tags_of(session, doc_id)}
    session.execute(delete(Tag).where(Tag.document_id == doc_id, Tag.tag == norm))
    session.commit()
    tags = _sync_or_queue(session, es, sf, doc_id)
    return {"tags": tags}


# ---- collections
class CollectionBody(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    model_config = {"str_strip_whitespace": True}


def _count(session: Session, p: Principal, cid: UUID) -> int:
    """Items the caller can currently see (a stale member must not signal hidden documents)."""
    return (
        session.scalar(
            select(func.count())
            .select_from(CollectionItem)
            .join(Document, Document.id == CollectionItem.document_id)
            .where(CollectionItem.collection_id == cid, readable_documents(p))
        )
        or 0
    )


def _own(session: Session, p: Principal, cid: UUID) -> Collection:
    c = session.scalar(select(Collection).where(Collection.id == cid, Collection.owner_id == p.user_id))
    if c is None:
        raise HTTPException(404, "not found")
    return c


@router.get("/collections")
def list_collections(p: Who, session: Session_) -> list[dict]:
    n = func.count(Document.id)
    rows = session.execute(
        select(Collection, n)
        .outerjoin(CollectionItem, CollectionItem.collection_id == Collection.id)
        .outerjoin(Document, and_(Document.id == CollectionItem.document_id, readable_documents(p)))
        .where(Collection.owner_id == p.user_id)
        .group_by(Collection.id)
        .order_by(Collection.name, Collection.id)
    ).all()
    return [{"id": str(c.id), "name": c.name, "count": k} for c, k in rows]


@router.post("/collections", status_code=201)
def create_collection(body: CollectionBody, p: Who, session: Session_) -> dict:
    c = Collection(owner_id=p.user_id, name=body.name)
    session.add(c)
    mark(session, p.user_id, "collect")
    session.commit()
    return {"id": str(c.id), "name": c.name, "count": 0}


@router.patch("/collections/{cid}")
def rename_collection(cid: UUID, body: CollectionBody, p: Who, session: Session_) -> dict:
    c = _own(session, p, cid)
    c.name = body.name
    session.commit()
    return {"id": str(c.id), "name": c.name, "count": _count(session, p, cid)}


@router.delete("/collections/{cid}", status_code=204)
def delete_collection(cid: UUID, p: Who, session: Session_) -> Response:
    c = _own(session, p, cid)
    session.execute(delete(CollectionItem).where(CollectionItem.collection_id == cid))
    session.delete(c)
    session.commit()
    return Response(status_code=204)


@router.put("/collections/{cid}/items/{doc_id}")
def add_item(cid: UUID, doc_id: UUID, p: Who, session: Session_) -> dict:
    _own(session, p, cid)
    # lock the collection so a concurrent delete can't slip between the check and the insert
    if session.scalar(select(Collection.id).where(Collection.id == cid).with_for_update()) is None:
        raise HTTPException(404, "not found")
    _visible(session, p, doc_id)
    session.execute(
        insert(CollectionItem).values(collection_id=cid, document_id=doc_id).on_conflict_do_nothing()
    )
    session.commit()
    return {"id": str(cid), "count": _count(session, p, cid)}


@router.delete("/collections/{cid}/items/{doc_id}")
def remove_item(cid: UUID, doc_id: UUID, p: Who, session: Session_) -> dict:
    _own(session, p, cid)
    session.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == cid, CollectionItem.document_id == doc_id
        )
    )
    session.commit()
    return {"id": str(cid), "count": _count(session, p, cid)}
