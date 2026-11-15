"""BibTeX export of documents the caller may read (visible + searchable rule, P8 task 4)."""

import re
import unicodedata
from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.api.routes.search import _own_collections
from roracle.auth import Principal
from roracle.models import Document, DocumentVersion
from roracle.notes.access import readable_doc_ids

MAX_IDS = 200
_ESC = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "&": r"\&",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
}


def escape(s: str) -> str:
    return "".join(_ESC.get(c, c) for c in " ".join(str(s).split()))


def _ascii(s: str) -> str:
    return re.sub(
        r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    )


def _last(name: str) -> str:
    name = name.strip()
    return name.split(",")[0] if "," in name else (name.split() or [""])[-1]


def _key(title: str, meta: dict) -> str:
    authors = [a for a in meta.get("authors") or [] if isinstance(a, str)]
    word = next(iter(re.findall(r"\w+", title)), "")
    return (
        (_ascii(_last(authors[0])) if authors else "anon")
        + _ascii(str(meta.get("year") or "nd"))
        + _ascii(word)
    )


def _entry(key: str, title: str, meta: dict) -> str:
    authors = [a for a in meta.get("authors") or [] if isinstance(a, str)]
    venue = meta.get("venue")
    fields = [("title", title)]
    if authors:
        fields.append(("author", " and ".join(escape(a) for a in authors)))
    if meta.get("year"):
        fields.append(("year", str(meta["year"])))
    if venue:
        fields.append(("journal", venue))
    if meta.get("doi"):
        fields.append(("doi", str(meta["doi"])))
    body = ",\n".join(f"  {k} = {{{v if k == 'author' else escape(v)}}}" for k, v in fields)
    return f"@{'article' if venue else 'misc'}{{{key},\n{body}\n}}\n"


def entries(session: Session, p: Principal, doc_ids: list[UUID], collection_id: str | None) -> list[str]:
    ids = set(doc_ids)
    if collection_id:
        ids |= {UUID(d) for d in _own_collections(session, p, [collection_id])[1]}
    if len(ids) > MAX_IDS:
        raise HTTPException(422, f"at most {MAX_IDS} documents")
    if not ids:
        return []
    docs = list(
        session.scalars(
            select(Document)
            .where(Document.id.in_(ids), Document.id.in_(readable_doc_ids(p)))
            .order_by(Document.title, Document.id)
        )
    )
    versions: dict[UUID, DocumentVersion] = {}
    for v in session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id.in_([d.id for d in docs]))
        .order_by(DocumentVersion.version)
    ):
        cur = versions.get(v.document_id)  # prefer the newest searchable version, else (owner) the newest
        if cur is None or v.stage == "searchable" or cur.stage != "searchable":
            versions[v.document_id] = v
    rows = []
    for d in docs:
        v = versions.get(d.id)
        meta = v.meta if v else {}
        rows.append(
            (_key(d.title or meta.get("title") or "", meta), d.title or meta.get("title") or "", meta)
        )
    groups: dict[str, list[int]] = defaultdict(list)
    for i, (k, _, _) in enumerate(rows):
        groups[k].append(i)
    keys = [k for k, _, _ in rows]
    for k, idx in groups.items():
        if len(idx) > 1:
            for n, i in enumerate(idx):
                keys[i] = k + (chr(97 + n) if n < 26 else str(n))
    return [_entry(keys[i], t, m) for i, (_, t, m) in enumerate(rows)]


def export(session: Session, p: Principal, doc_ids: list[UUID], collection_id: str | None) -> str:
    return "\n".join(entries(session, p, doc_ids, collection_id))
