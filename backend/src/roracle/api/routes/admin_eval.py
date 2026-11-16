"""Admin-only eval API. Gold queries, gold doc ids, ranked results and judge text can reveal that private
documents exist, so every route needs `Admin` (JWT admin; PATs are refused)."""

import uuid
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roracle import queue
from roracle.api.routes.admin_deps import Admin
from roracle.audit import audit
from roracle.db import get_session
from roracle.eval import gold
from roracle.eval.gold import GoldItem
from roracle.models import Document, User
from roracle.models_eval import EvalQuery, EvalResult, EvalRun, EvalSet

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]
MAX_IMPORT = 2 * 1024 * 1024
Kind = Literal["fact", "multihop", "global"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetIn(_In):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=2000)
    is_reference: bool = False


class SetPatch(_In):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=2000)
    is_reference: bool | None = None


class QueryIn(_In):
    key: str | None = Field(None, min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    kind: Kind = "fact"
    principals: list[uuid.UUID] = Field(min_length=1)
    as_pat: bool = False
    relevant: list[GoldItem] = Field(min_length=1)
    reference_answer: str | None = None
    status: Literal["draft", "active"] = "active"


class QueryPatch(_In):
    text: str | None = Field(None, min_length=1, max_length=2000)
    kind: Kind | None = None
    principals: list[uuid.UUID] | None = Field(None, min_length=1)
    as_pat: bool | None = None
    relevant: list[GoldItem] | None = Field(None, min_length=1)
    reference_answer: str | None = None
    status: Literal["draft", "active"] | None = None


class GenerateIn(_In):
    n: int = Field(ge=1, le=200)
    principal: uuid.UUID


class RunIn(_In):
    set_id: uuid.UUID
    path: Literal["chat", "search"] = "chat"
    judge: bool = False


def _set_json(s: EvalSet, queries: int, drafts: int) -> dict:
    return {
        "id": str(s.id), "name": s.name, "description": s.description, "is_reference": s.is_reference,
        "created_at": s.created_at, "queries": queries, "drafts": drafts,
    }  # fmt: skip


def _query_json(q: EvalQuery, names: dict[uuid.UUID, str] | None = None) -> dict:
    return {
        "id": str(q.id), "set_id": str(q.set_id), "key": q.key, "text": q.text, "kind": q.kind,
        "principals": [str(u) for u in q.principals],
        "principal_names": [(names or {}).get(u, str(u)) for u in q.principals], "as_pat": q.as_pat, "relevant": q.relevant,
        "reference_answer": q.reference_answer, "status": q.status,
    }  # fmt: skip


def _get_set(session: Session, set_id: uuid.UUID) -> EvalSet:
    s = session.get(EvalSet, set_id)
    if s is None:
        raise HTTPException(404, "not found")
    return s


def _check_refs(session: Session, principals: list[uuid.UUID], relevant: list[GoldItem]) -> list[dict]:
    """422 unless every principal exists and every gold doc exists and is not deleted; returns JSON items."""
    known = set(session.scalars(select(User.id).where(User.id.in_(principals))))
    if missing := [str(p) for p in principals if p not in known]:
        raise HTTPException(422, f"unknown principal: {missing[0]}")
    ids = []
    for g in relevant:
        try:
            ids.append(uuid.UUID(g.doc))
        except ValueError:
            raise HTTPException(422, f"gold doc is not a document id: {g.doc}") from None
    live = set(
        session.scalars(select(Document.id).where(Document.id.in_(ids), Document.deleted_at.is_(None)))
    )
    if bad := [str(i) for i in ids if i not in live]:
        raise HTTPException(422, f"unknown or deleted document: {bad[0]}")
    return [{**g.model_dump(exclude_none=True), "doc": str(i)} for g, i in zip(relevant, ids, strict=True)]


@router.get("/admin/eval/sets")
def list_sets(p: Admin, session: Db) -> dict:
    out = []
    for s in session.scalars(select(EvalSet).order_by(EvalSet.created_at)):
        rows = session.execute(select(EvalQuery.status).where(EvalQuery.set_id == s.id)).scalars().all()
        out.append(_set_json(s, rows.count("active"), rows.count("draft")))
    return {"items": out}


@router.post("/admin/eval/sets", status_code=201)
def create_set(body: SetIn, p: Admin, session: Db) -> dict:
    if session.scalar(select(EvalSet.id).where(EvalSet.name == body.name)):
        raise HTTPException(409, "a set with that name exists")
    s = EvalSet(name=body.name, description=body.description, created_by=p.user_id)
    session.add(s)
    session.flush()
    if body.is_reference:
        _make_reference(session, s)
    audit(session, p, "eval.set.create", str(s.id), {"name": s.name})
    return _set_json(s, 0, 0)


def _make_reference(session: Session, s: EvalSet) -> None:
    session.execute(
        update(EvalSet).where(EvalSet.is_reference, EvalSet.id != s.id).values(is_reference=False)
    )
    session.flush()
    s.is_reference = True
    session.flush()


@router.patch("/admin/eval/sets/{set_id}")
def patch_set(set_id: uuid.UUID, body: SetPatch, p: Admin, session: Db) -> dict:
    s = _get_set(session, set_id)
    d = body.model_dump(exclude_none=True)
    if (
        "name" in d
        and d["name"] != s.name
        and session.scalar(select(EvalSet.id).where(EvalSet.name == d["name"]))
    ):
        raise HTTPException(409, "a set with that name exists")
    for k in ("name", "description"):
        if k in d:
            setattr(s, k, d[k])
    if d.get("is_reference") is True:
        _make_reference(session, s)
    elif d.get("is_reference") is False:
        s.is_reference = False
    session.flush()
    audit(session, p, "eval.set.update", str(s.id), {"changes": sorted(d)})
    rows = session.execute(select(EvalQuery.status).where(EvalQuery.set_id == s.id)).scalars().all()
    return _set_json(s, rows.count("active"), rows.count("draft"))


@router.delete("/admin/eval/sets/{set_id}", status_code=204)
def delete_set(set_id: uuid.UUID, p: Admin, session: Db) -> None:
    s = _get_set(session, set_id)
    audit(session, p, "eval.set.delete", str(s.id), {"name": s.name})
    session.delete(s)


@router.get("/admin/eval/sets/{set_id}/queries")
def list_queries(
    set_id: uuid.UUID, p: Admin, session: Db, status: Literal["draft", "active"] | None = None
) -> dict:
    _get_set(session, set_id)
    stmt = select(EvalQuery).where(EvalQuery.set_id == set_id).order_by(EvalQuery.created_at, EvalQuery.key)
    if status:
        stmt = stmt.where(EvalQuery.status == status)
    rows = list(session.scalars(stmt))
    ids = {u for q in rows for u in q.principals}
    names = {i: n for i, n in session.execute(select(User.id, User.username).where(User.id.in_(ids)))}
    return {"items": [_query_json(q, names) for q in rows]}


def _new_query(session: Session, set_id: uuid.UUID, b: QueryIn) -> EvalQuery:
    rel = _check_refs(session, b.principals, b.relevant)
    key = b.key or f"q-{uuid.uuid4().hex[:8]}"
    if session.scalar(select(EvalQuery.id).where(EvalQuery.set_id == set_id, EvalQuery.key == key)):
        raise HTTPException(409, f"query key exists: {key}")
    q = EvalQuery(
        set_id=set_id, key=key, text=b.text, kind=b.kind, principals=b.principals, as_pat=b.as_pat,
        relevant=rel, reference_answer=b.reference_answer, status=b.status,
    )  # fmt: skip
    session.add(q)
    session.flush()
    return q


@router.post("/admin/eval/sets/{set_id}/queries", status_code=201)
def create_query(set_id: uuid.UUID, body: QueryIn, p: Admin, session: Db) -> dict:
    _get_set(session, set_id)
    q = _new_query(session, set_id, body)
    audit(session, p, "eval.query.create", str(q.id), {"set": str(set_id), "key": q.key})
    return _query_json(q)


@router.patch("/admin/eval/queries/{query_id}")
def patch_query(query_id: uuid.UUID, body: QueryPatch, p: Admin, session: Db) -> dict:
    q = session.get(EvalQuery, query_id)
    if q is None:
        raise HTTPException(404, "not found")
    d = body.model_dump(exclude_unset=True)
    if "principals" in d or "relevant" in d:
        rel = _check_refs(
            session, body.principals or q.principals, body.relevant or [GoldItem(**i) for i in q.relevant]
        )
        if "relevant" in d:
            d["relevant"] = rel
    for k, v in d.items():
        setattr(q, k, v)
    session.flush()
    audit(session, p, "eval.query.update", str(q.id), {"changes": sorted(d)})
    return _query_json(q)


@router.delete("/admin/eval/queries/{query_id}", status_code=204)
def delete_query(query_id: uuid.UUID, p: Admin, session: Db) -> None:
    q = session.get(EvalQuery, query_id)
    if q is None:
        raise HTTPException(404, "not found")
    audit(session, p, "eval.query.delete", str(q.id), {"set": str(q.set_id), "key": q.key})
    session.delete(q)


@router.post("/admin/eval/sets/{set_id}/import")
async def import_queries(set_id: uuid.UUID, request: Request, p: Admin, session: Db) -> dict:
    _get_set(session, set_id)
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_IMPORT:
            raise HTTPException(413, "import larger than 2 MiB")
    try:
        data = yaml.safe_load(raw.decode())  # JSON is YAML
        items = gold.GoldSet.model_validate({"queries": data["queries"]}).queries
    except (yaml.YAMLError, UnicodeDecodeError, KeyError, TypeError, ValidationError) as e:
        raise HTTPException(422, f"invalid gold file: {type(e).__name__}") from e
    keys = [i.key for i in items]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "duplicate query keys in file")
    clash = session.scalars(
        select(EvalQuery.key).where(EvalQuery.set_id == set_id, EvalQuery.key.in_(keys))
    ).all()
    if clash:
        raise HTTPException(409, f"query key exists: {clash[0]}")
    for i in items:
        try:
            principals = [uuid.UUID(x) for x in i.principals]
        except ValueError:
            raise HTTPException(422, f"principal is not a user id: {i.key}") from None
        _new_query(session, set_id, QueryIn(
            key=i.key, text=i.text, kind=i.kind, principals=principals, as_pat=i.as_pat,
            relevant=i.relevant, reference_answer=i.reference_answer,
        ))  # fmt: skip
    audit(session, p, "eval.import", str(set_id), {"created": len(items)})
    return {"created": len(items)}


@router.post("/admin/eval/sets/{set_id}/generate", status_code=202)
def generate(set_id: uuid.UUID, body: GenerateIn, p: Admin, session: Db) -> dict:
    _get_set(session, set_id)
    if session.get(User, body.principal) is None:
        raise HTTPException(422, f"unknown principal: {body.principal}")
    job = queue.enqueue(
        session, "eval_generate", {"set_id": str(set_id), "principal": str(body.principal), "n": body.n}
    )
    audit(session, p, "eval.generate", str(set_id), {"n": body.n, "principal": str(body.principal)})
    return {"job_id": job}


@router.post("/admin/eval/runs", status_code=202)
def start_run(body: RunIn, p: Admin, session: Db) -> dict:
    _get_set(session, body.set_id)
    if body.judge and body.path != "chat":
        raise HTTPException(422, "judge needs the chat path")
    try:  # same input checks as the CLI: R5 and unknown principals are refused up front
        gs = gold.load_db(session, body.set_id)
        if not gs.queries:
            raise ValueError("the set has no active queries")
        gold.resolve_principals(session, gs)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    job = queue.enqueue(
        session, "eval_run",
        {"set_id": str(body.set_id), "trigger": "admin", "path": body.path, "judge": body.judge},
    )  # fmt: skip
    audit(session, p, "eval.run", str(body.set_id), {"path": body.path, "judge": body.judge})
    return {"job_id": job}


@router.get("/admin/eval/runs")
def list_runs(
    p: Admin, session: Db, set_id: uuid.UUID | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> dict:
    stmt = select(EvalRun).order_by(EvalRun.started_at.desc()).limit(limit)
    if set_id:
        stmt = stmt.where(EvalRun.set_id == set_id)
    runs = list(session.scalars(stmt))[::-1]  # newest `limit`, oldest first for the trend chart
    return {
        "items": [
            {
                "id": str(r.id), "set_id": str(r.set_id) if r.set_id else None, "set_name": r.set_name,
                "started_at": r.started_at, "trigger": r.trigger, "label": r.label, "path": r.path,
                "status": r.status, "metrics": {"overall": r.metrics["overall"]} if r.metrics else None,
            }
            for r in runs
        ]
    }  # fmt: skip


@router.get("/admin/eval/runs/{run_id}")
def get_run(run_id: uuid.UUID, p: Admin, session: Db) -> dict:
    r = session.get(EvalRun, run_id)
    if r is None:
        raise HTTPException(404, "not found")
    m = r.metrics or {}
    texts = {}
    if r.set_id:
        texts = {
            q.key: q.text for q in session.scalars(select(EvalQuery).where(EvalQuery.set_id == r.set_id))
        }
    rows = session.scalars(
        select(EvalResult)
        .where(EvalResult.run_id == r.id, EvalResult.metrics.is_not(None))
        .order_by(EvalResult.metrics["p_recall@10"].as_float(), EvalResult.query_key)
        .limit(20)
    ).all()
    by_principal = m.get("by_principal", {})
    uids = {k.split(":")[0] for k in [*by_principal, *(x.principal for x in rows)]}
    names = {
        str(u.id): u.username
        for u in session.scalars(select(User).where(User.id.in_([uuid.UUID(i) for i in uids])))
    }

    def who(key: str) -> str:
        uid, _, pat = key.partition(":")
        return names.get(uid, uid) + (":pat" if pat else "")

    tops = {x.query_key + x.principal: list(dict.fromkeys(d for _, d in x.ranked))[:5] for x in rows}
    ids = {uuid.UUID(d) for t in tops.values() for d in t}
    docs = {
        str(i): t for i, t in session.execute(select(Document.id, Document.title).where(Document.id.in_(ids)))
    }
    worst = [
        {
            "query_key": x.query_key, "text": texts.get(x.query_key, ""), "kind": x.kind,
            "principal": who(x.principal), "p_recall@10": x.metrics["p_recall@10"],
            "top_docs": [docs.get(d, d) for d in tops[x.query_key + x.principal]],
        }
        for x in rows
    ]  # fmt: skip
    return {
        "id": str(r.id), "set_id": str(r.set_id) if r.set_id else None, "set_name": r.set_name,
        "started_at": r.started_at, "finished_at": r.finished_at, "trigger": r.trigger, "label": r.label,
        "path": r.path, "status": r.status, "error": r.error, "metrics": m,
        "by_kind": m.get("by_kind", {}), "by_principal": {who(k): v for k, v in by_principal.items()},
        "worst": worst,
    }  # fmt: skip
