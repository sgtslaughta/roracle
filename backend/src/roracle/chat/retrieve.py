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
from roracle.notes.community import note_passages, strict_gate
from roracle.retrieval import Passage, embed_query, search_chunks

MAX_CHARS = 1200
MIN_K, MAX_K = 4, 12
TAB_BOOST, PAGE_BOOST = 1.5, 2.0
SELECTION_SCORE = 1e9


class Tab(BaseModel):
    doc_id: str
    version_id: str | None = None
    page: int = Field(default=1, ge=1)


class Selection(BaseModel):
    doc_id: str
    page: int = Field(ge=1)
    text: str = Field(max_length=4000)


class Viewer(BaseModel):
    tabs: list[Tab] = Field(default_factory=list, max_length=3)
    ignored: list[str] = Field(default_factory=list, max_length=100)
    selection: Selection | None = None


def _uid(x: str) -> str | None:
    try:
        return str(UUID(x))
    except ValueError:
        return None


def _uids(xs) -> list[str]:
    """Normalised, de-duplicated (order kept), invalid ids dropped."""
    return list(dict.fromkeys(u for u in map(_uid, xs) if u))


def _merge(main: list[Passage], tab: list[Passage]) -> dict[str, Passage]:
    """Tab search only ADDS chunks the corpus search missed, scaled to not outrank the main results."""
    found = {x.chunk_id: x for x in main}
    extra = [x for x in tab if x.chunk_id not in found]
    if main and extra:
        scale = min(x.score for x in main) / max(x.score for x in extra)
        for x in extra:
            x.score *= scale
    found.update((x.chunk_id, x) for x in extra)
    return found


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


def has_open_context(session: Session, p: Principal, viewer: Viewer) -> bool:
    """True if the caller can see at least one open tab or the selected doc (scope=open needs this)."""
    ids = {_uid(t.doc_id) for t in viewer.tabs} | (
        {_uid(viewer.selection.doc_id)} if viewer.selection else set()
    )
    return bool(_live(session, p, {i for i in ids if i}))


def _search(es, llm, settings: Settings, p: Principal, q: str, doc_ids: list[str] | None, size: int, qvec):
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
            qvec=qvec,
            min_similarity=settings.chat_min_similarity or None,
            summary_knn=False,  # summary hit is chunk 0 of a doc, not the matching passage
        )
    except NotFoundError:  # nothing ingested yet: no index
        return []


def _selection(session: Session, sel: Selection, doc_id: str, live: dict[str, DocumentVersion]) -> Passage:
    v = live[doc_id]
    meta = v.meta or {}
    doc = session.get(Document, v.document_id)
    return Passage(
        chunk_id="selection",
        doc_id=doc_id,
        version_id=str(v.id),
        text=sel.text[:MAX_CHARS],
        highlight="",
        pages=[sel.page],
        bboxes=[],
        score=SELECTION_SCORE,
        title=doc.title or meta.get("title") or v.filename,
        authors=list(meta.get("authors") or []),
        year=meta.get("year"),
    )


def _pin(top: list[Passage], ranked: list[Passage], pins: list[str], k: int) -> list[Passage]:
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
    include_notes: bool = True,
) -> list[Passage]:
    k = max(MIN_K, min(MAX_K, k or settings.chat_passages))
    dropped = set(_uids(excludes)) | set(_uids(viewer.ignored))
    tab_pages: dict[str, set[int]] = {}
    for t in viewer.tabs:
        tid = _uid(t.doc_id)
        if tid and tid not in dropped:
            tab_pages.setdefault(tid, set()).add(t.page)
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
        qvec = embed_query(llm, settings, question)  # embed once per turn
        main = _search(es, llm, settings, p, question, scoped, 50, qvec)
        tab = (
            _search(es, llm, settings, p, question, tab_ids, 20, qvec) if tab_ids and scope != "open" else []
        )
        found = _merge(main, tab)

    sel = viewer.selection
    sel_id = _uid(sel.doc_id) if sel else None
    live = _live(session, p, {x.doc_id for x in found.values()} | ({sel_id} if sel_id else set()))
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

    out = _pin(ranked[:k], ranked, [d for d in _uids(pins) if d not in dropped], k)
    if sel and sel_id in live and sel_id not in dropped:
        out.insert(0, _selection(session, sel, sel_id, live))
    return out + (note_passages(session, p, out, strict_gate(settings)) if include_notes else [])
