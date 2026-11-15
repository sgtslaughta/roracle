"""Shared fakes for ingest/index tests."""

import hashlib
import json
import uuid
from functools import partial
from types import SimpleNamespace

import numpy as np
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem
from docling_core.types.doc.base import CoordOrigin, Size

from roracle.ingest import handle_ingest
from roracle.models import Blob, Document, DocumentVersion


def docling_doc(
    title: str | None = "A Study of Retrieval", topic: str = "retrieval", paras: int = 12
) -> dict:
    doc = DoclingDocument(name="t")
    doc.add_page(page_no=1, size=Size(width=612, height=792))
    parent = None
    if title:
        parent = doc.add_text(label=DocItemLabel.TITLE, text=title)
    for i in range(paras):
        doc.add_text(
            parent=parent,
            label=DocItemLabel.TEXT,
            text=f"Paragraph {i} about {topic} and unique words {uuid.uuid4().hex} " * 6,
            prov=ProvenanceItem(
                page_no=1,
                bbox=BoundingBox(l=72, t=700 - i, r=500, b=650 - i, coord_origin=CoordOrigin.BOTTOMLEFT),
                charspan=(0, 10),
            ),
        )
    return doc.export_to_dict()


class FakeLLM:
    def __init__(self, dims: int):
        self.dims = dims
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _embed(self, model, input):
        def vec(t):  # deterministic per text, zero-mean: different texts are unlikely summary near-dups
            rng = np.random.default_rng(int.from_bytes(hashlib.sha256(t.encode()).digest()[:8], "little"))
            return rng.standard_normal(self.dims).tolist()

        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=vec(t)) for i, t in enumerate(input)])

    def _chat(self, **kw):  # summary depends on the input, so distinct docs get distinct summary vectors
        digest = hashlib.sha256(kw["messages"][-1]["content"].encode()).hexdigest()[:12]
        msg = SimpleNamespace(content=f"A summary {digest}.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def make_version(db_session, store, owner, *, doc=None, version=1, name="p.pdf", **doc_kw) -> DocumentVersion:
    data = uuid.uuid4().bytes
    sha = hashlib.sha256(data).hexdigest()
    store.client.put_object(Bucket=store.bucket, Key=store.key(sha), Body=data)
    db_session.add(Blob(sha256=sha, size=len(data)))
    if doc is None:
        doc = Document(
            **{"owner_id": owner.id, "title": None, "org_unit": "/", "visibility": "everyone", **doc_kw}
        )
        db_session.add(doc)
        db_session.flush()
    v = DocumentVersion(
        document_id=doc.id,
        version=version,
        blob_sha256=sha,
        filename=name,
        content_type="application/pdf",
        size=1,
    )
    db_session.add(v)
    db_session.commit()
    return v


def handler(settings, store, es, llm, session_factory):
    return partial(
        handle_ingest, settings=settings, store=store, es=es, llm=llm, session_factory=session_factory
    )


def fake_convert(doc: dict):
    return lambda *a, **k: json.loads(json.dumps(doc))
