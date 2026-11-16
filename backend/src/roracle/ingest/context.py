"""LLM contextual note per chunk (R9): situates the chunk in its document for BM25 + embedding.

One call per chunk, sequential, with a stable `<document>` message first so server-side prefix caching
applies. Notes are cached per version; an `exclude_llm` label (re-read from Postgres before EVERY call,
ADR-0014) turns the note into the heading path with no LLM call.

Notes are untrusted LLM output about this document only: they feed BM25/embeddings of the same document and must
never be added to a retrieval `_source` list or an LLM prompt without the exclude_llm + R19 gates.
The cache key is sha256(chunk text) per version: it ignores the model and `context_doc_chars` (R9), so a
change of context model or prompt requires bumping CONTEXT_PROMPT_VERSION.
"""

import hashlib
import json
import logging

from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.labels.policy import Policy
from roracle.models import Document, DocumentVersion

log = logging.getLogger(__name__)

CONTEXT_PROMPT_VERSION = "1"
MAX_NOTE_CHARS = 600
SYSTEM = (
    "You write retrieval notes. Given a document and one chunk of it, reply with 1-2 short sentences "
    "(50-100 words at most) that situate the chunk within the document: what the document is, which "
    "section or topic the chunk belongs to, and any entity a bare reading of the chunk would miss. "
    "Plain text, no preamble, no quotation of the chunk."
)


def cache_key(version_id) -> str:
    return f"derived/{version_id}/context-v{CONTEXT_PROMPT_VERSION}.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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


def heading_note(c) -> str:
    return " > ".join(c.headings)


def window(doc_text: str, chunk, n: int, total: int, size: int) -> str:
    """Whole document if it fits, else a window snapped to half-size steps so neighbouring chunks share
    an identical prefix (prefix-cache friendly) while still containing the chunk's position."""
    if len(doc_text) <= size:
        return doc_text
    pos = doc_text.find(chunk.text[:80])
    centre = pos + len(chunk.text) // 2 if pos >= 0 else int(len(doc_text) * (n + 0.5) / max(total, 1))
    step = max(size // 2, 1)
    start = (centre // step) * step - step // 2
    start = max(0, min(start, len(doc_text) - size))
    return doc_text[start : start + size]


def _excluded(session: Session, d: Document) -> bool:
    session.refresh(d, ["label_id"])  # a raise mid-job stops further calls
    return Policy.load(session).has(d.label_id, "exclude_llm")


def notes_for(
    session: Session,
    llm,
    settings: Settings,
    store,
    v: DocumentVersion,
    d: Document,
    doc_text: str,
    chunks: list,
) -> list[str]:
    key = cache_key(v.id)
    cache = _load(store, key)
    fresh: dict[str, str] = {}
    out: list[str] = []
    try:
        for i, c in enumerate(chunks):
            sha = _sha(c.text)
            if sha in cache:  # reused even after a later label raise (same as summaries, R9)
                out.append(cache[sha])
                continue
            if i >= settings.context_max_chunks or _excluded(session, d):
                out.append(heading_note(c))
                continue
            win = window(doc_text, c, c.n, len(chunks), settings.context_doc_chars)
            resp = llm.chat.completions.create(
                model=settings.context_model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"<document>\n{win}\n</document>"},
                    {
                        "role": "user",
                        "content": f"<chunk>\n{c.text}\n</chunk>\nSituate this chunk within the document.",
                    },
                ],
                temperature=0,
                max_tokens=settings.context_max_tokens,
            )
            note = " ".join((resp.choices[0].message.content or "").split())[:MAX_NOTE_CHARS]
            fresh[sha] = cache[sha] = note
            out.append(note)
    finally:
        if fresh:  # keep progress on a mid-document failure; heading fallbacks are never cached
            store.put_bytes(key, json.dumps(cache).encode(), "application/json")
    return out
