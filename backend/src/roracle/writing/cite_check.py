"""Citation keys: parsing and the deterministic (PostgreSQL) resolution. Task 4 extends this module.

R19: a key the caller cannot read looks exactly like one that does not exist (both resolve to None)."""

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.chat.access import visible_versions
from roracle.models import Document, DocumentVersion
from roracle.models_writing import Draft
from roracle.notes.access import readable_doc_ids

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_DOC = re.compile(rf"doc:({_UUID})")
_PSG = re.compile(rf"psg:({_UUID}):(\d{{1,9}})")


def parse_key(key: str) -> tuple | None:
    """`("doc", UUID)` | `("psg", version UUID, n)` | None when malformed."""
    if m := _DOC.fullmatch(key):
        return ("doc", UUID(m.group(1)))
    if m := _PSG.fullmatch(key):
        return ("psg", UUID(m.group(1)), int(m.group(2)))
    return None


def resolve_keys(session: Session, p: Principal, keys: list[str]) -> dict[str, dict | None]:
    """key -> {"kind","doc_id",...} when the caller can read it, else None (malformed, missing or invisible).

    `psg` chunk existence is not checked here (needs ES)."""
    parsed = {k: parse_key(k) for k in dict.fromkeys(keys)}
    vers = {x[1] for x in parsed.values() if x and x[0] == "psg"}
    ver_doc = (
        {
            v: d
            for v, d in session.execute(
                select(DocumentVersion.id, DocumentVersion.document_id).where(DocumentVersion.id.in_(vers))
            )
        }
        if vers
        else {}
    )
    cand = {x[1] for x in parsed.values() if x and x[0] == "doc"} | set(ver_doc.values())
    ok_docs = (
        set(
            session.scalars(
                select(Document.id).where(
                    Document.id.in_(cand),
                    Document.id.in_(readable_doc_ids(p)),
                    Document.id.notin_(select(Draft.document_id)),  # drafts are never library documents
                )
            )
        )
        if cand
        else set()
    )
    seen = visible_versions(
        session, p, [{"doc_id": str(ver_doc[v]), "version_id": str(v)} for v in vers if v in ver_doc]
    )
    out: dict[str, dict | None] = {}
    for k, x in parsed.items():
        out[k] = None
        if x is None:
            continue
        if x[0] == "doc" and x[1] in ok_docs:
            out[k] = {"kind": "doc", "doc_id": str(x[1])}
        elif x[0] == "psg" and (d := ver_doc.get(x[1])) and d in ok_docs and (str(d), str(x[1])) in seen:
            out[k] = {"kind": "psg", "doc_id": str(d), "version_id": str(x[1]), "n": x[2]}
    return out
