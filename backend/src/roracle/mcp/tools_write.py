"""MCP tools: ask, upload and handoffs. `run` is server._run (own session, audit `mcp.<tool>`, ToolError)."""

import base64
import binascii
import hashlib
import json
import mimetypes
from collections.abc import Callable
from io import BytesIO
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
import openai
from elasticsearch import ConnectionError as EsConnectionError
from elasticsearch import TransportError
from fastapi import FastAPI, HTTPException
from mcp.server import MCPServer
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, UploadFile

from roracle import handoff
from roracle.api.routes import documents, library
from roracle.api.routes import search as search_routes
from roracle.auth import Principal, get_app_settings
from roracle.chat.answer import build_messages, citation_item, parse_citations, split_notes
from roracle.chat.global_answer import answer_sync, route_question
from roracle.chat.retrieve import Viewer, retrieve_for_chat
from roracle.labels.policy import Policy, label_json
from roracle.labels.service import label_by_name
from roracle.mcp.auth import dep
from roracle.models import Document


def digest(text: str) -> str:
    """Audit target for free text: a hash, never the text (matches security.md)."""
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]


NO_PASSAGES = "No accessible passages matched."
Run = Callable[..., Any]


def _need_write(p: Principal) -> None:
    if "write" not in p.scopes:
        raise HTTPException(403, "insufficient scope")


def _abs(app: FastAPI, m: dict) -> dict:
    base = app.state.settings.public_base_url.rstrip("/")
    return {**m, "url": base + m["url"]} if base else m


def _ask(app: FastAPI, session: Session, p: Principal, question: str, collection: str | None) -> dict:
    s = dep(app, get_app_settings, session)
    llm = dep(app, search_routes.get_llm)
    scope = f"collection:{collection}" if collection else "all"
    prep, plan, meta = None, None, {}  # `route` is reported only when the router is on
    try:
        if s.router:
            es = dep(app, library.get_es)
            prep, meta = route_question(session, es, llm, s, p, question, scope == "all")
            plan = answer_sync(session, es, llm, s, p, question, prep) if prep else None
            if prep and plan is None:  # nothing citable in the global pass: answer from the fact path
                meta = {"route": "fact"}
        passages = (
            plan[1]
            if plan
            else retrieve_for_chat(
                session=session,
                es=dep(app, library.get_es),
                llm=llm,
                settings=s,
                p=p,
                question=question,
                viewer=Viewer(),
                scope=scope,
                pins=[],
                excludes=[],
            )
        )  # gate (a): retrieve_for_chat applies exclude_llm in ES and Postgres
        passages, notes = split_notes(passages)
        if not passages:
            return {"answer": NO_PASSAGES, "citations": [], **meta}
        r = llm.chat.completions.create(
            model=s.chat_model_list[0],
            messages=plan[0] if plan else build_messages(question, passages, [], 0, notes),
            **({"max_tokens": s.global_reduce_tokens} if plan else {}),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except (openai.APIError, httpx.HTTPError):
        raise HTTPException(503, "inference unavailable") from None
    except (EsConnectionError, TransportError):
        raise HTTPException(503, "search unavailable") from None
    text = r.choices[0].message.content or ""
    ns = parse_citations(text, len(passages))
    cites = [citation_item(n, passages[n - 1]) for n in ns]
    policy = Policy.load(session)
    labels = dict(
        session.execute(
            select(Document.id, Document.label_id).where(Document.id.in_({UUID(c["doc_id"]) for c in cites}))
        ).all()
    )
    return {
        "answer": text,
        **meta,
        "citations": [{**c, "label": label_json(policy.get(labels[UUID(c["doc_id"])]))} for c in cites],
    }


def _upload(app, session, p, filename, b64, title, visibility, label_id=None) -> dict:
    _need_write(p)
    s = dep(app, get_app_settings, session)
    if len(b64) * 3 // 4 > s.mcp_max_upload_bytes + 3:
        raise HTTPException(413, "file too large for MCP upload; use POST /documents")
    try:
        data = base64.b64decode("".join(b64.split()), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "invalid base64") from None
    if len(data) > s.mcp_max_upload_bytes:
        raise HTTPException(413, "file too large for MCP upload; use POST /documents")
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    f = UploadFile(BytesIO(data), filename=filename, headers=Headers({"content-type": ctype}))
    try:
        return documents.upload_file(
            p, session, dep(app, documents.get_blob_store), s, f,
            documents.UploadForm(title=title, visibility=visibility, label_id=label_id),
        )  # fmt: skip
    except HTTPException as e:
        if e.status_code == 409 and isinstance(e.detail, dict):  # already in the library: not an error
            return {"duplicate_of": e.detail["document_id"]}
        raise


def register(mcp: MCPServer, app: FastAPI, run: Run) -> None:
    max_b64 = app.state.settings.mcp_max_upload_bytes * 4 // 3 + 8

    @mcp.tool()
    def ask(
        question: Annotated[str, Field(min_length=1, max_length=4000)],
        collection: Annotated[str | None, Field(max_length=200)] = None,
    ) -> dict:
        """Ask a question answered only from library passages the caller may see. Returns the answer
        with [n] markers and `citations` (doc_id, chunk_id, page, quote). `collection` is an id from
        list_collections."""
        return run(app, "ask", lambda s, p: _ask(app, s, p, question, collection), digest(question))

    @mcp.tool()
    def upload(
        filename: Annotated[str, Field(min_length=1, max_length=255)],
        content_base64: Annotated[str, Field(max_length=max_b64)],
        title: Annotated[str | None, Field(max_length=200)] = None,
        visibility: Literal["private", "shared", "org_unit", "everyone"] = "private",
        label: Annotated[str | None, Field(max_length=40)] = None,
    ) -> dict:
        """Upload a document (base64 content; needs a write token; size-capped, larger files use
        POST /documents). `label` is a sensitivity label name (default: the library default; choosing a
        lower one than the default is refused). Returns document_id/version_id, or {"duplicate_of": id}."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(p)  # scope error before any label lookup
            lid = label_by_name(session, label).id if label else None
            return _upload(app, session, p, filename, content_base64, title, visibility, lid)

        return run(app, "upload", go, filename)

    @mcp.tool()
    def load_handoff(code: Annotated[str, Field(min_length=1, max_length=200)]) -> dict:
        """Load a handoff (code, XXXX-XXXX, /h/ URL or roracle://handoff/ URI): brief, goal, documents,
        passages, notes and `parts` page counts for get_handoff_part. Content you cannot see is
        counted in `withheld`."""
        return run(app, "load_handoff", lambda s, p: _abs(app, handoff.load(s, p, code)), code)

    @mcp.tool()
    def get_handoff_part(
        code: Annotated[str, Field(min_length=1, max_length=200)],
        part: Annotated[str, Field(max_length=20)] = "transcript",
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> dict:
        """One page of a handoff's `transcript` or `notes` (20 items per page)."""
        return run(app, "get_handoff_part", lambda s, p: handoff.get_part(s, p, code, part, page), code)

    @mcp.tool()
    def create_handoff(
        title: Annotated[str, Field(max_length=200)],
        brief: Annotated[str, Field(max_length=20000)],
        doc_ids: Annotated[list[str] | None, Field(max_length=500)] = None,
        goal: Annotated[str, Field(max_length=2000)] = "",
        audience: Literal["creator", "authenticated"] = "creator",
        expires_in_days: Annotated[int, Field(ge=1, le=90)] = 7,
    ) -> dict:
        """Create a handoff (needs a write token) so a person or another agent can continue this work.
        Documents the caller cannot see are dropped; `dropped` counts requested ids not kept (unknown ids included). Returns code, display,
        url, uri, dropped."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(p)
            h = handoff.create(
                session, p, "agent", title=title, goal=goal, brief=brief, doc_ids=doc_ids or [],
                audience=audience, expires_in_days=expires_in_days, via="mcp",
            )  # fmt: skip
            m = _abs(app, handoff.view(session, p, h))
            out = {k: m[k] for k in ("code", "display", "url", "uri")}
            return {**out, "dropped": len(set(doc_ids or [])) - len(h.doc_ids)}

        return run(app, "create_handoff", go, title)

    @mcp.tool()
    def append_to_handoff(
        code: Annotated[str, Field(min_length=1, max_length=200)],
        note: Annotated[str, Field(min_length=1, max_length=10000)],
    ) -> dict:
        """Append a note to a handoff you can load (needs a write token)."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(p)
            return handoff.append_note(session, p, code, note, via="mcp")

        return run(app, "append_to_handoff", go, code)

    @mcp.resource("roracle://handoff/{code}", mime_type="application/json")
    def handoff_resource(code: str) -> str:
        """A handoff manifest, same content as load_handoff."""
        return json.dumps(
            run(app, "handoff_resource", lambda s, p: _abs(app, handoff.load(s, p, code)), code)
        )
