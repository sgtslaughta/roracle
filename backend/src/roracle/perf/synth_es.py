"""Deterministic synthetic Elasticsearch chunks: unit-norm random vectors, Zipf text, persona-mix ACLs.

Only indices named `perf-*` are ever written. Documents align with `synth_pg.seed` (same `det_uuid` ids and
`visibility` mix), so the API can be loaded against both.
"""

from collections.abc import Iterator
from typing import Any

import numpy as np
from elasticsearch import Elasticsearch, helpers

from roracle.perf.synth_pg import det_uuid, visibility
from roracle.search_index import _mapping, acl_hash

VOCAB = 5000
WORDS_PER_CHUNK = 40


def _actions(
    index: str, chunks: int, dims: int, docs: int, personas: dict, seed: int, label_id: str | None
) -> Iterator[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    users, groups, ous = personas.get("users", 100), personas.get("groups", 20), personas.get("org_units", 10)
    shares_pct = personas.get("shares_pct", 10)
    for c in range(chunks):
        d = c % docs
        n = c // docs
        vis = visibility(d, shares_pct)
        vec = rng.standard_normal(dims).astype(np.float32)
        vec /= np.linalg.norm(vec)
        words = np.minimum(rng.zipf(1.3, WORDS_PER_CHUNK), VOCAB)
        vid = str(det_uuid(seed, "v", d))
        src: dict[str, Any] = {
            "doc_id": str(det_uuid(seed, "d", d)),
            "version_id": vid,
            "version": 1,
            "is_latest": True,
            "chunk_id": f"{vid}:{n}",
            "owner_id": str(det_uuid(seed, "u", d % users)),
            "visibility": vis,
            "org_unit": f"ou{d % ous}",
            "coauthor_users": [str(det_uuid(seed, "u", (d + 3) % users))] if d % 20 == 0 else [],
            "share_users": [str(det_uuid(seed, "u", (d + 1) % users))] if vis == "shared" else [],
            "share_groups": [f"g{d % groups}"] if vis == "shared" else [],
            "title": f"Synthetic document {d}",
            "tags": [f"tag{(d * 7 + k) % 500}" for k in range(2)],
            "file_type": "pdf",
            "year": 2000 + d % 26,
            "text": " ".join(f"w{w}" for w in words),
            "embedding_model": "perf",
            "embedding": vec.tolist(),
        }
        if label_id:
            src["label_id"] = label_id
            src["acl_hash"] = acl_hash({**src, "tags": src["tags"]}, src["title"])  # equals the PG-side hash
        yield {"_index": index, "_id": f"{vid}:{n}", "_source": src}


def seed(
    es: Elasticsearch,
    index: str,
    *,
    chunks: int,
    dims: int,
    docs: int,
    personas: dict,
    seed: int = 1,
    batch: int = 2000,
    aliases: tuple[str, ...] = (),
    label_id: str | None = None,
) -> int:
    """Create `index` (must start with `perf-`) and bulk-index `chunks` chunks round-robin over `docs`
    documents. `aliases` (e.g. the ROR_READ_ALIAS / ROR_MUTATE_ALIAS values) are added to it. Returns count."""
    if not index.startswith("perf-"):
        raise ValueError("synthetic data is only written to perf-* indices")
    if bad := [a for a in aliases if not a.startswith("perf-")]:
        raise ValueError(f"aliases must start with 'perf-': {bad}")
    if not es.indices.exists(index=index):
        es.indices.create(
            index=index,
            mappings=_mapping(dims),
            settings={"index": {"number_of_replicas": 0, "refresh_interval": "-1"}},
        )
    helpers.bulk(
        es.options(request_timeout=120),
        _actions(index, chunks, dims, docs, personas, seed, label_id),
        chunk_size=batch,
    )
    es.indices.put_settings(index=index, settings={"index": {"refresh_interval": "1s"}})
    es.indices.refresh(index=index)
    if aliases:
        es.indices.update_aliases(actions=[{"add": {"index": index, "alias": a}} for a in aliases])
    return int(es.count(index=index)["count"])
