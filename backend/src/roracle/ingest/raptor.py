"""Per-document RAPTOR (R10): section summaries -> one document summary, indexed as extra retrieval units.

Strictly per document: a unit is built from the chunks of ONE version and cached under that version
(`derived/{version_id}/raptor-v1.json`), never combining two documents. Units are retrieval aids only: the chat
path expands a unit hit into its real source chunks (`retrieval.expand_units`), so a summary is never cited.

Gate: an `exclude_llm` label (re-read from Postgres before EVERY call, ADR-0014) yields no units and no LLM
call. Summaries are untrusted LLM output about this document only. The cache key is sha256(input text); a
change of model or prompt requires bumping RAPTOR_PROMPT_VERSION.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Literal

from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.ingest.chunking import Chunk
from roracle.labels.policy import Policy
from roracle.models import Document, DocumentVersion

log = logging.getLogger(__name__)

RAPTOR_PROMPT_VERSION = "1"
WINDOW = 8  # chunks per section when the document has no headings
MAX_UNIT_CHARS = 1500
MAX_SOURCES = 200  # source chunk ids kept on the document unit
SECTION_SYSTEM = (
    "You write retrieval summaries. Summarise this section of a research document in 3-5 sentences, "
    "keeping names, methods, numbers and terminology a searcher would use. Plain text, no preamble."
)
DOC_SYSTEM = (
    "You write retrieval summaries. Given the section summaries of one research document, write a "
    "5-8 sentence summary of the whole document covering its goal, methods, findings and terminology. "
    "Plain text, no preamble."
)


@dataclass
class Unit:
    kind: Literal["section", "document"]
    key: str  # "s{i}" | "d": chunk id suffix, `{version_id}:{key}`
    text: str
    headings: list[str]
    source_chunks: list[str]
    pages: list[int]


def cache_key(version_id) -> str:
    return f"derived/{version_id}/raptor-v{RAPTOR_PROMPT_VERSION}.json"


def sections(chunks: list[Chunk]) -> list[list[Chunk]]:
    """Consecutive chunks sharing `headings[0]` form a section; headingless runs are cut into windows of 8."""
    out: list[list[Chunk]] = []
    run: list[Chunk] = []
    key: str | None = None
    for c in chunks:
        k = c.headings[0] if c.headings else None
        if run and (k != key or (k is None and len(run) >= WINDOW)):
            out.append(run)
            run = []
        key = k
        run.append(c)
    if run:
        out.append(run)
    return out


def _sha(system: str, text: str) -> str:
    return hashlib.sha256(f"{system}\n{text}".encode()).hexdigest()


def _load(store, key: str) -> dict[str, str]:
    try:
        data = json.loads(store.get_bytes(key))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "NotFound", "404"):
            return {}
        raise
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _excluded(session: Session, d: Document) -> bool:
    session.refresh(d, ["label_id"])  # a raise mid-job stops further calls
    return Policy.load(session).has(d.label_id, "exclude_llm")


def _chat(llm, model: str, system: str, text: str) -> str:
    resp = llm.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        temperature=0,
        max_tokens=400,
    )
    return " ".join((resp.choices[0].message.content or "").split())[:MAX_UNIT_CHARS]


def build_units(
    session: Session,
    llm,
    settings: Settings,
    store,
    v: DocumentVersion,
    d: Document,
    chunks: list[Chunk],
) -> list[Unit]:
    """Section units + one document unit for this version; `[]` for an `exclude_llm` document."""
    if not chunks or _excluded(session, d):
        return []
    model = settings.raptor_model or settings.summary_model
    key = cache_key(v.id)
    cache = _load(store, key)
    fresh = False
    units: list[Unit] = []

    def summary(system: str, text: str) -> str | None:
        nonlocal fresh
        sha = _sha(system, text)
        if sha in cache:  # reused even after a later label raise (same as notes/summaries, R9)
            return cache[sha]
        if _excluded(session, d):
            return None
        cache[sha] = out = _chat(llm, model, system, text)
        fresh = True
        return out

    try:
        for i, sec in enumerate(sections(chunks)):
            body = "\n\n".join(c.text for c in sec)[: settings.raptor_section_chars]
            heads = sec[0].headings
            text = summary(SECTION_SYSTEM, ("\n".join(heads) + "\n\n" if heads else "") + body)
            if text is None:
                return []
            if text:
                units.append(
                    Unit(
                        "section", f"s{i}", text, list(heads),
                        [f"{v.id}:{c.n}" for c in sec], sorted({p for c in sec for p in c.pages}),
                    )
                )  # fmt: skip
        if not units:
            return []
        text = summary(DOC_SYSTEM, "\n\n".join(u.text for u in units)[: settings.raptor_section_chars])
        if text is None:
            return []
        if text:
            srcs = [s for u in units for s in u.source_chunks]
            step = max(1, -(-len(srcs) // MAX_SOURCES))
            units.append(
                Unit("document", "d", text, [], srcs[::step], sorted({p for u in units for p in u.pages}))
            )
    finally:
        if fresh:  # keep progress on a mid-document failure
            store.put_bytes(key, json.dumps(cache).encode(), "application/json")
    return units
