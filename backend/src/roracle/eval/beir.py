"""`roracle eval import-beir`: seed a BEIR-format dataset (SciFact) from a local directory, offline.

Each corpus entry becomes a real Document: a text/plain blob in the store, a minimal Docling JSON, and a version
at stage "parsed" handed to the normal `ingest` job (summary, near-dup, chunker, embedding, index, R16), so
rebuilds, the viewer and later features see ordinary documents. The job queue does the batching. The dataset
directory is a mirrored artifact: nothing here touches the network.

Idempotent: a document is keyed by `meta.beir_id = "beir:<dataset>:<corpus-id>"` per owner. A re-run (also under a
second set name) reuses documents, re-queues the unfinished ones and only adds missing gold queries.
Layout: corpus.jsonl ({_id,title,text}), queries.jsonl ({_id,text}), qrels/test.tsv (query-id, corpus-id, score).
"""

import csv
import hashlib
import json
from pathlib import Path
from typing import Literal

from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem
from docling_core.types.doc.base import CoordOrigin, Size
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import reembed
from roracle.audit import audit
from roracle.auth import principal_for_user
from roracle.config import Settings
from roracle.ingest.pipeline import TERMINAL, docling_key
from roracle.models import Blob, Document, DocumentShare, DocumentVersion, User
from roracle.models_eval import EvalQuery, EvalSet
from roracle.queue import enqueue

BATCH = 100
Visibility = Literal["private", "shared", "org_unit", "everyone"]


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


def docling_json(title: str, text: str) -> dict:
    doc = DoclingDocument(name=title[:80] or "doc")
    doc.add_page(page_no=1, size=Size(width=612, height=792))
    parent = doc.add_text(label=DocItemLabel.TITLE, text=title)
    box = BoundingBox(l=72, t=700, r=500, b=650, coord_origin=CoordOrigin.BOTTOMLEFT)
    doc.add_text(
        parent=parent, label=DocItemLabel.TEXT, text=text,
        prov=ProvenanceItem(page_no=1, bbox=box, charspan=(0, len(text))),
    )  # fmt: skip
    return doc.export_to_dict()


def _user(session: Session, name: str, what: str) -> User:
    u = session.scalar(select(User).where(User.username == name))
    if u is None:
        raise ValueError(f"no user named {name!r} (they must have logged in once)")
    if principal_for_user(session, u.id).is_admin:
        raise ValueError(f"{what} {name!r} is an admin; use non-admin users (R5)")
    return u


def import_beir(
    session: Session, store, settings: Settings, root: Path, *, set_name: str, owner: str,
    dataset: str = "scifact", visibility: Visibility = "private", principals: list[str] = (),
    groups: list[str] = (),
) -> dict:  # fmt: skip
    """Gold principals = owner + `principals`; they (and `groups`) get access by share, which needs visibility
    "shared" (raised from "private" automatically). Returns counts."""
    for f in ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"):
        if not (root / f).is_file():
            raise ValueError(f"{root / f} not found (expected BEIR layout)")
    if reembed.running_job(session) is not None:
        raise ValueError("a re-embed cutover is running; import after it finishes")
    own = _user(session, owner, "owner")
    gold_users = [own, *(u for u in (_user(session, n, "principal") for n in principals) if u.id != own.id)]
    if (gold_users[1:] or groups) and visibility == "private":
        visibility = "shared"
    tag = f"beir:{dataset}"
    es_set = session.scalar(select(EvalSet).where(EvalSet.name == set_name))
    if es_set is not None and es_set.description != f"{tag} (imported offline)":
        raise ValueError(f"eval set {set_name!r} exists and was not created by this importer")
    if es_set is None:
        es_set = EvalSet(name=set_name, description=f"{tag} (imported offline)", created_by=own.id)
        session.add(es_set)
        session.commit()  # the set exists before any document, so a failed run is resumable
    qrels = read_qrels(root / "qrels" / "test.tsv")

    known: dict[str, tuple] = {}  # corpus id -> (document id, version id, stage)
    rows = session.execute(
        select(DocumentVersion.meta["beir_id"].astext, Document.id, DocumentVersion.id, DocumentVersion.stage)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.owner_id == own.id, DocumentVersion.meta["beir_id"].astext.like(f"{tag}:%"))
    )
    for ext, did, vid, stage in rows:
        known[ext[len(tag) + 1 :]] = (did, vid, stage)

    ids: dict[str, object] = {}
    created = queued = 0
    for row in _jsonl(root / "corpus.jsonl"):
        cid, text = str(row["_id"]), (row.get("text") or "").strip()
        if not text:
            continue
        if cid in known:
            did, vid, stage = known[cid]
            ids[cid] = did
            if stage not in TERMINAL:
                enqueue(session, "ingest", {"version_id": str(vid)})
                queued += 1
            continue
        title = (row.get("title") or "").strip() or cid
        data = text.encode()
        sha = hashlib.sha256(data).hexdigest()
        store.put_bytes(store.key(sha), data, "text/plain")
        if session.get(Blob, sha) is None:
            session.add(Blob(sha256=sha, size=len(data)))
        doc = Document(owner_id=own.id, title=title, visibility=visibility, org_unit="/")
        session.add(doc)
        session.flush()
        session.refresh(doc)  # the default label is a server-side expression
        session.add_all(
            DocumentShare(document_id=doc.id, principal_kind="user", principal=str(u.id))
            for u in gold_users[1:]
        )
        session.add_all(
            DocumentShare(document_id=doc.id, principal_kind="group", principal=g) for g in groups
        )
        ver = DocumentVersion(
            document_id=doc.id, version=1, blob_sha256=sha, filename=f"{cid}.txt", content_type="text/plain",
            size=len(data), stage="parsed", is_latest=True, page_count=1,
            meta={"authors": [], "year": None, "beir_id": f"{tag}:{cid}"},
        )  # fmt: skip
        session.add(ver)
        session.flush()
        store.put_bytes(
            docling_key(ver.id), json.dumps(docling_json(title, text)).encode(), "application/json"
        )
        enqueue(session, "ingest", {"version_id": str(ver.id)})
        ids[cid] = doc.id
        created += 1
        if created % BATCH == 0:
            session.commit()
    session.commit()

    have = set(session.scalars(select(EvalQuery.key).where(EvalQuery.set_id == es_set.id)))
    n = 0
    for row in _jsonl(root / "queries.jsonl"):
        key = f"beir-{row['_id']}"
        gold = {d: g for d, g in qrels.get(str(row["_id"]), {}).items() if d in ids}
        if not gold or key in have:
            continue
        session.add(
            EvalQuery(
                set_id=es_set.id, key=key, text=row["text"][:2000], kind="fact",
                principals=[u.id for u in gold_users],
                relevant=[{"doc": str(ids[d]), "grade": min(max(g, 1), 3)} for d, g in sorted(gold.items())],
            )
        )  # fmt: skip
        n += 1
    audit(
        session, None, "eval.import_beir", set_name,
        {"dataset": dataset, "owner": owner, "created": created, "reused": len(ids) - created, "queries": n},
    )  # fmt: skip
    session.commit()
    return {"documents": created, "reused": len(ids) - created, "requeued": queued, "queries": n}
