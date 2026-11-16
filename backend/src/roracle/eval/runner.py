"""Run a gold set through the production retrieval path as each real principal (ACL-aware).

Every hit is re-checked against Postgres independently of the retrieval code; a surviving hit on an
invisible document is an ACL leak and fails the run.
"""

import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import runtime
from roracle.access import llm_allowed, visible_documents
from roracle.api.routes.search import rank_for_search
from roracle.auth import Principal
from roracle.chat.retrieve import rank_for_chat
from roracle.config import Settings
from roracle.eval import metrics as m
from roracle.eval import store
from roracle.eval.gold import GoldSet, resolve_principals
from roracle.models import Document
from roracle.models_eval import EvalSet
from roracle.retrieval import Passage

Rank = Callable[[Principal, str, list[Passage]], list[Passage]]  # (principal, query, raw out-param) -> hits


def visible_ids(session: Session, p: Principal, doc_ids: set[str], path: str) -> set[str]:
    """Subset of `doc_ids` the principal may see on `path` (chat also needs gate (a)); one SQL query."""
    uuids = []
    for d in doc_ids:
        try:
            uuids.append(UUID(d))
        except ValueError:
            pass
    if not uuids:
        return set()
    cond = [Document.id.in_(uuids), visible_documents(p)]
    if path == "chat":
        cond.append(llm_allowed())
    return {str(i) for i in session.scalars(select(Document.id).where(*cond))}


def default_rank(session: Session, es, llm, settings: Settings, path: str) -> Rank:
    def chat(p: Principal, q: str, raw: list[Passage]) -> list[Passage]:
        return rank_for_chat(session, es, llm, settings, p, q, depth=m.DEPTH, raw=raw)

    def search(p: Principal, q: str, raw: list[Passage]) -> list[Passage]:
        return rank_for_search(session, es, llm, settings, p, q, raw=raw)

    return chat if path == "chat" else search


def run_set(
    session: Session,
    es,
    llm,
    settings: Settings,
    gs: GoldSet,
    *,
    path: Literal["chat", "search"] = "chat",
    trigger: str,
    label: str = "",
    judge: bool = False,
    overrides: dict | None = None,
    sf=None,
    rank: Rank | None = None,
) -> UUID:
    """One `eval_results` row per (query, principal); returns the run id. `sf` (session factory) persists the
    run in its own session so a failed run survives the caller's rollback; `rank` is a test seam."""
    if judge:
        raise NotImplementedError("LLM judge arrives with eval judge (task 2)")
    principals = resolve_principals(session, gs)  # R5 before anything is written
    settings = runtime.apply(settings, overrides or {})
    rank = rank or default_rank(session, es, llm, settings, path)
    db_set = store.set_by_name(session, gs.name) if gs.name else None
    config = {
        "overrides": overrides or {},
        "embed_model": settings.embed_model,
        "embed_index": settings.embed_index,
    }
    with sf() if sf else nullcontext(session) as ws:
        run_id = store.start_run(
            ws, set_id=db_set.id if isinstance(db_set, EvalSet) else None, set_name=gs.name,
            trigger=trigger, label=label, path=path, config=config,
        )  # fmt: skip
        try:
            rows = []
            for q in gs.queries:
                for ref in q.principals:
                    for key in (ref, ref + ":pat") if q.as_pat else (ref,):
                        r, ranked = _pair(session, principals[key], key, q, path, rank)
                        store.add_result(ws, run_id, r, ranked)
                        rows.append(r)
            agg = m.aggregate(rows)
        except Exception as e:
            ws.rollback()
            store.finish_run(ws, run_id, None, f"{type(e).__name__}: {e}")
            raise
        leaks = agg["acl_violations"]
        store.finish_run(ws, run_id, agg, f"acl_violations: {leaks}" if leaks else None)
    return run_id


def _pair(session, p: Principal, key: str, q, path: str, rank: Rank) -> tuple[m.PairResult, list[list[str]]]:
    visible = visible_ids(session, p, {g.doc for g in q.relevant}, path)
    expected = [g for g in q.relevant if g.doc in visible]
    raw: list[Passage] = []
    t0 = time.perf_counter()
    found = rank(p, q.text, raw)
    ms = round((time.perf_counter() - t0) * 1000)
    hits = [m.Hit(x.doc_id, x.text, x.pages) for x in found]
    ok = visible_ids(session, p, {x.doc_id for x in found} | {x.doc_id for x in raw}, path)
    res = m.PairResult(
        q.key, key, q.kind, m.score_pair(hits, expected) if expected else None,
        sum(x.doc_id not in ok for x in found), sum(x.doc_id not in ok for x in raw), ms,
    )  # fmt: skip
    return res, [[x.chunk_id, x.doc_id] for x in found[: m.DEPTH]]
