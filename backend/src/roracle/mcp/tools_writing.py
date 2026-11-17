"""MCP writing tools (P9). `run` is server._run."""

from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException
from mcp.server import MCPServer
from pydantic import Field
from sqlalchemy.orm import Session

from roracle.api.routes import library
from roracle.api.routes import search as search_routes
from roracle.auth import Principal, get_app_settings
from roracle.labels.policy import Policy, label_json
from roracle.mcp.auth import dep
from roracle.mcp.tools_write import Run, _need_write
from roracle.models import Document
from roracle.writing import cite_check, drafts, skill, validate


def register(mcp: MCPServer, app: FastAPI, run: Run) -> None:
    @mcp.tool()
    def save_draft(
        content: str,
        draft_id: Annotated[str | None, Field(max_length=100)] = None,
        title: Annotated[str | None, Field(max_length=500)] = None,
        profile: Annotated[str | None, Field(max_length=100)] = None,
        base_version: Annotated[int | None, Field(ge=0, le=2**31 - 1)] = None,
    ) -> dict:
        """Save Markdown as a new version of a draft you may edit (needs a write token), or create a draft
        when `draft_id` is absent (then `title` is required; `profile` optional). Pass `base_version` (the
        version you last read) to refuse overwriting a newer save. Returns {draft_id, version, url}."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(p)
            cap = dep(app, get_app_settings, session).draft_max_bytes
            if draft_id is None:
                if not title or not title.strip():
                    raise HTTPException(422, "title is required to create a draft")
                d = drafts.create(session, p, title.strip(), content, profile, cap)
                doc_id, ver = d.document_id, 1
            else:
                try:
                    doc_id = UUID(draft_id)
                except ValueError:
                    raise HTTPException(404, "not found") from None
                ver = drafts.save(session, p, doc_id, content, base_version, cap).version
            lab = Policy.load(session).get(session.get(Document, doc_id).label_id)
            return {
                "draft_id": str(doc_id),
                "version": ver,
                "url": f"/drafts/{doc_id}",
                "label": label_json(lab),
            }

        return run(app, "save_draft", go, draft_id or "")

    @mcp.tool()
    def validate_draft(draft_id: Annotated[str, Field(max_length=100)]) -> dict:
        """Check a draft you may read against its writing profile (structure, limits, notation, acronyms,
        cross-references, units, AI disclosure, citation keys, LLM style). Returns {version, profile, ok,
        blocking, warnings}; each finding is {rule, message, line}. Blocking findings must be fixed."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(p)  # stores a report
            try:
                doc_id = UUID(draft_id)
            except ValueError:
                raise HTTPException(404, "not found") from None
            cfg = dep(app, get_app_settings, session)
            return validate.validate(session, dep(app, search_routes.get_llm), cfg, p, doc_id)

        return run(app, "validate_draft", go, draft_id)

    @mcp.tool()
    def check_citations(draft_id: Annotated[str, Field(max_length=100)]) -> dict:
        """Verify each citation (@doc:/@psg: key + its sentence) of a draft you author against the library
        passages you may read. Returns {version, ok, items}; item = {key, sentence, sentence_hash, status
        resolved|unresolved, verdict supported|unsupported|unclear, reason, chunk_id, doc_id, title,
        acknowledged}. Unresolved keys do not exist or are not readable by you (indistinguishable)."""

        def go(session: Session, p: Principal) -> dict:
            _need_write(p)  # stores a report
            try:
                doc_id = UUID(draft_id)
            except ValueError:
                raise HTTPException(404, "not found") from None
            cfg = dep(app, get_app_settings, session)
            return cite_check.check(
                session, dep(app, library.get_es), dep(app, search_routes.get_llm), cfg, p, doc_id
            )

        return run(app, "check_citations", go, draft_id)


def register_guides(mcp: MCPServer, app: FastAPI, run: Run) -> None:
    """`write_paper` prompt + `roracle://writing/*` resources: same text as the skill zip."""

    def read(name: str, fn) -> str:
        def go(session: Session, p: Principal) -> str:
            try:
                return fn(session)
            except KeyError:
                raise HTTPException(404, "not found") from None

        return run(app, name, go)

    @mcp.prompt()
    def write_paper(topic: Annotated[str, Field(max_length=2000)], profile: str = "journal-article") -> str:
        """Instructions for writing a paper on `topic` as a rOracle draft under a writing profile."""
        return read("write_paper", lambda s: skill.prompt_text(s, topic, profile[:100]))

    @mcp.resource("roracle://writing/profiles", mime_type="application/json")
    def profiles_resource() -> str:
        """Effective writing profiles (name, title, version, source)."""
        return read("writing_profiles", skill.profile_list)

    @mcp.resource("roracle://writing/profiles/{name}", mime_type="application/yaml")
    def profile_resource(name: str) -> str:
        """One writing profile as YAML (limits, required sections, template, style guidance)."""
        return read("writing_profile", lambda s: skill.profile_yaml(s, name[:100]))

    @mcp.resource("roracle://writing/style-guide", mime_type="text/markdown")
    def style_guide() -> str:
        """Writing style guide."""
        return read("writing_style_guide", lambda s: skill.guide("style-guide"))

    @mcp.resource("roracle://writing/citation-rules", mime_type="text/markdown")
    def citation_rules() -> str:
        """Citation key and verification rules."""
        return read("writing_citation_rules", lambda s: skill.guide("citation-rules"))
