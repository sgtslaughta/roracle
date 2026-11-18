"""MCP note tools: agents read notes and only *propose* (P8 task 4). `run` is server._run."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException
from mcp.server import MCPServer
from pydantic import Field
from sqlalchemy.orm import Session

from roracle import bibtex
from roracle.api.routes import annotations, library
from roracle.auth import Principal
from roracle.labels.service import label_by_name
from roracle.mcp.auth import dep
from roracle.mcp.tools_write import Run, _need_write, digest
from roracle.notes import proposals
from roracle.notes.access import get_readable
from roracle.notes.schemas import BODY_MAX

Id = Annotated[str, Field(max_length=100)]


def _uuid(v: str) -> UUID:
    try:
        return UUID(v)
    except ValueError:
        raise HTTPException(404, "not found") from None


def _proposal(row) -> dict:
    return {"proposal_id": str(row.id), "status": row.status}


def register(mcp: MCPServer, app: FastAPI, run: Run) -> None:
    @mcp.tool()
    def list_annotations(doc_id: Id) -> dict:
        """List the notes on a document you may read (oldest first, max 500; anchors re-checked against the
        latest version, `may_not_apply_to_version` set when the quote moved or vanished)."""

        def go(session: Session, p: Principal) -> dict:
            items = annotations.list_document_notes(session, dep(app, library.get_es), p, _uuid(doc_id))
            if items is None:
                raise HTTPException(404, "not found")
            return {"items": items}

        return run(app, "list_annotations", go, doc_id)

    @mcp.tool()
    def propose_annotation(
        doc_id: Id,
        kind: Literal["comment", "finding", "correction", "question", "relation"],
        body: Annotated[str, Field(min_length=1, max_length=BODY_MAX)],
        quote: Annotated[str | None, Field(max_length=1000)] = None,
        prefix: Annotated[str, Field(max_length=200)] = "",
        suffix: Annotated[str, Field(max_length=200)] = "",
        page: Annotated[int | None, Field(ge=1)] = None,
        relation_type: Literal["extends", "contradicts", "replicates", "supersedes"] | None = None,
        target_doc_id: Id | None = None,
        visibility: Literal["private", "shared", "everyone"] = "private",
    ) -> dict:
        """Propose a note (needs a write token). Nothing is published: the owner approves it in the
        proposals inbox. Anchor with `quote` (+ prefix/suffix/page); kind 'relation' needs relation_type
        and target_doc_id. At most 50 pending proposals per client. Returns {proposal_id, status}."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(session, p)
            payload: dict = {"document_id": doc_id, "kind": kind, "body": body, "visibility": visibility}
            if quote:
                payload["anchor"] = {"quote": quote, "prefix": prefix, "suffix": suffix, "page": page}
            if relation_type or target_doc_id:
                payload["relation"] = {"type": relation_type, "target_document_id": target_doc_id}
            return _proposal(
                proposals.propose(session, p, "relation" if kind == "relation" else "note", payload)
            )

        return run(app, "propose_annotation", go, digest(body))

    @mcp.tool()
    def propose_label(
        doc_id: Id,
        label: Annotated[str, Field(min_length=1, max_length=40)],
        annotation_id: Id | None = None,
        reason: Annotated[str | None, Field(max_length=500)] = None,
    ) -> dict:
        """Propose a sensitivity label (by name) for a document you can read, or for one of its notes with
        `annotation_id` (needs a write token). Nothing changes until a human approves (lowering needs an
        admin); `reason` is advisory and not stored, the approver supplies their own. Returns {proposal_id, status}."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(session, p)
            payload: dict = {"document_id": doc_id, "label_id": label_by_name(session, label).id}
            if annotation_id:
                payload["annotation_id"] = annotation_id
            if reason:
                payload["reason"] = reason
            return _proposal(proposals.propose(session, p, "label", payload))

        return run(app, "propose_label", go, f"{doc_id}:{label}"[:200])

    @mcp.tool()
    def reply_to_annotation(
        annotation_id: Id, body: Annotated[str, Field(min_length=1, max_length=BODY_MAX)]
    ) -> dict:
        """Propose a reply to a note you can read (needs a write token); a human approves it first.
        Returns {proposal_id, status}."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(session, p)
            n = get_readable(session, p, _uuid(annotation_id))
            payload = {"document_id": str(n.document_id), "parent_id": str(n.id), "body": body}
            return _proposal(proposals.propose(session, p, "note", payload))

        return run(app, "reply_to_annotation", go, digest(body))

    @mcp.tool()
    def export_bibtex(
        doc_ids: Annotated[list[str] | None, Field(max_length=bibtex.MAX_IDS)] = None,
        collection: Annotated[str | None, Field(max_length=100)] = None,
    ) -> dict:
        """BibTeX for documents (ids and/or one of your collections, at most 200); documents you cannot
        see are omitted. Returns {bibtex, count}."""

        def go(session: Session, p: Principal) -> dict:
            if not doc_ids and not collection:
                raise HTTPException(422, "doc_ids or collection required")
            es = bibtex.entries(session, p, [_uuid(d) for d in doc_ids or []], collection)
            return {"bibtex": "\n".join(es), "count": len(es)}

        return run(app, "export_bibtex", go, ",".join(doc_ids or [])[:100] or collection or "")
