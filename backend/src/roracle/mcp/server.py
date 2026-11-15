"""MCP server: read tools. Each tool = own session, `audit("mcp.<tool>")`, commit; errors -> ToolError."""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from elasticsearch import NotFoundError
from fastapi import FastAPI, HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy.orm import Session

from roracle.api.routes import documents_read, library
from roracle.api.routes import search as search_routes
from roracle.api.routes.search import BadInput
from roracle.audit import audit
from roracle.auth import Principal, get_app_settings
from roracle.chat.access import visible_versions
from roracle.mcp.auth import current, dep, open_session
from roracle.search_acl import acl_filter
from roracle.search_index import ALIAS

MAX_IDS = 50
_SOURCE = ["chunk_id", "doc_id", "version_id", "text", "pages", "bboxes", "title"]


def _run(app: FastAPI, tool: str, fn: Callable[[Session, Principal], Any], target: str = "") -> Any:
    p = current()
    with open_session(app) as session:
        try:
            out = fn(session, p)
            audit(session, p, f"mcp.{tool}", target[:200])
            session.commit()
            return out
        except (HTTPException, BadInput) as e:
            session.rollback()
            audit(session, p, f"mcp.{tool}", target[:200], {"error": True})
            session.commit()
            raise ToolError(str(e.detail if isinstance(e, HTTPException) else e)) from None
        except Exception:
            session.rollback()
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
            query={"bool": {"filter": [acl_filter(p), {"terms": {"chunk_id": sorted(wanted)}}]}},
        )["hits"]["hits"]
    except NotFoundError:
        return []
    srcs = [h["_source"] for h in hits if h["_source"]["chunk_id"] in wanted]
    ok = visible_versions(session, p, srcs)  # R19: Postgres is the authority
    srcs = [s for s in srcs if (s["doc_id"], s["version_id"]) in ok]
    srcs.sort(key=lambda s: (s["version_id"], _split(s["chunk_id"])[1]))
    return [
        {
            "chunk_id": s["chunk_id"],
            "doc_id": s["doc_id"],
            "version_id": s["version_id"],
            "title": s.get("title"),
            "pages": s.get("pages") or [],
            "bboxes": s.get("bboxes") or [],
            "text": s["text"],
        }
        for s in srcs
    ]


def _slim(hit: dict) -> dict:
    return {
        **{k: hit[k] for k in ("doc_id", "title", "authors", "year", "score")},
        "passages": [
            {"chunk_id": x["chunk_id"], "text": x["text"], "page": x["page"]} for x in hit["passages"]
        ],
    }


def build_mcp(app: FastAPI) -> MCPServer:
    mcp = MCPServer("rOracle")

    @mcp.tool()
    def search(
        query: Annotated[str, Field(max_length=search_routes.Q_MAX)],
        collection: Annotated[str | None, Field(max_length=search_routes.VALUE_MAX)] = None,
        limit: int = 10,
    ) -> dict:
        """Search the research library (hybrid keyword + semantic). Returns documents the caller may see,
        each with matching passages and chunk_ids usable with get_passages. `collection` is an id from
        list_collections; `limit` is 1..50 documents."""

        def go(session: Session, p: Principal) -> dict:
            cols = [collection] if collection else None
            search_routes.validate_search_params(query, {"collection": cols}, limit)
            body = search_routes.search(
                p,
                session,
                dep(app, library.get_es),
                dep(app, search_routes.get_llm),
                dep(app, get_app_settings),
                q=query,
                tags=None,
                year=None,
                file_type=None,
                org_unit=None,
                collection=cols,
                author=None,
                sort="relevance",
                page=1,
                size=limit,
            )
            return {"results": [_slim(h) for h in body["results"]]}

        return _run(app, "search", go, query)

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
        chunk_ids: Annotated[list[str], Field(max_length=MAX_IDS)], context: int = 0
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
        """List the caller's own collections (id, name, document count)."""
        return _run(app, "list_collections", lambda s, p: library.list_collections(p, s))

    return mcp
