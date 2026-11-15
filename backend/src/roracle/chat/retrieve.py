"""Reader-aware retrieval for chat: tabs, selection, pins and excludes shape what the model sees.

R19: Postgres is the authority. Every passage (and the selection) must belong to a document in
`visible_documents(p)` whose latest `searchable` version it comes from; tab/pin ids never bypass that.
"""

from uuid import UUID

from elasticsearch import NotFoundError
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.api.routes.search import _own_collections
from roracle.auth import Principal
from roracle.config import Settings
from roracle.models import Document, DocumentVersion
from roracle.retrieval import Passage, search_chunks

MAX_CHARS = 1200
MIN_K, MAX_K = 4, 12
TAB_BOOST, PAGE_BOOST = 1.5, 2.0
SELECTION_SCORE = 1e9


class Tab(BaseModel):
    doc_id: str
    version_id: str | None = None
    page: int = 1


class Selection(BaseModel):
    doc_id: str
    page: int
    text: str = Field(max_length=4000)


class Viewer(BaseModel):
    tabs: list[Tab] = Field(default_factory=list, max_length=3)
    ignored: list[str] = Field(default_factory=list)
    selection: Selection | None = None


def _live(session: Session, p: Principal, doc_ids: set[str]) -> dict[str, DocumentVersion]:
    """doc id -> latest searchable version, for the given ids the caller may see (one query)."""
    uuids = []
    for d in doc_ids:
        try:
            uuids.append(UUID(d))
        except ValueError:
            pass
    if not uuids:
        return {}
    rows = session.execute(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            visible_documents(p),
            Document.id.in_(uuids),
            and_(DocumentVersion.is_latest.is_(True), DocumentVersion.stage == "searchable"),
        )
    ).scalars()
    return {str(v.document_id): v for v in rows}


def _search(es, llm, settings: Settings, p: Principal, q: str, doc_ids: list[str] | None, size: int):
    try:
        return search_chunks(
            es,
            llm,
            settings,
            p,
            query=q,
            filters={},
            doc_ids=doc_ids,
            size=size,
            min_similarity=settings.chat_min_similarity or None,
        )
    except NotFoundError:  # nothing ingested yet: no index
        return []


def _selection(sel: Selection, live: dict[str, DocumentVersion]) -> Passage:
    v = live[sel.doc_id]
    meta = v.meta or {}
    return Passage(
        chunk_id="selection",
        doc_id=sel.doc_id,
        version_id=str(v.id),
        text=sel.text[:MAX_CHARS],
        highlight="",
        pages=[sel.page],
        bboxes=[],
        score=SELECTION_SCORE,
        title="Selected passage",
        authors=list(meta.get("authors") or []),
        year=meta.get("year"),
    )


def _pin(top: list[Passage], ranked: list[Passage], pins: set[str], k: int) -> list[Passage]:
    """Make each pinned doc's best passage part of the top k, evicting the lowest unpinned."""
    top = list(top)
    for doc in pins:
        if any(x.doc_id == doc for x in top):
            continue
        best = next((x for x in ranked if x.doc_id == doc), None)
        victim = next((i for i in range(len(top) - 1, -1, -1) if top[i].doc_id not in pins), None)
        if best is None or (len(top) >= k and victim is None):
            continue
        if len(top) >= k:
            top.pop(victim)
        top.append(best)
    return sorted(top, key=lambda x: -x.score)


def retrieve_for_chat(
    session: Session,
    es,
    llm,
    settings: Settings,
    p: Principal,
    question: str,
    viewer: Viewer,
    scope: str,
    pins: list[str],
    excludes: list[str],
    k: int | None = None,
) -> list[Passage]:
    k = max(MIN_K, min(MAX_K, k or settings.chat_passages))
    dropped = set(excludes) | set(viewer.ignored)
    tab_pages: dict[str, set[int]] = {}
    for t in viewer.tabs:
        if t.doc_id not in dropped:
            tab_pages.setdefault(t.doc_id, set()).add(t.page)
    live_tabs = _live(session, p, set(tab_pages))
    tab_ids = [d for d in tab_pages if d in live_tabs]

    if scope == "all":
        scoped: list[str] | None = None
    elif scope == "open":
        scoped = tab_ids
    elif scope.startswith("collection:"):
        _, scoped = _own_collections(session, p, [scope.partition(":")[2]])
    else:
        raise ValueError(f"unknown scope: {scope}")

    found: dict[str, Passage] = {}
    if scoped is None or scoped:
        for x in _search(es, llm, settings, p, question, scoped, 50):
            found[x.chunk_id] = x
        if tab_ids and scope != "open":
            for x in _search(es, llm, settings, p, question, tab_ids, 20):
                if x.chunk_id not in found or found[x.chunk_id].score < x.score:
                    found[x.chunk_id] = x

    sel = viewer.selection
    live = _live(session, p, {x.doc_id for x in found.values()} | ({sel.doc_id} if sel else set()))
    ranked: list[Passage] = []
    for x in found.values():
        v = live.get(x.doc_id)
        if v is None or x.doc_id in dropped or x.version_id != str(v.id):
            continue
        if x.doc_id in tab_pages:
            x.score *= PAGE_BOOST if tab_pages[x.doc_id] & set(x.pages) else TAB_BOOST
        x.text = x.text[:MAX_CHARS]
        ranked.append(x)
    ranked.sort(key=lambda x: -x.score)

    out = _pin(ranked[:k], ranked, set(pins) - dropped, k)
    if sel and sel.doc_id in live and sel.doc_id not in dropped:
        out.insert(0, _selection(sel, live))
    return out
