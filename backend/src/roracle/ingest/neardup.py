"""MinHash near-duplicate detection among documents visible to the uploader.

Stored hashes are `MinHash.hashvalues` (uint64 x 128, little-endian "<u8") bytes, built with seed=1 and scheme affine64.
NEVER change NUM_PERM, SEED, shingle size or normalization without re-hashing stored versions:
signatures from different settings are not comparable.
"""

import hashlib
import re
import uuid

import numpy as np
from datasketch import MinHash
from elasticsearch import NotFoundError
from sqlalchemy import delete, exists, or_, select, text, tuple_
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.config import Settings
from roracle.ingest.embed import from_bytes
from roracle.labels.policy import Policy
from roracle.models import Document, DocumentVersion, Job, VersionLsh
from roracle.search_acl import acl_filter
from roracle.search_index import active_index

NUM_PERM = 128
SEED = 1
SCHEME = "affine64"  # uint64 hashvalues; explicit because it must be explicit when reloading hashvalues
SHINGLE = 5
BANDS, ROWS = 16, 8  # 16 bands x 8 rows: P(candidate) = 1-(1-s^8)^16 = 0.9999 at s=.9, .947 at .8, .60 at .7
MAX_CANDIDATES = 500  # a degenerate bucket never turns the check back into a scan
KNN_K = 10


def _shingles(text: str) -> set[str]:
    words = re.findall(r"[^\W_]+", text.lower())
    if not words:
        return set()
    if len(words) < SHINGLE:
        return {" ".join(words)}
    return {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def minhash(text: str) -> bytes | None:
    """Signature bytes, or None for text without words (never matches anything)."""
    shingles = _shingles(text)
    if not shingles:
        return None
    m = MinHash(num_perm=NUM_PERM, seed=SEED, scheme=SCHEME)
    for s in shingles:
        m.update(s.encode())
    return m.hashvalues.astype("<u8").tobytes()


def _load(b: bytes) -> MinHash:
    return MinHash(
        num_perm=NUM_PERM,
        seed=SEED,
        scheme=SCHEME,
        hashvalues=np.frombuffer(b, dtype="<u8").astype(np.uint64),
    )


def similarity(a: bytes | None, b: bytes | None) -> float:
    if not a or not b:
        return 0.0
    return float(_load(a).jaccard(_load(b)))


def cosine(a: bytes | None, b: bytes | None) -> float:
    if not a or not b:
        return 0.0
    x, y = from_bytes(a), from_bytes(b)
    if x.shape != y.shape:
        return 0.0
    n = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(np.dot(x, y)) / n if n else 0.0


def bands(mh: bytes, b: int = BANDS, r: int = ROWS) -> list[tuple[int, int]]:
    """LSH keys of a signature: (band, signed 64-bit digest of that band's `r` rows). Two signatures share a band
    only when all `r` rows agree, so near-duplicates collide in at least one band with high probability."""
    rows = np.frombuffer(mh, dtype="<u8")
    return [
        (
            i,
            int.from_bytes(
                hashlib.blake2b(rows[i * r : (i + 1) * r].tobytes(), digest_size=8).digest(),
                "big",
                signed=True,
            ),
        )
        for i in range(b)
    ]


def write_bands(session: Session, version_id, mh: bytes | None) -> None:
    """Replace the version's LSH rows (no commit: callers own the transaction)."""
    session.execute(delete(VersionLsh).where(VersionLsh.version_id == version_id))
    if mh:
        session.add_all(VersionLsh(band=b, hash=h, version_id=version_id) for b, h in bands(mh))
        session.flush()


def _visible(principal: Principal, version: DocumentVersion):
    """Candidates the uploader may be told about (R16/R19): visible documents, a searchable version unless it is
    the uploader's own, latest, not parked/rejected/cancelled, never the same document."""
    return (
        visible_documents(principal),
        or_(Document.owner_id == principal.user_id, DocumentVersion.stage == "searchable"),  # R16
        DocumentVersion.is_latest.is_(True),
        DocumentVersion.document_id != version.document_id,
        DocumentVersion.stage.not_in(["review", "rejected", "cancelled"]),
    )


def _best(version: DocumentVersion, candidates, threshold: float, summary_threshold: float):
    best, best_sim = None, 0.0
    for c in candidates:
        sims = []
        if version.minhash and c.minhash:
            s = similarity(version.minhash, c.minhash)
            if s >= threshold:
                sims.append(s)
        if version.summary_embedding and c.summary_embedding:
            s = cosine(version.summary_embedding, c.summary_embedding)
            if s >= summary_threshold:
                sims.append(s)
        if sims and max(sims) > best_sim:
            best, best_sim = c, max(sims)
    return best


def lsh_backfill_pending(session: Session) -> bool:
    return bool(
        session.scalar(
            select(exists().where(Job.kind == "lsh_backfill", Job.status.in_(("queued", "running"))))
        )
    )


def find_near_duplicate(
    session: Session,
    principal: Principal,
    version: DocumentVersion,
    threshold: float,
    summary_threshold: float,
    *,
    es=None,
    settings: Settings | None = None,
) -> DocumentVersion | None:
    """Best visible near-duplicate of `version`, or None.

    With `es` and `settings` (the ingest path): candidates come from (a) the LSH bands of the MinHash, (b) ES kNN
    on `summary_embedding` under the uploader's ACL filter, (c) the uploader's own in-flight versions (not yet
    indexed). EVERY candidate is then loaded through the same visibility predicates as the exhaustive check
    (R19 re-check in Postgres) and verified with the exact Jaccard / cosine against the unchanged thresholds.
    Without them (tools, tests) or while `lsh_backfill` is still running: the exhaustive scan."""
    if not version.minhash and not version.summary_embedding:
        return None
    if es is None or settings is None or lsh_backfill_pending(session):
        return _find_exhaustive(session, principal, version, threshold, summary_threshold)
    ids: set = set()
    if version.minhash:
        keys = bands(version.minhash)
        ids.update(
            session.scalars(
                select(VersionLsh.version_id)
                .where(tuple_(VersionLsh.band, VersionLsh.hash).in_(keys))
                .distinct()
                .limit(MAX_CANDIDATES)
            )
        )
    if version.summary_embedding:
        ids.update(_knn_candidates(session, es, settings, principal, version))
    q = (
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(*_visible(principal, version))
    )
    cands = list(session.scalars(q.where(DocumentVersion.id.in_(ids)))) if ids else []
    # the uploader's own versions that are not searchable yet exist in neither ES nor (summary vectors) the bands
    if version.summary_embedding:
        cands += session.scalars(
            q.where(
                Document.owner_id == principal.user_id,
                DocumentVersion.stage != "searchable",
                DocumentVersion.summary_embedding.is_not(None),
                DocumentVersion.id.not_in(ids) if ids else text("true"),
            ).limit(MAX_CANDIDATES)
        )
    return _best(version, cands, threshold, summary_threshold)


def _knn_candidates(session, es, settings, principal, version) -> list:
    vec = from_bytes(version.summary_embedding).tolist()
    if len(vec) != settings.embed_dims:  # a vector of another model: not comparable (and not indexed)
        return []
    try:
        r = es.search(
            index=active_index(settings),
            knn={
                "field": "summary_embedding",
                "query_vector": vec,
                "k": KNN_K,
                "num_candidates": 10 * KNN_K,
                "filter": [acl_filter(principal, Policy.load(session)), {"term": {"is_latest": True}}],
            },
            size=KNN_K,
            source=["version_id"],
        )
    except NotFoundError:  # nothing indexed yet
        return []
    out = []
    for h in r["hits"]["hits"]:
        try:
            out.append(uuid.UUID(h["_source"]["version_id"]))
        except (KeyError, ValueError):
            pass
    return out


def _find_exhaustive(
    session: Session,
    principal: Principal,
    version: DocumentVersion,
    threshold: float,
    summary_threshold: float,
) -> DocumentVersion | None:
    """The original linear scan over every visible version (reference for the equivalence tests)."""
    candidates = session.scalars(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            *_visible(principal, version),
            or_(DocumentVersion.minhash.is_not(None), DocumentVersion.summary_embedding.is_not(None)),
        )
    )
    return _best(version, candidates, threshold, summary_threshold)
