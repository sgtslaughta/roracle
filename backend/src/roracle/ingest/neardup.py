"""MinHash near-duplicate detection among documents visible to the uploader.

Stored hashes are `MinHash.hashvalues` (uint64 x 128, little-endian "<u8") bytes, built with seed=1 and scheme affine64.
NEVER change NUM_PERM, SEED, shingle size or normalization without re-hashing stored versions:
signatures from different settings are not comparable.
"""

import hashlib
import logging
import re
import uuid

import numpy as np
from datasketch import MinHash
from elasticsearch import NotFoundError
from sqlalchemy import and_, delete, exists, func, not_, or_, select, text, tuple_
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.config import Settings
from roracle.ingest.embed import from_bytes
from roracle.labels.policy import Policy
from roracle.metrics import NEARDUP_EXHAUSTIVE
from roracle.models import AppSetting, Document, DocumentVersion, Job, VersionLsh
from roracle.search_acl import acl_filter
from roracle.search_index import active_index

log = logging.getLogger(__name__)
NUM_PERM = 128
SEED = 1
SCHEME = "affine64"  # uint64 hashvalues; explicit because it must be explicit when reloading hashvalues
SHINGLE = 5
BANDS, ROWS = 16, 8  # 16 bands x 8 rows: P(candidate) = 1-(1-s^8)^16 = 0.9999 at s=.9, .947 at .8, .60 at .7
MAX_CANDIDATES = 500  # applied AFTER visibility, best band overlap first: a degenerate bucket is never a scan
MIN_LSH_THRESHOLD = 0.8  # 16x8 banding misses 2% of pairs at 0.80, 16% at 0.75, 37% at 0.70: below it, scan
PRE_DEDUPE_STAGES = (
    "uploaded",
    "scanned",
    "parsed",
    "summarized",
)  # before the version's own dedupe finished
LSH_MARKER = (
    "lsh_backfilled"  # app_settings key written by the last backfill batch (or the migration when empty)
)
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
    # Two uploads of the same paper at once: whoever checks SECOND is parked. A newer own version that has not
    # finished its own check yet (it will find us) is ignored; one that already passed it (deduped, chunked,
    # embedded, ...) must count, else both copies would be indexed. `_dedupe` serialises the checks per owner.
    mine_newer = and_(
        Document.owner_id == principal.user_id,
        DocumentVersion.stage.in_(PRE_DEDUPE_STAGES),
        or_(
            DocumentVersion.created_at > version.created_at,
            and_(DocumentVersion.created_at == version.created_at, DocumentVersion.id > version.id),
        ),
    )
    return (
        visible_documents(principal),
        not_(mine_newer),
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


_ready = False


def lsh_ready(session: Session) -> bool:
    """True once the backfill finished (explicit marker; a failed or missing backfill keeps the exhaustive check).
    Only a positive answer is cached in-process: it never turns false again."""
    global _ready
    if not _ready:
        _ready = session.get(AppSetting, LSH_MARKER) is not None
        if not _ready:
            _warn_not_ready(session)
    return _ready


def _warn_not_ready(session: Session) -> None:
    """No marker: the exhaustive scan runs. Expected while `lsh_backfill` is queued/running; if no such job exists
    the backfill failed (or was never enqueued) and every ingest stays on the slow path: log + metric for ops."""
    pending = session.scalar(
        select(exists().where(Job.kind == "lsh_backfill", Job.status.in_(("queued", "running"))))
    )
    if not pending:
        NEARDUP_EXHAUSTIVE.labels("backfill_missing_or_failed").inc()
        log.warning(
            "near-dup LSH marker missing and no lsh_backfill job is queued/running: using the exhaustive scan"
        )


def mark_lsh_ready(session: Session) -> None:
    session.merge(AppSetting(key=LSH_MARKER, value=True))
    session.flush()


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
    Without them (tools, tests), before the backfill marker exists, or when `threshold < MIN_LSH_THRESHOLD` (the
    banding is tuned for >= 0.8): the exhaustive scan. Summary vectors that are not in ES (indexed before
    `summary_embedding` existed, other dimensions) are only compared by the exhaustive scan until re-embed/rebuild."""
    if not version.minhash and not version.summary_embedding:
        return None
    if es is None or settings is None or threshold < MIN_LSH_THRESHOLD or not lsh_ready(session):
        return _find_exhaustive(session, principal, version, threshold, summary_threshold)
    ids: set = set()
    if version.minhash:
        keys = bands(version.minhash)
        hits = func.count()
        ids.update(  # visibility FIRST, then the best band overlap, then the cap
            session.scalars(
                select(DocumentVersion.id)
                .select_from(VersionLsh)
                .join(DocumentVersion, DocumentVersion.id == VersionLsh.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(tuple_(VersionLsh.band, VersionLsh.hash).in_(keys), *_visible(principal, version))
                .group_by(DocumentVersion.id)
                .order_by(hits.desc(), DocumentVersion.id)
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
            )
            .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id)  # deterministic under the cap
            .limit(MAX_CANDIDATES)
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


def find_sibling_duplicate(
    session: Session,
    principal: Principal,
    version: DocumentVersion,
    threshold: float,
    summary_threshold: float,
    since,
) -> DocumentVersion | None:
    """The uploader's OWN versions that raced us: in flight (not searchable) or created since `since` (the start of the
    lock-free global search). Cheap by construction (band lookup restricted to one owner, no ES, no scan), so it can run
    inside the per-owner lock; `version`'s bands must already be written."""
    q = (
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            *_visible(principal, version),
            Document.owner_id == principal.user_id,
            DocumentVersion.id != version.id,
            or_(DocumentVersion.stage != "searchable", DocumentVersion.created_at >= since),
        )
    )
    match = []
    if version.minhash:
        match.append(
            DocumentVersion.id.in_(
                select(VersionLsh.version_id).where(
                    tuple_(VersionLsh.band, VersionLsh.hash).in_(bands(version.minhash))
                )
            )
        )
    if version.summary_embedding:
        match.append(DocumentVersion.summary_embedding.is_not(None))
    if not match:
        return None
    return _best(
        version,
        session.scalars(
            q.where(or_(*match))
            .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id)
            .limit(MAX_CANDIDATES)
        ),
        threshold,
        summary_threshold,
    )


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
