"""Deterministic synthetic Postgres corpus: pure `generate_series` SQL, md5-derived ids, then VACUUM ANALYZE.

Ids are `md5('<seed>:<kind>:<i>')::uuid`, so `det_uuid` reproduces them in Python (synth_es uses it to
align chunks with documents). Run it against a scratch database only (e.g. roracle_perf).
"""

import hashlib
import uuid

from sqlalchemy import create_engine, text

BLOB = "0" * 63 + "1"


def det_uuid(seed: int, kind: str, i: int) -> uuid.UUID:
    return uuid.UUID(hashlib.md5(f"{seed}:{kind}:{i}".encode()).hexdigest())


def visibility(i: int, shares_pct: int = 10) -> str:
    """Persona mix per document index: shared `shares_pct`, then private 15, org_unit 15, rest everyone."""
    r = i % 100
    if r < shares_pct:
        return "shared"
    if r < shares_pct + 15:
        return "private"
    if r < shares_pct + 30:
        return "org_unit"
    return "everyone"


def _id(kind: str, col: str = "i") -> str:
    return f"md5(:s || ':{kind}:' || {col})::uuid"


_VIS = (
    "CASE WHEN i % 100 < :sh THEN 'shared' WHEN i % 100 < :sh + 15 THEN 'private'"
    " WHEN i % 100 < :sh + 30 THEN 'org_unit' ELSE 'everyone' END"
)


def _statements() -> dict[str, str]:
    """(count key, SQL) in dependency order. Params: s (seed text), docs, users, sh, co, cit, tags, ..."""
    return {
        "users": (
            f"INSERT INTO users (id, sub, username, email, groups)"
            f" SELECT {_id('u')}, 'perf-sub-' || i, 'perf-user-' || i, 'perf-' || i || '@example.test',"
            f" jsonb_build_array('g' || (i % 20)) FROM generate_series(0, :users - 1) i ON CONFLICT DO NOTHING"
        ),
        "blobs": (
            f"INSERT INTO blobs (sha256, size, refcount) VALUES ('{BLOB}', 1000, 1) ON CONFLICT DO NOTHING"
        ),
        "documents": (
            f"INSERT INTO documents (id, owner_id, title, org_unit, visibility, created_at, label_id)"
            f" SELECT {_id('d')}, {_id('u', '(i % :users)')}, 'Synthetic document ' || i, 'ou' || (i % 10),"
            f" {_VIS}, now() - (:docs - i) * interval '10 seconds',"
            f" (SELECT id FROM labels WHERE is_default LIMIT 1) FROM generate_series(0, :docs - 1) i"
        ),
        "versions": (
            f"INSERT INTO document_versions (id, document_id, version, blob_sha256, filename, content_type,"
            f" size, created_at, stage, is_latest, page_count)"
            f" SELECT {_id('v')}, {_id('d')}, 1, '{BLOB}', 'doc-' || i || '.pdf', 'application/pdf', 1000,"
            f" now() - (:docs - i) * interval '10 seconds', 'searchable', true, 10"
            f" FROM generate_series(0, :docs - 1) i"
        ),
        "tags": (
            f"INSERT INTO tags (document_id, tag) SELECT {_id('d')}, 'tag' || ((i * 7 + k) % 500)"
            f" FROM generate_series(0, :docs - 1) i, generate_series(0, :tags - 1) k"
        ),
        "shares": (
            f"INSERT INTO document_shares (document_id, principal_kind, principal)"
            f" SELECT {_id('d')}, 'user', {_id('u', '((i + 1) % :users)')}::text"
            f" FROM generate_series(0, :docs - 1) i WHERE i % 100 < :sh"
            f" UNION ALL SELECT {_id('d')}, 'group', 'g' || (i % 20)"
            f" FROM generate_series(0, :docs - 1) i WHERE i % 100 < :sh"
        ),
        "coauthors": (
            f"INSERT INTO document_coauthors (document_id, user_id)"
            f" SELECT {_id('d')}, {_id('u', '((i + 3) % :users)')}"
            f" FROM generate_series(0, :docs - 1) i WHERE i % :co = 0 AND (i + 3) % :users <> i % :users"
        ),
        "citations": (
            f"INSERT INTO doc_citations (src_version_id, ref_no, target_document_id, confidence)"
            f" SELECT {_id('v')}, k, {_id('d', '((i * 31 + k * 17 + 1) % :docs)')}, ((i * k) % 100) / 100.0"
            f" FROM generate_series(0, :docs - 1) i, generate_series(1, :cit) k"
        ),
        "jobs_done": (
            "INSERT INTO jobs (kind, payload, status, attempts, run_after, created_at, updated_at)"
            " SELECT (ARRAY['ingest','acl_sync','embed'])[1 + i % 3], jsonb_build_object('n', i), 'done', 1,"
            " now() - (i % 2592000) * interval '1 second', now() - (i % 2592000) * interval '1 second',"
            " now() - (i % 2592000) * interval '1 second' FROM generate_series(1, :jd) i"
        ),
        "jobs_queued": (
            "INSERT INTO jobs (kind, payload, status, run_after)"
            " SELECT CASE WHEN i * 3 <= :jq THEN 'rebuild_units' ELSE (ARRAY['ingest','acl_sync'])[1 + i % 2] END,"
            " jsonb_build_object('n', i), 'queued', now() - (:jq - i + 3600) * interval '1 second'"
            " FROM generate_series(1, :jq) i"
        ),
        "audit": (
            "INSERT INTO audit_log (at, user_id, action, target, client)"
            f" SELECT now() - (i % 2592000) * interval '1 second', {_id('u', '(i % :users)')},"
            " (ARRAY['label.view','search','doc.view'])[1 + i % 3], 'target-' || (i % 1000), 'perf'"
            " FROM generate_series(1, :au) i"
        ),
    }


def seed(
    url: str,
    *,
    docs: int,
    users: int,
    shares_pct: int = 10,
    coauthor_every: int = 20,
    citations_per_doc: int = 5,
    tags_per_doc: int = 2,
    jobs_done: int = 0,
    jobs_queued: int = 0,
    audit_rows: int = 0,
    seed: int = 1,
) -> dict[str, int]:
    """Populate a migrated scratch database; returns inserted row counts per table. Needs a default label."""
    params = {
        "s": str(seed), "docs": docs, "users": users, "sh": shares_pct, "co": coauthor_every,
        "cit": citations_per_doc, "tags": tags_per_doc, "jd": jobs_done, "jq": jobs_queued, "au": audit_rows,
    }  # fmt: skip
    counts: dict[str, int] = {}
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            for key, sql in _statements().items():
                counts[key] = c.execute(text(sql), params).rowcount
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("VACUUM (ANALYZE, PARALLEL 0)"))
    finally:
        engine.dispose()
    return counts


def signature(seed: int, i: int) -> bytes:
    """Deterministic MinHash-shaped signature (NUM_PERM uint64) of synthetic version `i`."""
    from roracle.ingest.neardup import NUM_PERM

    rng = __import__("numpy").random.default_rng([seed, i])
    return rng.integers(0, 2**63, NUM_PERM, dtype="<u8").tobytes()


def seed_lsh(url: str, *, n: int, seed: int = 1, batch: int = 20_000) -> int:
    """minhash + LSH bands for the first `n` synthetic versions (COPY), then the backfill marker, so the ingest path
    uses the banded lookup exactly as in production. Returns the number of band rows."""
    import psycopg

    from roracle.ingest.neardup import bands

    rows = 0
    with psycopg.connect(url.replace("+psycopg", "")) as conn:
        for lo in range(0, n, batch):
            ids = [det_uuid(seed, "v", i) for i in range(lo, min(n, lo + batch))]
            sigs = [signature(seed, i) for i in range(lo, lo + len(ids))]
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE IF NOT EXISTS _mh (id uuid, mh bytea) ON COMMIT DELETE ROWS")
                with cur.copy("COPY _mh (id, mh) FROM STDIN") as cp:
                    for vid, sig in zip(ids, sigs, strict=True):
                        cp.write_row((vid, sig))
                cur.execute("UPDATE document_versions v SET minhash = t.mh FROM _mh t WHERE v.id = t.id")
                with cur.copy("COPY version_lsh (band, hash, version_id) FROM STDIN") as cp:
                    for vid, sig in zip(ids, sigs, strict=True):
                        for band, h in bands(sig):
                            cp.write_row((band, h, vid))
                            rows += 1
            conn.commit()
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('lsh_backfilled', 'true'::jsonb) ON CONFLICT DO NOTHING"
        )
        conn.commit()
        conn.autocommit = True
        conn.execute("VACUUM (ANALYZE, PARALLEL 0) version_lsh")
        conn.execute("VACUUM (ANALYZE, PARALLEL 0) document_versions")
    return rows
