"""Task 10.5: near-duplicate candidates through LSH bands + ES kNN; ACL-correct and equivalent to the scan."""

import random
import time
import uuid

import numpy as np
import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import func, select, text

from roracle import maintenance
from roracle.auth import Principal
from roracle.ingest.embed import to_bytes
from roracle.ingest.neardup import (
    _find_exhaustive,
    bands,
    find_near_duplicate,
    lsh_ready,
    mark_lsh_ready,
    minhash,
    similarity,
    write_bands,
)
from roracle.models import Blob, Document, DocumentVersion, Job, User, VersionLsh
from roracle.queue import enqueue

TH, STH = 0.85, 0.97


@pytest.fixture(autouse=True)
def _ready(db_session, request):
    """The LSH path is only used after the backfill marker exists (see test_backfill_*)."""
    if "backfill" not in request.node.name:
        mark_lsh_ready(db_session)


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
    )  # her own in-flight copy still counts (as before) when it is the OLDER upload
    db_session.execute(
        DocumentVersion.__table__.update()
        .where(DocumentVersion.id == mine.id)
        .values(created_at=new.created_at - __import__("datetime").timedelta(seconds=5))
    )
    db_session.refresh(mine)
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
    assert lsh_ready(db_session)  # the LAST batch wrote the marker
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


def test_failed_or_partial_backfill_never_switches_to_lsh(db_session, monkeypatch):
    monkeypatch.setattr(maintenance, "LSH_BATCH", 2)
    rng = np.random.default_rng(6)
    ana = user(db_session, "ana")
    for _ in range(5):
        version(db_session, ana, rand_sig(rng), bands_too=False)
    maintenance.handle_lsh_backfill(db_session, {})  # first batch only: the chained job "failed" (never ran)
    assert not lsh_ready(db_session)
    db_session.execute(Job.__table__.insert().values(kind="lsh_backfill", payload={}, status="failed"))
    assert not lsh_ready(db_session)  # a failed job is not a marker


def test_hidden_band_sharing_versions_never_crowd_out_the_visible_copy(db_session, monkeypatch):
    from roracle.ingest import neardup

    monkeypatch.setattr(neardup, "MAX_CANDIDATES", 5)
    rng = np.random.default_rng(7)
    ana, bob = user(db_session, "ana"), user(db_session, "bob")
    sig = rand_sig(rng)
    for _ in range(
        40
    ):  # 40 private copies of the same paper: more than the candidate cap, none visible to ana
        version(db_session, bob, sig, visibility="private")
    _, pub = version(db_session, bob, sig)
    _, new = version(db_session, ana, sig, stage="summarized")
    got = find_near_duplicate(db_session, principal(ana), new, TH, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == pub.id


def test_threshold_below_the_banding_floor_uses_the_exhaustive_scan(db_session):
    rng = np.random.default_rng(8)
    ana, bob = user(db_session, "ana"), user(db_session, "bob")
    sig = rand_sig(rng)
    _, orig = version(db_session, bob, sig, bands_too=False)  # no bands at all: only a scan can find it
    _, new = version(
        db_session, ana, mutate(sig, rng, 0.2), stage="summarized", bands_too=False
    )  # ~0.8 similar
    assert find_near_duplicate(db_session, principal(ana), new, 0.85, STH, es=NoEs(), settings=_s()) is None
    got = find_near_duplicate(db_session, principal(ana), new, 0.75, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == orig.id


def test_pairs_at_the_threshold_edge_are_still_found(db_session):
    rng = np.random.default_rng(9)
    ana = user(db_session, "ana")
    others = [user(db_session, f"o{i}") for i in range(3)]
    sigs = []
    for i in range(300):
        sigs.append(rand_sig(rng))
        version(db_session, others[i % 3], sigs[-1])
    p, wanted, found = principal(ana), 0, 0
    for i in range(0, 300, 6):  # ĵ in 0.86-0.88: just above the 0.85 default
        q = mutate(sigs[i], rng, 0.125)
        _, new = version(db_session, ana, q, stage="summarized", bands_too=False)
        want = _find_exhaustive(db_session, p, new, TH, STH)
        got = find_near_duplicate(db_session, p, new, TH, STH, es=NoEs(), settings=_s())
        wanted += want is not None
        found += (got.id if got else None) == (want.id if want else None)
    assert wanted >= 40 and found >= 49  # exact miss rate at the edge is ~0.04%: at most one pair of 50


def test_two_simultaneous_uploads_park_only_the_later_one(db_session):
    rng = np.random.default_rng(10)
    ana = user(db_session, "ana")
    sig = rand_sig(rng)
    _, a = version(db_session, ana, sig, stage="summarized")
    _, b = version(db_session, ana, sig, stage="summarized")
    db_session.execute(
        DocumentVersion.__table__.update()
        .where(DocumentVersion.id == b.id)
        .values(created_at=a.created_at.replace(microsecond=0) + __import__("datetime").timedelta(seconds=5))
    )
    db_session.refresh(a)
    db_session.refresh(b)
    p = principal(ana)
    assert (
        find_near_duplicate(db_session, p, a, TH, STH, es=NoEs(), settings=_s()) is None
    )  # B is newer: ignored
    got = find_near_duplicate(db_session, p, b, TH, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == a.id  # only the later upload is parked


def test_newer_copy_that_already_passed_its_own_check_parks_the_older_one(db_session):
    """B (newer) dedupes first and goes on to indexing; A (older) dedupes while B is `chunked`: A must be parked."""
    rng = np.random.default_rng(11)
    ana = user(db_session, "ana")
    sig = rand_sig(rng)
    _, a = version(db_session, ana, sig, stage="summarized")
    _, b = version(db_session, ana, sig, stage="chunked")  # B already finished its own check, unparked
    db_session.execute(
        DocumentVersion.__table__.update()
        .where(DocumentVersion.id == b.id)
        .values(created_at=a.created_at + __import__("datetime").timedelta(seconds=5))
    )
    db_session.refresh(a)
    db_session.refresh(b)
    got = find_near_duplicate(db_session, principal(ana), a, TH, STH, es=NoEs(), settings=_s())
    assert got is not None and got.id == b.id
    for stage in ("deduped", "embedded"):  # every stage after the check counts
        db_session.execute(
            DocumentVersion.__table__.update().where(DocumentVersion.id == b.id).values(stage=stage)
        )
        db_session.refresh(b)
        assert (
            find_near_duplicate(db_session, principal(ana), a, TH, STH, es=NoEs(), settings=_s()).id == b.id
        )
    db_session.execute(
        DocumentVersion.__table__.update().where(DocumentVersion.id == b.id).values(stage="parsed")
    )
    db_session.refresh(b)  # still mid-check: it will find A itself, so A ignores it
    assert find_near_duplicate(db_session, principal(ana), a, TH, STH, es=NoEs(), settings=_s()) is None


def _two_versions(db_session, store, name="dave"):
    from ingest_helpers import make_version

    owner = user(db_session, name)
    a, b = (make_version(db_session, store, owner) for _ in range(2))
    for v in (a, b):
        v.stage, v.is_latest = "summarized", True  # a first version is latest from intake on
    db_session.commit()
    return owner, a, b


def _dedupe(session_factory, settings, vid, text):
    from roracle.ingest import pipeline

    with session_factory() as s:
        ver = s.get(DocumentVersion, vid)
        d = s.get(Document, ver.document_id)
        out = pipeline._dedupe(
            s, ver, d, text, settings.model_copy(update={"embed_dims": 4}), session_factory
        )
        s.commit()
        return out


_TEXT: list[str] = []


def _text():
    """One fixed document text (docling_doc invents fresh unique words on every call)."""
    from ingest_helpers import docling_doc

    from roracle.ingest import pipeline

    if not _TEXT:
        _TEXT.append(pipeline.document_text(docling_doc(paras=12)))
    return _TEXT[0]


def test_two_simultaneous_copies_exactly_one_is_parked_the_one_that_decides_second(
    db_session, session_factory, settings, store, monkeypatch
):
    """Both finish their lock-free search before either decides; the lock section then serialises the decisions."""
    import threading

    from roracle.ingest import pipeline

    _, a, b = _two_versions(db_session, store)
    barrier, real, results = threading.Barrier(2, timeout=10), pipeline.find_near_duplicate, {}

    def find(*args, **kw):
        out = real(*args, **kw)
        barrier.wait()  # both searched (and found nothing) before any decision is written
        return out

    monkeypatch.setattr(pipeline, "find_near_duplicate", find)
    ts = [
        threading.Thread(
            target=lambda v=v: results.__setitem__(v.id, _dedupe(session_factory, settings, v.id, _text()))
        )
        for v in (a, b)
    ]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    assert sorted(results.values()) == [False, True]
    db_session.expire_all()
    parked = next(v for v in (a, b) if results[v.id])
    other = b if parked is a else a
    got = db_session.get(DocumentVersion, parked.id)
    assert (got.stage, got.near_dup_of) == ("review", other.id)
    assert db_session.get(DocumentVersion, other.id).stage == "deduped"
    assert db_session.scalar(select(func.count()).select_from(VersionLsh)) == 2 * 16  # both wrote their bands


def test_a_slow_search_never_holds_the_owner_lock(db_session, session_factory, settings, store, monkeypatch):
    """Bulk imports by one owner must not serialise on the (possibly 100 s) scan: B decides while A is still searching."""
    import threading

    from roracle.ingest import pipeline

    _, a, b = _two_versions(db_session, store, "erin")
    in_find, release, real, calls = threading.Event(), threading.Event(), pipeline.find_near_duplicate, []

    def find(*args, **kw):
        calls.append(args[2].id)
        if len(calls) == 1:  # A's search stalls
            in_find.set()
            release.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(pipeline, "find_near_duplicate", find)
    ta = threading.Thread(target=lambda: _dedupe(session_factory, settings, a.id, _text()))
    ta.start()
    in_find.wait(5)
    t0 = time.monotonic()
    assert _dedupe(session_factory, settings, b.id, _text()) is False  # B is not blocked by A's search
    assert time.monotonic() - t0 < 5 and ta.is_alive()
    release.set()
    ta.join(20)
    db_session.expire_all()
    assert db_session.get(DocumentVersion, a.id).stage == "review"  # A decides last: it sees B and is parked


def test_bands_minhash_and_decision_are_atomic_and_a_crash_leaves_nothing(
    db_session, session_factory, settings, store, monkeypatch
):
    from roracle.ingest import neardup, pipeline

    _, a, _b = _two_versions(db_session, store, "frank")
    real = neardup.write_bands

    def crash(*args, **kw):
        real(*args, **kw)
        raise RuntimeError("worker died between the band write and the commit")

    monkeypatch.setattr(pipeline, "write_bands", crash)
    with pytest.raises(RuntimeError):
        _dedupe(session_factory, settings, a.id, _text())
    db_session.expire_all()
    v = db_session.get(DocumentVersion, a.id)
    assert (v.stage, v.minhash) == ("summarized", None)  # nothing committed: not "deduped without bands"
    assert db_session.scalar(select(func.count()).select_from(VersionLsh)) == 0
    monkeypatch.setattr(pipeline, "write_bands", real)
    assert _dedupe(session_factory, settings, a.id, _text()) is False  # the retry decides normally
    db_session.expire_all()
    assert db_session.get(DocumentVersion, a.id).stage == "deduped"
    assert db_session.scalar(select(func.count()).select_from(VersionLsh)) == 16


def test_a_second_run_cannot_overwrite_the_first_decision(db_session, session_factory, settings, store):
    _, a, b = _two_versions(db_session, store, "gina")
    assert _dedupe(session_factory, settings, b.id, _text()) is False
    assert _dedupe(session_factory, settings, a.id, _text()) is True  # parked next to B
    for _ in range(
        2
    ):  # stale re-runs (lease lost / retried job): compare-and-set on stage='summarized' matches nothing
        assert _dedupe(session_factory, settings, a.id, _text()) is True
        assert _dedupe(session_factory, settings, b.id, _text()) is False
    db_session.expire_all()
    got = db_session.get(DocumentVersion, a.id)
    assert (got.stage, got.near_dup_of) == ("review", b.id)
    assert db_session.get(DocumentVersion, b.id).stage == "deduped"


def test_owner_lock_is_try_with_bounded_backoff(db_session, session_factory, settings, store, monkeypatch):
    from roracle.ingest import pipeline

    owner, a, _b = _two_versions(db_session, store, "hugo")
    monkeypatch.setattr(pipeline, "LOCK_TRIES", 3)
    monkeypatch.setattr(pipeline, "LOCK_SLEEP_S", 0.01)
    holder = session_factory()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"dedupe:{owner.id}"})
    try:
        with pytest.raises(pipeline.DedupeBusy):  # gives up instead of pinning a connection forever
            _dedupe(session_factory, settings, a.id, _text())
    finally:
        holder.rollback()
        holder.close()
    db_session.expire_all()
    assert db_session.get(DocumentVersion, a.id).stage == "summarized"  # nothing written


def test_a_stale_worker_cannot_decide(db_session, session_factory, settings, store):
    """Job fencing: the dedupe's own-session writes raise LeaseLost once the claim is gone."""
    import threading

    from roracle import queue

    _, a, _b = _two_versions(db_session, store, "ivy")
    jid = enqueue(db_session, "ingest", {"version_id": str(a.id)})
    db_session.commit()
    with session_factory() as s:
        queue.claim(s, ["ingest"], 60)
        s.commit()
    with session_factory() as s:
        first_token = s.get(Job, jid).claim_token
    with session_factory() as s:  # the job is requeued and reclaimed by someone else
        s.execute(text("UPDATE jobs SET lease_until = now() - interval '1 s'"))
        queue.requeue_expired(s)
        s.execute(text("UPDATE jobs SET run_after = now()"))
        s.commit()
    with session_factory() as s:
        queue.claim(s, ["ingest"], 60)
        s.commit()
    token = queue._current.set((jid, first_token, threading.Event()))  # we are the STALE worker
    try:
        with pytest.raises(queue.LeaseLost):
            _dedupe(session_factory, settings, a.id, _text())
    finally:
        queue._current.reset(token)
    db_session.expire_all()
    v = db_session.get(DocumentVersion, a.id)
    assert (v.stage, v.minhash) == ("summarized", None)


def test_failed_backfill_logs_and_counts_when_no_job_is_pending(db_session, caplog):
    from roracle.ingest import neardup
    from roracle.metrics import NEARDUP_EXHAUSTIVE

    before = NEARDUP_EXHAUSTIVE.labels("backfill_missing_or_failed")._value.get()
    neardup._ready = False
    db_session.execute(text_delete_marker())
    with caplog.at_level("WARNING"):
        assert not lsh_ready(db_session)
    assert "exhaustive scan" in caplog.text
    assert NEARDUP_EXHAUSTIVE.labels("backfill_missing_or_failed")._value.get() == before + 1
    enqueue(db_session, "lsh_backfill", {})  # a pending backfill is expected: no alarm
    neardup._ready = False
    caplog.clear()
    assert not lsh_ready(db_session)
    assert "exhaustive scan" not in caplog.text


def text_delete_marker():
    from sqlalchemy import delete

    from roracle.models import AppSetting

    return delete(AppSetting).where(AppSetting.key == "lsh_backfilled")
