"""MinHash near-duplicate detection among documents visible to the uploader.

Stored hashes are `MinHash.hashvalues` (uint64 x 128, little-endian "<u8") bytes, built with seed=1 and scheme affine64.
NEVER change NUM_PERM, SEED, shingle size or normalization without re-hashing stored versions:
signatures from different settings are not comparable.
"""

import re

import numpy as np
from datasketch import MinHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.models import Document, DocumentVersion

NUM_PERM = 128
SEED = 1
SCHEME = "affine64"  # uint64 hashvalues; explicit because it must be explicit when reloading hashvalues
SHINGLE = 5


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


def find_near_duplicate(
    session: Session, principal: Principal, version: DocumentVersion, threshold: float
) -> DocumentVersion | None:
    if not version.minhash:
        return None
    # ponytail: linear scan over visible versions; move to LSH buckets in Elastic when > ~50k docs per user scope.
    candidates = session.scalars(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            visible_documents(principal),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.minhash.is_not(None),
            DocumentVersion.document_id != version.document_id,
            DocumentVersion.stage.not_in(["review", "rejected", "cancelled"]),
        )
    )
    best, best_sim = None, 0.0
    for c in candidates:
        s = similarity(version.minhash, c.minhash)
        if s >= threshold and s > best_sim:
            best, best_sim = c, s
    return best
