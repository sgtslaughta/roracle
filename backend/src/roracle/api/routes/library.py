"""Votes, tags and collections (P3 task 3). Collections stay in PG only (R12)."""

import re
import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle import clients
from roracle.access import visible_documents
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Collection, CollectionItem, Document, Tag, Vote
from roracle.search_index import ALIAS

router = APIRouter()
MAX_TAGS = 50  # R14
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]


def get_es(request: Request) -> Elasticsearch:
    app = request.app
    if not hasattr(app.state, "es"):
        app.state.es = clients.es(app.state.settings)
    return app.state.es


def normalize_tag(s: str) -> str:
    t = re.sub(r"[\W_]+", "-", unicodedata.normalize("NFKC", s).casefold()).strip("-")
    if not 1 <= len(t) <= 40:
        raise ValueError("tag must be 1-40 characters after normalization")
    return t


def _visible(session: Session, p: Principal, doc_id: UUID) -> Document:
    doc = session.scalar(select(Document).where(Document.id == doc_id, visible_documents(p)))
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
                select(Document.id).where(Document.id == body.superseded_by, visible_documents(p))
            )
            if target is None or target == doc_id:
                raise HTTPException(422, "superseded_by not found")
        vals = {"value": body.value, "reason": body.reason, "superseded_by": body.superseded_by}
        session.execute(
            insert(Vote)
            .values(user_id=p.user_id, document_id=doc_id, **vals)
            .on_conflict_do_update(index_elements=[Vote.user_id, Vote.document_id], set_=vals)
        )
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
    tags = tags_of(session, doc_id)  # re-read right before the update to shrink the stale window
    try:
        es.update_by_query(
            index=ALIAS,
            query={"term": {"doc_id": str(doc_id)}},
            script={"source": "ctx._source.tags = params.tags", "lang": "painless", "params": {"tags": tags}},
            refresh=True,
            conflicts="proceed",
        )
    except NotFoundError:  # no index yet (nothing ingested); P2 _index reads tags from PG
        pass
    return tags


@router.post("/documents/{doc_id}/tags")
def add_tag(
    doc_id: UUID, body: TagBody, p: Who, session: Session_, es: Annotated[Elasticsearch, Depends(get_es)]
) -> dict:
    _visible(session, p, doc_id)
    have = tags_of(session, doc_id)
    if body.tag not in have and len(have) >= MAX_TAGS:
        raise HTTPException(422, "too many tags")
    session.execute(insert(Tag).values(document_id=doc_id, tag=body.tag).on_conflict_do_nothing())
    session.commit()
    tags = _sync_es(session, es, doc_id)
    return {"tag": body.tag, "tags": tags}


@router.delete("/documents/{doc_id}/tags/{tag}")
def delete_tag(
    doc_id: UUID, tag: str, p: Who, session: Session_, es: Annotated[Elasticsearch, Depends(get_es)]
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
    tags = _sync_es(session, es, doc_id)
    return {"tags": tags}


# ---- collections
class CollectionBody(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    model_config = {"str_strip_whitespace": True}


def _count(session: Session, cid: UUID) -> int:
    return session.scalar(select(func.count()).where(CollectionItem.collection_id == cid)) or 0


def _own(session: Session, p: Principal, cid: UUID) -> Collection:
    c = session.scalar(select(Collection).where(Collection.id == cid, Collection.owner_id == p.user_id))
    if c is None:
        raise HTTPException(404, "not found")
    return c


@router.get("/collections")
def list_collections(p: Who, session: Session_) -> list[dict]:
    n = func.count(CollectionItem.document_id)
    rows = session.execute(
        select(Collection, n)
        .outerjoin(CollectionItem, CollectionItem.collection_id == Collection.id)
        .where(Collection.owner_id == p.user_id)
        .group_by(Collection.id)
        .order_by(Collection.name, Collection.id)
    ).all()
    return [{"id": str(c.id), "name": c.name, "count": k} for c, k in rows]


@router.post("/collections", status_code=201)
def create_collection(body: CollectionBody, p: Who, session: Session_) -> dict:
    c = Collection(owner_id=p.user_id, name=body.name)
    session.add(c)
    session.commit()
    return {"id": str(c.id), "name": c.name, "count": 0}


@router.put("/collections/{cid}/items/{doc_id}")
def add_item(cid: UUID, doc_id: UUID, p: Who, session: Session_) -> dict:
    _own(session, p, cid)
    _visible(session, p, doc_id)
    session.execute(
        insert(CollectionItem).values(collection_id=cid, document_id=doc_id).on_conflict_do_nothing()
    )
    session.commit()
    return {"id": str(cid), "count": _count(session, cid)}


@router.delete("/collections/{cid}/items/{doc_id}")
def remove_item(cid: UUID, doc_id: UUID, p: Who, session: Session_) -> dict:
    _own(session, p, cid)
    session.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == cid, CollectionItem.document_id == doc_id
        )
    )
    session.commit()
    return {"id": str(cid), "count": _count(session, cid)}
