"""`roracle eval import-beir`: seed a BEIR-format dataset (SciFact) from a local directory, offline.

Direct seeding instead of the upload path: the corpus is plain text, so uploading would mean fabricating PDFs
only for Docling to parse them back. Documents are still ordinary rows (owner, visibility, label default,
`acl_fields` in ES), written through the same indexer as ingest, so every ACL and label path applies to
them unchanged. Nothing here touches the network; the dataset directory is a mirrored artifact.

Layout: corpus.jsonl ({_id,title,text}), queries.jsonl ({_id,text}), qrels/test.tsv (query-id, corpus-id, score).
"""

import csv
import hashlib
import json
import uuid
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import search_index
from roracle.config import Settings
from roracle.ingest.chunking import Chunk
from roracle.ingest.embed import doc_prompt, embed, prompt_sha
from roracle.models import Blob, Document, DocumentVersion, User
from roracle.models_eval import EvalQuery, EvalSet

BATCH = 64


def _jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_qrels(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) != 3 or not row[2].lstrip("-").isdigit():
                continue  # header
            if int(row[2]) > 0:
                out.setdefault(row[0], {})[row[1]] = int(row[2])
    return out


def import_beir(
    session: Session, es, llm, settings: Settings, root: Path, *, set_name: str, owner: str,
    visibility: Literal["private", "shared", "org_unit", "everyone"] = "everyone",
) -> dict:  # fmt: skip
    for f in ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"):
        if not (root / f).is_file():
            raise ValueError(f"{root / f} not found (expected BEIR layout)")
    user = session.scalar(select(User).where(User.username == owner))
    if user is None:
        raise ValueError(f"no user named {owner!r} (they must have logged in once)")
    if session.scalar(select(EvalSet.id).where(EvalSet.name == set_name)):
        raise ValueError(f"eval set {set_name!r} already exists")
    qrels = read_qrels(root / "qrels" / "test.tsv")
    index = search_index.active_index(settings)
    if not es.indices.exists(
        index=index
    ):  # created by the API/worker at start-up; never repoint aliases here
        raise ValueError(f"search index {index!r} does not exist; start the worker once first")
    ids: dict[str, uuid.UUID] = {}
    batch: list[dict] = []

    def flush() -> None:
        if not batch:
            return
        prompts = [doc_prompt(b["title"], b["text"]) for b in batch]
        vecs = embed(llm, settings.embed_model, prompts, settings.embed_dims)
        for b, vec, pr in zip(batch, vecs, prompts, strict=True):
            search_index.index_chunks(
                es, b["ver"], b["doc"], [Chunk(0, b["text"], "", [], [1], [])], [vec],
                search_index.acl_fields(session, b["doc"]), settings.embed_model, title=b["title"],
                prompt_shas=[prompt_sha(pr)], index=index,
            )  # fmt: skip
        session.commit()
        batch.clear()

    for row in _jsonl(root / "corpus.jsonl"):
        title = (row.get("title") or "").strip() or str(row["_id"])
        text = (row.get("text") or "").strip()
        if not text:
            continue
        sha = hashlib.sha256(f"beir:{set_name}:{row['_id']}".encode()).hexdigest()
        if session.get(Blob, sha) is None:
            session.add(Blob(sha256=sha, size=len(text)))
        doc = Document(owner_id=user.id, title=title, visibility=visibility, org_unit="/")
        session.add(doc)
        session.flush()
        session.refresh(doc)  # the default label is a server-side expression
        ver = DocumentVersion(
            document_id=doc.id, version=1, blob_sha256=sha, filename=f"{row['_id']}.txt",
            content_type="text/plain", size=len(text), stage="searchable", is_latest=True, page_count=1,
            meta={"authors": [], "year": None, "beir_id": str(row["_id"])},
        )  # fmt: skip
        session.add(ver)
        session.flush()
        ids[str(row["_id"])] = doc.id
        batch.append({"title": title, "text": text, "doc": doc, "ver": ver})
        if len(batch) >= BATCH:
            flush()
    flush()

    es_set = EvalSet(name=set_name, description="SciFact (BEIR), imported offline", created_by=user.id)
    session.add(es_set)
    session.flush()
    n = 0
    for row in _jsonl(root / "queries.jsonl"):
        gold = {d: g for d, g in qrels.get(str(row["_id"]), {}).items() if d in ids}
        if not gold:
            continue
        session.add(
            EvalQuery(
                set_id=es_set.id, key=f"beir-{row['_id']}", text=row["text"][:2000], kind="fact",
                principals=[user.id],
                relevant=[{"doc": str(ids[d]), "grade": min(max(g, 1), 3)} for d, g in sorted(gold.items())],
            )
        )  # fmt: skip
        n += 1
    session.commit()
    return {"documents": len(ids), "queries": n}
