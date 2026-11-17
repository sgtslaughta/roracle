"""Run a gold set through the production retrieval path as each real principal (ACL-aware).

Every hit is re-checked against Postgres independently of the retrieval code; a surviving hit on an
invisible document is an ACL leak and fails the run.
"""

import hashlib
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import runtime
from roracle.access import llm_allowed, visible_documents
from roracle.api.routes.search import rank_for_search
from roracle.auth import Principal
from roracle.chat import agentic, global_answer
from roracle.chat import router as query_router
from roracle.chat.retrieve import MAX_K, MIN_K, rank_for_chat
from roracle.config import Settings
from roracle.eval import judge as judge_mod
from roracle.eval import metrics as m
from roracle.eval import store
from roracle.eval.gold import GoldSet, resolve_principals
from roracle.labels.textgate import gated_docs
from roracle.models import Document
from roracle.models_eval import EvalSet
from roracle.queue import check_lease
from roracle.retrieval import Passage

Rank = Callable[[Principal, str, list[Passage]], list[Passage]]  # (principal, query, raw out-param) -> hits
JudgeFn = Callable[[Principal, str, list[Passage]], dict]  # (principal, query, chat passages) -> judge dict


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


def redact_url(v):
    """`user:pass@host` and query strings (tokens) never reach eval_runs.config or the reports."""
    if not isinstance(v, str) or "://" not in v:
        return v
    u = urlsplit(v)
    host = u.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host + (f":{u.port}" if u.port else "")
    return urlunsplit((u.scheme, netloc, u.path, "", ""))


def _global_answer(session, es, llm, settings: Settings, p: Principal, q: str, docs) -> dict:
    t0 = time.perf_counter()
    msgs, passages = global_answer.reduce_inputs(session, es, llm, settings, p, q, docs)
    text = global_answer.NO_FINDINGS.format(n=len(docs))
    if passages:
        r = llm.chat.completions.create(
            model=settings.chat_model_list[0],
            messages=msgs,
            temperature=0.2,
            max_tokens=settings.global_reduce_tokens,
        )
        text = r.choices[0].message.content or ""
    return {
        "answer": text, "docs_considered": len(docs), "docs_cited": len(passages),
        "latency_ms": round((time.perf_counter() - t0) * 1000), "passages": passages,
    }  # fmt: skip


def default_rank(
    session: Session, es, llm, settings: Settings, path: str, answers: dict | None = None, factory=None
) -> Rank:
    def chat(p: Principal, q: str, raw: list[Passage]) -> list[Passage]:
        route = query_router.classify(llm, settings, q) if settings.router else None
        if route == "global":
            docs = global_answer.candidates(session, es, llm, settings, p, q, raw=raw)
            if (
                len(docs) >= global_answer.MIN_DOCS
            ):  # the global path's candidate documents as the ranked list
                if answers is not None:  # judged run: sampled pairs also run map + reduce (M2)
                    answers[(str(p.user_id), p.via, q)] = docs
                return global_answer.ranked(docs)
            raw.clear()
        if settings.agentic_retrieval and route in (None, "multihop"):  # same condition as the chat stream
            return agentic.retrieve(
                session, es, llm, settings, p, q, depth=m.DEPTH, raw=raw, session_factory=factory
            )[0]
        return rank_for_chat(session, es, llm, settings, p, q, depth=m.DEPTH, raw=raw)

    def search(p: Principal, q: str, raw: list[Passage]) -> list[Passage]:
        hits = rank_for_search(session, es, llm, settings, p, q, raw=raw)
        if p.via != "pat":
            return hits
        gated = gated_docs(session, p, {x.doc_id for x in hits})  # /search withholds their text from PATs
        return [x for x in hits if x.doc_id not in gated]

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
    judge_fn: JudgeFn | None = None,
    parallel_sf=None,
) -> UUID:
    """One `eval_results` row per (query, principal); returns the run id. `sf` (session factory) persists the
    run in its own session so a failed run survives the caller's rollback; `rank` and `judge_fn` are test
    seams. `parallel_sf`: a session factory with pool room (the CLI/compare engines, not the worker's 3+1 pool) lets the
    agentic loop run a round's sub-queries in parallel as in production. `judge` answers and judges a deterministic sample of `eval_judge_sample` non-PAT chat pairs
    (the same pairs on every run of a set, so A/B runs compare like with like)."""
    if judge and path != "chat":
        raise ValueError("the judge needs the chat path")
    principals = resolve_principals(session, gs)  # R5 before anything is written
    settings = runtime.apply(settings, overrides or {})
    answers: dict = {}
    rank = rank or default_rank(session, es, llm, settings, path, answers if judge else None, parallel_sf)
    judge_fn = judge_fn or (lambda p, q, ps: judge_mod.judge_pair(session, llm, settings, p, q, ps))
    db_set = store.set_by_name(session, gs.name) if gs.name else None
    config = {
        "overrides": {k: redact_url(v) for k, v in (overrides or {}).items()},
        "embed_model": settings.embed_model,
        "embed_index": settings.embed_index,
        "principals": {k: p.is_admin for k, p in principals.items()},
        "queries": len(gs.queries),
        "is_reference": bool(getattr(db_set, "is_reference", False)),
        **(
            {"judge": {"model": settings.eval_judge_model, "sample": settings.eval_judge_sample}}
            if judge
            else {}
        ),
    }
    k = max(MIN_K, min(MAX_K, settings.chat_passages))  # what chat would hand the model
    with sf() if sf else nullcontext(session) as ws:
        run_id = store.start_run(
            ws, set_id=db_set.id if isinstance(db_set, EvalSet) else None, set_name=gs.name,
            trigger=trigger, label=label, path=path, config=config,
        )  # fmt: skip
        try:
            pairs = []
            for q in gs.queries:
                check_lease()
                for ref in q.principals:
                    for key in (ref, ref + ":pat") if q.as_pat else (ref,):
                        r, ranked, found = _pair(session, principals[key], key, q, path, rank)
                        pairs.append((r, ranked, found[:k], principals[key], q.text))
            judged: dict[tuple[str, str], dict] = {}
            if judge:
                eligible = [x for x in pairs if x[0].scores is not None and x[3].via != "pat"]
                eligible.sort(
                    key=lambda x: hashlib.sha1(f"{x[0].query_key}|{x[0].principal}".encode()).digest()
                )
                for r, _, top, p, text in eligible[: settings.eval_judge_sample]:
                    docs = answers.get((str(p.user_id), p.via, text))
                    g = _global_answer(session, es, llm, settings, p, text, docs) if docs else None
                    if g and g["passages"]:  # judge the reduce answer that was actually produced
                        j = judge_mod.judge_text(session, llm, settings, p, g["answer"], g["passages"])
                    else:
                        j = judge_fn(p, text, top)
                    judged[(r.query_key, r.principal)] = (
                        {**j, "global": {k: v for k, v in g.items() if k != "passages"}} if g else j
                    )
            for r, ranked, *_ in pairs:
                store.add_result(ws, run_id, r, ranked, judged.get((r.query_key, r.principal)))
            agg = m.aggregate([x[0] for x in pairs])
            if judge:
                agg["judge"] = judge_mod.summary(list(judged.values()))
        except Exception as e:
            ws.rollback()
            store.finish_run(ws, run_id, None, f"{type(e).__name__}: {e}")
            raise
        leaks = agg["acl_violations"]
        store.finish_run(ws, run_id, agg, f"acl_violations: {leaks}" if leaks else None)
    return run_id


def _pair(
    session, p: Principal, key: str, q, path: str, rank: Rank
) -> tuple[m.PairResult, list[list[str]], list[Passage]]:
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
    return res, [[x.chunk_id, x.doc_id] for x in found[: m.DEPTH]], found
