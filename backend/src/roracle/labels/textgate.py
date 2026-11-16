"""Text withholding by label: what an agent (PAT) may not read of a document, and derived chat content.

Humans are unaffected, except `llm=True` (replay into an LLM), where `exclude_llm` binds everyone."""

from collections.abc import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.labels.policy import Policy
from roracle.models import Document

TEXT_KEYS = ("quote", "text", "highlight")


def withheld_text(label: str) -> str:
    return f"[withheld: {label}]"


def gated_docs(session: Session, p: Principal, doc_ids: Iterable, *, llm: bool = False) -> dict[str, str]:
    """doc id -> label name for documents whose text (or text derived from it) `p` may not receive.

    PAT: `block_agents` or `exclude_llm`. `llm=True` (LLM replay): `exclude_llm` for everyone."""
    agent = p.via == "pat"
    if not (agent or llm):
        return {}
    ids = []
    for d in doc_ids:
        try:
            ids.append(UUID(str(d)))
        except ValueError:
            pass
    if not ids:
        return {}
    policy = Policy.load(session)
    out = {}
    for did, lid in session.execute(select(Document.id, Document.label_id).where(Document.id.in_(ids))):
        lab = policy.get(lid)
        if lab.exclude_llm or (agent and lab.block_agents):
            out[str(did)] = lab.name
    return out


def doc_ids_of(items: Iterable) -> set[str]:
    return {str(x["doc_id"]) for x in items if isinstance(x, dict) and x.get("doc_id")}


def strip_text(items: list[dict], gated: dict[str, str]) -> list[dict]:
    """Items of gated docs lose their passage text (title/metadata stay) and carry `withheld`."""
    return [
        {**{k: v for k, v in x.items() if k not in TEXT_KEYS}, "withheld": gated[str(x["doc_id"])]}
        if isinstance(x, dict) and str(x.get("doc_id")) in gated
        else x
        for x in items
    ]


def require_text(session: Session, p: Principal, doc: Document) -> None:
    """403 for a PAT reading raw document bytes/layout of an `exclude_llm` document (R: agents get no text)."""
    if p.via != "pat":
        return
    lab = Policy.load(session).get(doc.label_id)
    if lab.exclude_llm:
        raise HTTPException(403, f"withheld by label: {lab.name}")


def withhold_messages(items: list[dict], gated: dict[str, str], docs_of) -> None:
    """In place: assistant `content` becomes `[withheld: <label>]` when `docs_of(item)` meets a gated doc."""
    for m in items:
        if m.get("role") != "assistant":
            continue
        hit = sorted(d for d in docs_of(m) if d in gated)
        if hit:
            m["content"] = withheld_text(gated[hit[0]])


def replay_history(session: Session, p: Principal, history: list) -> list:
    """Drop assistant turns (and the user turn before each) whose sources are now `exclude_llm`, or
    agent-blocked when the caller is a PAT: stale answers must not feed the LLM again."""
    ids = doc_ids_of(c for m in history for c in (*m.citations, *m.retrieved))
    gated = gated_docs(session, p, ids, llm=True)
    if not gated:
        return history
    drop: set[int] = set()
    for i, m in enumerate(history):
        if m.role == "assistant" and any(
            isinstance(c, dict) and str(c.get("doc_id")) in gated for c in (*m.citations, *m.retrieved)
        ):
            drop.add(i)
            if i and history[i - 1].role == "user":
                drop.add(i - 1)
    return [m for i, m in enumerate(history) if i not in drop]
