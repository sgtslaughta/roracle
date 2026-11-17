"""Task 10.5: near-duplicate candidates through LSH bands + ES kNN; ACL-correct and equivalent to the scan."""

import random
import uuid

import numpy as np
import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import func, select

from roracle import maintenance
from roracle.auth import Principal
from roracle.ingest.embed import to_bytes
from roracle.ingest.neardup import (
    _find_exhaustive,
    bands,
    find_near_duplicate,
    minhash,
    similarity,
    write_bands,
)
from roracle.models import Blob, Document, DocumentVersion, Job, User, VersionLsh
from roracle.queue import enqueue

TH, STH = 0.85, 0.97


class NoEs:
    """No indexed chunks at all: kNN contributes nothing."""

    def search(self, **kw):
        return {"hits": {"hits": []}}


def user(db, sub):
    u = User(id=uuid.uuid4(), sub=sub, username=sub, groups=[])
    db.add(u)
    db.flush()
    return u


def principal(u):
    return Principal(user_id=u.id, sub=u.sub, username=u.username, groups=(), is_admin=False)


def version(db, owner, mh, visibility="everyone", stage="searchable", sv=None, bands_too=True):
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    db.add(Blob(sha256=sha, size=1))
    d = Document(owner_id=owner.id, title="t", org_unit="/", visibility=visibility)
    db.add(d)
    db.flush()
    v = DocumentVersion(
        document_id=d.id, version=1, blob_sha256=sha, filename="f", content_type="x", size=1, stage=stage,
        is_latest=True, minhash=mh, summary_embedding=to_bytes(sv) if sv is not None else None,
    )  # fmt: skip
    db.add(v)
    db.flush()
    if bands_too:
        write_bands(db, v.id, mh)
    return d, v


def rand_sig(rng):
    return rng.integers(0, 2**63, 128, dtype=np.uint64).astype("<u8").tobytes()


def mutate(sig, rng, frac):
    a = np.frombuffer(sig, dtype="<u8").copy()
    idx = rng.choice(128, int(128 * frac), replace=False)
    a[idx] = rng.integers(0, 2**63, len(idx), dtype=np.uint64)
    return a.astype("<u8").tobytes()


def test_bands_are_deterministic_16_pairs_and_signed_64_bit():
    sig = minhash(" ".join(f"w{i}" for i in range(300)))
    b = bands(sig)
    assert b == bands(sig) and [x[0] for x in b] == list(range(16))
    assert all(-(2**63) <= h < 2**63 for _, h in b)
    assert bands(minhash("completely different text about zebras " * 20)) != b


def test_a_95_percent_copy_shares_a_band_with_high_probability():
    rng = np.random.default_rng(1)
    hits = 0
    for _ in range(300):
        s = rand_sig(rng)
        t = mutate(s, rng, 0.05)  # ~95% of the rows agree
        hits += bool(set(bands(s)) & set(bands(t)))
    assert hits >= 297  # P = 1 - (1 - 0.95^8)^16 ~ 1.0


def test_near_copy_found_unrelated_not(db_session):
    rng = np.random.default_rng(2)
    ana, bob = user(db_session, "ana"), user(db_session, "bob")
    sig = rand_sig(rng)
    _, orig = version(db_session, bob, sig)
    version(db_session, bob, rand_sig(rng))
    _, new = version(db_session, ana, mutate(sig, rng, 0.05), stage="summarized")
    got = find_near_duplicate(db_session, principal(ana), new, TH, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == orig.id
    _, other = version(db_session, ana, rand_sig(rng), stage="summarized")
    assert find_near_duplicate(db_session, principal(ana), other, TH, STH, es=NoEs(), settings=_s()) is None


def _s():
    from roracle.config import Settings

    return Settings(
        database_url="x", s3_endpoint="x", s3_access_key="x", s3_secret_key="x", oidc_issuer="x", oidc_jwks_url="x",
        embed_dims=4,
    )  # fmt: skip


def test_invisible_candidate_is_never_returned_even_with_a_band_match(db_session):
    rng = np.random.default_rng(3)
    ana, bob = user(db_session, "ana"), user(db_session, "bob")
    sig = rand_sig(rng)
    version(db_session, bob, sig, visibility="private")  # bob's private copy: same bands, not visible to ana
    version(db_session, bob, sig, stage="review")  # parked
    version(db_session, bob, sig, stage="uploaded")  # someone else's unfinished upload (R16)
    _, new = version(db_session, ana, sig, stage="summarized")
    assert find_near_duplicate(db_session, principal(ana), new, TH, STH, es=NoEs(), settings=_s()) is None
    _, mine = version(
        db_session, ana, sig, stage="uploaded"
    )  # her own in-flight copy still counts (as before)
    got = find_near_duplicate(db_session, principal(ana), new, TH, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == mine.id


def test_summary_only_path_through_knn_and_own_inflight(db_session):
    ana, bob = user(db_session, "ana"), user(db_session, "bob")
    vec = [0.5, 0.5, 0.5, 0.5]
    _, pub = version(db_session, bob, None, sv=vec)
    _, new = version(db_session, ana, None, stage="summarized", sv=[0.5, 0.5, 0.5, 0.5001])

    class Es:
        def search(self, **kw):
            assert kw["knn"]["field"] == "summary_embedding" and kw["knn"]["k"] == 10
            assert {"term": {"is_latest": True}} in kw["knn"]["filter"]  # ACL filter + latest, inside knn
            return {"hits": {"hits": [{"_source": {"version_id": str(pub.id)}}]}}

    got = find_near_duplicate(db_session, principal(ana), new, TH, STH, es=Es(), settings=_s())
    assert got is not None and got.id == pub.id
    # a candidate ES returns but Postgres says the uploader may not see: dropped (R19 re-check)
    db_session.execute(
        Document.__table__.update().where(Document.id == pub.document_id).values(visibility="private")
    )
    assert find_near_duplicate(db_session, principal(ana), new, TH, STH, es=Es(), settings=_s()) is None
    # own in-flight version without an index entry: found by the direct scan
    _, own = version(db_session, ana, None, stage="chunked", sv=vec)
    got = find_near_duplicate(db_session, principal(ana), new, TH, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == own.id


def test_equals_the_exhaustive_scan_on_a_2k_doc_fixture(db_session):
    rng = np.random.default_rng(4)
    ana = user(db_session, "ana")
    others = [user(db_session, f"u{i}") for i in range(4)]
    rnd = random.Random(4)
    sigs = []
    for i in range(2000):
        owner = others[i % 4]
        vis = rnd.choice(["everyone", "everyone", "private"])
        sig = rand_sig(rng)
        sigs.append(sig)
        version(db_session, owner, sig, visibility=vis)
    p = principal(ana)
    found = missed = 0
    for i in rnd.sample(range(2000), 40):  # near-copies at 0.9-1.0 similarity: well above the 0.85 threshold
        q = mutate(sigs[i], rng, rnd.choice([0.0, 0.03, 0.06, 0.09]))
        _, new = version(db_session, ana, q, stage="summarized", bands_too=False)
        want = _find_exhaustive(db_session, p, new, TH, STH)
        got = find_near_duplicate(db_session, p, new, TH, STH, es=NoEs(), settings=_s())
        assert (got.id if got else None) == (want.id if want else None)
        found += want is not None
        missed += want is not None and got is None
    assert found > 15 and missed == 0  # recall of true near-duplicates equals the scan's


def test_backfill_job_writes_bands_and_blocks_the_fast_path_until_done(db_session, monkeypatch):
    monkeypatch.setattr(maintenance, "LSH_BATCH", 2)
    rng = np.random.default_rng(5)
    ana = user(db_session, "ana")
    vs = [version(db_session, ana, rand_sig(rng), bands_too=False)[1] for _ in range(5)]
    assert db_session.scalar(select(func.count()).select_from(VersionLsh)) == 0
    enqueue(db_session, "lsh_backfill", {})
    # pending backfill: exhaustive fallback still finds a copy whose bands are not written yet
    _, new = version(db_session, user(db_session, "bob"), vs[0].minhash, stage="summarized", bands_too=False)
    got = find_near_duplicate(
        db_session, principal(ana := user(db_session, "carol")), new, TH, STH, es=NoEs(), settings=_s()
    )
    assert got is not None and got.id == vs[0].id
    payload = {}
    for _ in range(4):
        maintenance.handle_lsh_backfill(db_session, payload)
        nxt = db_session.scalars(select(Job).where(Job.kind == "lsh_backfill", Job.status == "queued")).all()
        nxt = [j for j in nxt if j.payload]
        if not nxt:
            break
        payload = nxt[-1].payload
        nxt[-1].status = "done"
    assert db_session.scalar(select(func.count()).select_from(VersionLsh)) == 6 * 16  # 5 + the new one
    assert similarity(vs[0].minhash, new.minhash) == 1.0 and ana


@pytest.mark.integration
def test_knn_against_real_es_finds_the_summary_twin_and_respects_the_acl(db_session, settings):
    from roracle.search_index import ensure_index, index_name

    es = Elasticsearch(settings.elastic_url)
    cfg = settings.model_copy(update={"embed_model": f"fake-embed-{uuid.uuid4().hex[:8]}", "embed_dims": 4})
    name = ensure_index(es, cfg.embed_model, 4, settings=cfg)
    ana, bob, eve = user(db_session, "ana"), user(db_session, "bob"), user(db_session, "eve")
    vec = [0.5, 0.5, 0.5, 0.5]
    d_pub, pub = version(db_session, bob, None, sv=vec)
    d_priv, priv = version(db_session, eve, None, visibility="private", sv=vec)
    try:
        for d, v, vis, owner in ((d_pub, pub, "everyone", bob), (d_priv, priv, "private", eve)):
            es.index(
                index=name, refresh=True, id=f"{v.id}:0",
                document={
                    "chunk_id": f"{v.id}:0", "doc_id": str(d.id), "version_id": str(v.id), "is_latest": True,
                    "visibility": vis, "owner_id": str(owner.id), "summary_embedding": vec,
                },
            )  # fmt: skip
        _, new = version(db_session, ana, None, stage="summarized", sv=[0.5, 0.5, 0.5, 0.5001])
        got = find_near_duplicate(db_session, principal(ana), new, TH, STH, es=es, settings=cfg)
        assert got is not None and got.id == pub.id  # eve's private twin is filtered by ES and by Postgres
    finally:
        es.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)
