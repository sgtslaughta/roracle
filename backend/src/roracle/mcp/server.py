"""MCP server: read tools. Each tool = own session, `audit("mcp.<tool>")`, commit; errors -> ToolError."""

import logging
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from elasticsearch import NotFoundError
from fastapi import FastAPI, HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import readable_documents
from roracle.api.routes import documents_read, library
from roracle.api.routes import search as search_routes
from roracle.audit import audit
from roracle.auth import Principal, get_app_settings
from roracle.chat.access import visible_versions
from roracle.labels.policy import Policy, brief_labels, label_json
from roracle.labels.textgate import gated_docs, withheld_text
from roracle.mcp import tools_notes, tools_writing
from roracle.mcp.auth import current, dep, open_session
from roracle.mcp.tools_write import digest, register
from roracle.models import CollectionItem, Document
from roracle.notes.community import attach_notes, strict_gate
from roracle.search_acl import acl_filter
from roracle.search_index import ALIAS
from roracle.search_limits import Q_MAX, VALUE_MAX, BadInput
from roracle.search_params import SearchParams

log = logging.getLogger(__name__)
MAX_IDS = 50
_SOURCE = ["chunk_id", "doc_id", "version_id", "text", "pages", "bboxes", "title"]


def _msg(detail: Any) -> str:
    """Agent-facing error text: no Python reprs for structured HTTPException details."""
    if isinstance(detail, dict):
        extra = ", ".join(f"{k}={v}" for k, v in detail.items() if k != "code")
        return f"{detail.get('code', 'error')}" + (f" ({extra})" if extra else "")
    if isinstance(detail, list):
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail)


def _run(app: FastAPI, tool: str, fn: Callable[[Session, Principal], Any], target: str = "") -> Any:
    p = current()
    with open_session(app) as session:
        try:
            out = brief_labels(fn(session, p))
            audit(session, p, f"mcp.{tool}", target[:200])
            session.commit()
            return out
        except (HTTPException, BadInput) as e:
            session.rollback()
            audit(session, p, f"mcp.{tool}", target[:200], {"error": True})
            session.commit()
            raise ToolError(_msg(e.detail) if isinstance(e, HTTPException) else str(e)) from None
        except Exception:
            session.rollback()
            try:
                audit(session, p, f"mcp.{tool}", target[:200], {"error": "internal"})
                session.commit()
            except Exception:
                log.exception("mcp.%s: could not audit internal error", tool)
            raise


def _split(chunk_id: str) -> tuple[str, int]:
    try:
        ver, n = chunk_id.rsplit(":", 1)
        return str(UUID(ver)), int(n)
    except ValueError:
        raise BadInput(f"bad chunk id: {chunk_id[:80]}") from None


def _passages(app: FastAPI, session: Session, p: Principal, ids: list[str], context: int) -> list[dict]:
    if not 0 < len(ids) <= MAX_IDS:
        raise BadInput(f"chunk_ids must hold 1..{MAX_IDS} ids")
    if not 0 <= context <= 2:
        raise BadInput("context must be 0..2")
    wanted = set()
    for cid in ids:
        if len(cid) > 100:
            raise BadInput("chunk id too long")
        ver, n = _split(cid)
        wanted.update(f"{ver}:{k}" for k in range(max(0, n - context), n + context + 1))
    es = dep(app, library.get_es)
    try:
        hits = es.search(
            index=ALIAS,
            size=len(wanted),
            source=_SOURCE,
            query={
                "bool": {
                    "filter": [acl_filter(p, Policy.load(session)), {"terms": {"chunk_id": sorted(wanted)}}]
                }
            },
        )["hits"]["hits"]
    except NotFoundError:
        return []
    srcs = [h["_source"] for h in hits if h["_source"]["chunk_id"] in wanted]
    ok = visible_versions(session, p, srcs)  # R19: Postgres is the authority
    srcs = [s for s in srcs if (s["doc_id"], s["version_id"]) in ok]
    srcs.sort(key=lambda s: (s["version_id"], _split(s["chunk_id"])[1]))
    gated = gated_docs(session, p, {s["doc_id"] for s in srcs})  # exclude_llm: no text for agents
    policy = Policy.load(session)
    labels = dict(
        session.execute(
            select(Document.id, Document.label_id).where(Document.id.in_({UUID(s["doc_id"]) for s in srcs}))
        ).all()
    )
    return [
        {
            "chunk_id": s["chunk_id"],
            "doc_id": s["doc_id"],
            "version_id": s["version_id"],
            "title": s.get("title"),
            "label": label_json(policy.get(labels[UUID(s["doc_id"])])),
            "pages": s.get("pages") or [],
            "bboxes": s.get("bboxes") or [],
            "text": withheld_text(gated[s["doc_id"]]) if s["doc_id"] in gated else s["text"],
        }
        for s in srcs
    ]


def _collections(session: Session, p: Principal) -> list[dict]:
    out = library.list_collections(p, session)
    policy = Policy.load(session)
    top: dict[str, str] = {}
    rows = session.execute(
        select(CollectionItem.collection_id, Document.label_id)
        .join(Document, Document.id == CollectionItem.document_id)
        .where(CollectionItem.collection_id.in_([UUID(c["id"]) for c in out]), readable_documents(p))
    )
    for cid, lid in rows:
        if str(cid) not in top or policy.get(lid).rank > policy.get(top[str(cid)]).rank:
            top[str(cid)] = str(lid)
    return [{**c, "max_label": label_json(policy.get(top[c["id"]])) if c["id"] in top else None} for c in out]


def _slim(hit: dict) -> dict:
    return {
        **{k: hit[k] for k in ("doc_id", "title", "authors", "year", "score", "label")},
        "passages": [
            {"chunk_id": x["chunk_id"], "text": x["text"], "page": x["page"]} for x in hit["passages"]
        ],
        "notes": hit.get("notes", []),
        **({"withheld": hit["withheld"]} if hit.get("withheld") else {}),
    }


def build_mcp(app: FastAPI) -> MCPServer:
    mcp = MCPServer("rOracle")

    @mcp.tool()
    def search(
        query: Annotated[str, Field(max_length=Q_MAX)],
        collection: Annotated[str | None, Field(max_length=VALUE_MAX)] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> dict:
        """Search the research library (hybrid keyword + semantic). Returns documents the caller may see,
        each with matching passages and chunk_ids usable with get_passages. `collection` is an id from
        list_collections; `limit` is 1..50 documents."""

        def go(session: Session, p: Principal) -> dict:
            cols = [collection] if collection else None
            try:
                f = SearchParams(q=query, collection=cols or [], size=limit, notes=False)
            except ValidationError as e:
                raise BadInput(e.errors()[0]["msg"]) from None
            settings = dep(app, get_app_settings, session)
            body = search_routes.search(
                p,
                session,
                dep(app, library.get_es),
                dep(app, search_routes.get_llm),
                settings,
                dep(app, search_routes.get_http),
                f,
            )
            # notes reach an agent's LLM: strict gate (own, or well-upvoted), never the raw R8 set
            attach_notes(session, p, body["results"], True, strict_gate(settings))
            return {"results": [_slim(h) for h in body["results"]]}

        return _run(app, "search", go, digest(query))

    @mcp.tool()
    def get_document(doc_id: Annotated[str, Field(max_length=100)]) -> dict:
        """Get a document's metadata, summary, tags, votes and versions by id."""

        def go(session: Session, p: Principal) -> dict:
            try:
                did = UUID(doc_id)
            except ValueError:
                raise HTTPException(404, "not found") from None
            return documents_read.detail(did, p, session)

        return _run(app, "get_document", go, doc_id)

    @mcp.tool()
    def get_passages(
        chunk_ids: Annotated[list[str], Field(max_length=MAX_IDS)],
        context: Annotated[int, Field(ge=0, le=2)] = 0,
    ) -> list[dict]:
        """Fetch exact passages by chunk_id ("<version_id>:<n>", at most 50). `context` 0..2 adds that many
        neighbouring chunks either side. Returns text, pages and bboxes; passages the caller cannot see
        are omitted."""
        return _run(
            app,
            "get_passages",
            lambda s, p: _passages(app, s, p, chunk_ids, context),
            ",".join(chunk_ids)[:200],
        )

    @mcp.tool()
    def list_collections() -> list[dict]:
        """List the caller's own collections (id, name, document count, `max_label`: the most sensitive
        label among the documents you may see in it, null when empty)."""
        return _run(app, "list_collections", lambda s, p: _collections(s, p))

    register(mcp, app, _run)
    tools_notes.register(mcp, app, _run)
    tools_writing.register(mcp, app, _run)
    tools_writing.register_guides(mcp, app, _run)
    return mcp
