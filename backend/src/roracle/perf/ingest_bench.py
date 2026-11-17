"""In-process ingest measurements on a seeded scratch corpus: per-document pipeline overhead and near-duplicate lookup.

Models and the parser are replaced by zero-latency fakes, so the numbers are OUR code + Postgres + Elasticsearch at the
corpus size: the part of the 2 s/doc overhead budget (and the 50 ms near-dup budget) the application owns. The
throughput budget (>= 4 docs/min/worker) is dominated by docling and the LLM, which cannot be measured here."""

import hashlib
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
from sqlalchemy import select, text

from roracle import runtime
from roracle.auth import Principal
from roracle.config import Settings
from roracle.ingest import neardup, pipeline
from roracle.models import Document, DocumentVersion, User
from roracle.perf.report import Report
from roracle.perf.synth_pg import BLOB, det_uuid, signature


class FakeLlm:
    def __init__(self, dims: int):
        self.dims = dims
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _embed(self, model, input):
        def vec(t: str):
            rng = np.random.default_rng(int.from_bytes(hashlib.sha256(t.encode()).digest()[:8], "little"))
            v = rng.standard_normal(self.dims).astype(np.float32)
            return (v / np.linalg.norm(v)).tolist()

        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=vec(t)) for i, t in enumerate(input)])

    def _chat(self, **kw):
        digest = hashlib.sha256(kw["messages"][-1]["content"].encode()).hexdigest()[:12]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=f"A summary {digest}."))]
        )


class MemStore:
    def __init__(self):
        self.d: dict[str, bytes] = {}

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.d[key] = data

    def get_bytes(self, key):
        return self.d[key]


def _docling(paras: int = 30) -> dict:
    from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem
    from docling_core.types.doc.base import CoordOrigin, Size

    doc = DoclingDocument(name="t")
    doc.add_page(page_no=1, size=Size(width=612, height=792))
    parent = doc.add_text(label=DocItemLabel.TITLE, text="Perf probe " + uuid.uuid4().hex[:8])
    for i in range(paras):
        doc.add_text(
            parent=parent, label=DocItemLabel.TEXT, text=f"Paragraph {i} {uuid.uuid4().hex} " * 12,
            prov=ProvenanceItem(
                page_no=1, bbox=BoundingBox(l=72, t=700 - i, r=500, b=650 - i, coord_origin=CoordOrigin.BOTTOMLEFT),
                charspan=(0, 10),
            ),
        )  # fmt: skip
    return doc.export_to_dict()


def prepare(sf, settings: Settings, es, seed: int = 1) -> None:
    """Point the scratch DB at the perf index, mark the LSH backfill done (as after the migration)."""
    with sf() as s:
        runtime.set_active_embedding(s, settings.embed_model, settings.embed_dims, f"perf-chunks-{seed}")
        neardup.mark_lsh_ready(s)
        s.commit()


def ingest_overhead(sf, settings: Settings, es, *, docs: int, warmup: int = 3, seed: int = 1) -> dict:
    """Wall seconds per document for handle_ingest from the `parsed` stage (summary, embed, chunk, index, near-dup,
    dedupe, refresh, state writes) with instant fake models, plus the pipeline's own near-dup call latencies."""
    store, llm = MemStore(), FakeLlm(settings.embed_dims)
    with sf() as s:
        owner = s.get(User, det_uuid(seed, "u", 0))
        ids = []
        for _ in range(docs + warmup):
            d = Document(owner_id=owner.id, title=None, org_unit="ou0", visibility="everyone")
            s.add(d)
            s.flush()
            v = DocumentVersion(
                document_id=d.id, version=1, blob_sha256=BLOB, filename="p.pdf", content_type="application/pdf",
                size=1, stage="parsed",
            )  # fmt: skip
            s.add(v)
            s.flush()
            store.put_bytes(pipeline.docling_key(v.id), __import__("json").dumps(_docling()).encode())
            ids.append(v.id)
        s.commit()
    nd: list[float] = []
    real = pipeline.find_near_duplicate

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return real(*a, **k)
        finally:
            nd.append((time.perf_counter() - t) * 1000)

    pipeline.find_near_duplicate = timed
    lat: list[float] = []
    try:
        for i, vid in enumerate(ids):
            with sf() as s:
                t = time.perf_counter()
                pipeline.handle_ingest(
                    s,
                    {"version_id": str(vid)},
                    settings=settings,
                    store=store,
                    es=es,
                    llm=llm,
                    session_factory=sf,
                )
                s.commit()
                if i >= warmup:
                    lat.append(time.perf_counter() - t)
    finally:
        pipeline.find_near_duplicate = real
    with sf() as s:
        done = s.scalar(select(DocumentVersion.stage).where(DocumentVersion.id == ids[-1]))
        doc_ids = [
            str(d) for d in s.scalars(select(DocumentVersion.document_id).where(DocumentVersion.id.in_(ids)))
        ]
    cleanup(sf, es, doc_ids)
    return {"seconds": Report.of(lat), "near_dup_in_pipeline_ms": Report.of(nd[warmup:]), "last_stage": done}


def cleanup(sf, es, doc_ids: list[str]) -> None:
    """Remove the probe documents again (their chunks, jobs and rows), so repeated runs leave the corpus as seeded."""
    es.delete_by_query(
        index="perf-chunks-all", query={"terms": {"doc_id": doc_ids}}, refresh=True, conflicts="proceed"
    )
    with sf() as s:
        s.execute(
            text(
                "DELETE FROM jobs WHERE payload->>'version_id' IN (SELECT id::text FROM document_versions WHERE document_id = ANY(CAST(:d AS uuid[])))"
            ),
            {"d": doc_ids},
        )
        for sql in (
            "DELETE FROM doc_citations WHERE src_version_id IN (SELECT id FROM document_versions WHERE document_id = ANY(CAST(:d AS uuid[])))",
            "DELETE FROM document_versions WHERE document_id = ANY(CAST(:d AS uuid[]))",
            "DELETE FROM documents WHERE id = ANY(CAST(:d AS uuid[]))",
        ):
            s.execute(text(sql), {"d": doc_ids})
        s.commit()


def near_dup_probe(sf, settings: Settings, es, *, samples: int, lsh_docs: int, seed: int = 1) -> Report:
    """Near-duplicate lookup of a version whose signature differs from a seeded one in ~6% of the rows (Jaccard
    ~0.88): it collides in several of the 16 bands, so the candidate load + exact verification run as in production."""
    rng = np.random.default_rng(7)
    lat: list[float] = []
    with sf() as s:
        owner = s.get(User, det_uuid(seed, "u", 0))
        p = Principal(
            user_id=owner.id, sub=owner.sub, username=owner.username, groups=("g0",), is_admin=False
        )
        doc = s.scalar(select(Document).where(Document.owner_id == owner.id).limit(1))
        for k in range(samples + 5):
            sig = np.frombuffer(signature(seed, int(rng.integers(0, lsh_docs))), dtype="<u8").copy()
            flip = rng.random(sig.shape) < 0.06
            sig[flip] = rng.integers(0, 2**63, int(flip.sum()), dtype="<u8")
            v = DocumentVersion(
                id=uuid.uuid4(), document_id=doc.id, version=99, blob_sha256=BLOB, filename="q.pdf",
                content_type="x", size=1, stage="summarized", minhash=sig.astype("<u8").tobytes(),
                created_at=datetime.now(UTC),
            )  # fmt: skip
            t = time.perf_counter()
            neardup.find_near_duplicate(
                s,
                p,
                v,
                settings.near_dup_threshold,
                settings.near_dup_summary_threshold,
                es=es,
                settings=settings,
            )
            s.rollback()
            if k >= 5:
                lat.append((time.perf_counter() - t) * 1000)
    return Report.of(lat)
