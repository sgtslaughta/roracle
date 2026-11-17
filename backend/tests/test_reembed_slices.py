"""Task 10.11: sliced parallel re-embed, bulk-friendly target settings, embedding concurrency."""
# ruff: noqa: F811

import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from reembed_support import FakeEs, FakeLlm, base, drain, es  # noqa: F401
from sqlalchemy import select

from roracle import reembed
from roracle.ingest.embed import embed
from roracle.models import Document, DocumentVersion, Job


@pytest.fixture
def corpus(db_session, document, blob):
    """16 versions whose ids start with every hex digit, 3 chunks each: 48 chunks spread over all slices."""
    ids = []
    for d in "0123456789abcdef":
        doc = Document(owner_id=document.owner_id, title="t", org_unit="/", visibility="private")
        db_session.add(doc)
        db_session.flush()
        v = DocumentVersion(
            id=uuid.UUID(f"{d}0000000-0000-4000-8000-000000000000"), document_id=doc.id, version=1,
            blob_sha256=blob.sha256, filename="a.pdf", content_type="x", size=1, stage="searchable", is_latest=True,
        )  # fmt: skip
        db_session.add(v)
        ids.append((str(v.id), doc.id))
    db_session.commit()
    return ids


def seed(es, document, ids, n=3):
    for vid, doc_id in ids:
        for k in range(n):
            es.put(f"{vid}:{k}", doc_id)


def jobs(db, **where):
    return [j for j in db.scalars(select(Job).where(Job.kind == "reembed").order_by(Job.id)) if all(
        j.payload.get(k) == v for k, v in where.items())]  # fmt: skip


@pytest.mark.parametrize("n", [1, 2, 4, 7, 16])
def test_slices_cover_every_chunk_exactly_once(n):
    ids = [f"{d}abc:0" for d in "0123456789abcdef"] + ["fffff:1", "00000:2"]
    hit = {cid: 0 for cid in ids}
    for i in range(n):
        q = reembed._slice_query({"i": i, "n": n})
        for cid in ids:
            if FakeEs._in_slice(cid, q):
                hit[cid] += 1
    assert set(hit.values()) == {1}


def test_start_enqueues_one_chain_per_slice_and_each_copies_only_its_range(
    db_session, session_factory, es, base, document, corpus
):
    s = base.model_copy(update={"reembed_slices": 4})
    seed(es, document, corpus)
    reembed.start(db_session, es, s, "new", 2)
    db_session.commit()
    first = jobs(db_session, phase="copy")
    assert [j.payload["slice"] for j in first] == [{"i": i, "n": 4} for i in range(4)]
    j = first[1]  # slice 1 = hex digits 4-7
    reembed.handle_reembed(
        db_session, j.payload, es=es, llm=FakeLlm(2), session_factory=session_factory, settings=s
    )
    assert {k.split(":")[0][0] for k in es.data["chunks-new-1"]} == set("4567")
    assert j.payload["total"] is None  # the job's own payload is a copy; the continuation carries the numbers


def test_cutover_waits_for_all_slices_and_everything_is_copied_once(
    db_session, session_factory, es, base, document, corpus
):
    s = base.model_copy(update={"reembed_slices": 4})
    seed(es, document, corpus)
    reembed.start(db_session, es, s, "new", 2)
    db_session.commit()
    llm = FakeLlm(2)
    for step in range(60):  # run slices one after another; watch the cutover appear only after the LAST one
        j = db_session.scalar(
            select(Job).where(Job.kind == "reembed", Job.status == "queued").order_by(Job.id)
        )
        if j is None:
            break
        if j.payload["phase"] == "cutover":
            assert all(
                x.status == "done" for x in jobs(db_session, phase="copy")
            )  # every copy slice had finished
        j.status = "done"
        db_session.flush()
        reembed.handle_reembed(
            db_session, j.payload, es=es, llm=llm, session_factory=session_factory, settings=s
        )
        db_session.commit()
    assert len(es.data["chunks-new-1"]) == 48  # all chunks, none twice (same _id would just overwrite)
    assert len(jobs(db_session, phase="cutover")) == 1  # exactly one cutover, enqueued by the last slice
    assert es.aliases["chunks"] == {"chunks-new-1"}
    assert (
        len([j for j in jobs(db_session, phase="catchup") if j.payload["round"] == 1]) == 4
    )  # catch-up per slice


def test_target_is_created_bulk_friendly_and_restored_before_the_alias_swap(
    db_session, session_factory, es, base, document, corpus
):
    s = base.model_copy(update={"es_refresh_interval": "7s", "es_replicas": 1})
    seed(es, document, corpus, 1)
    reembed.start(db_session, es, s, "new", 2)
    db_session.commit()
    drain(db_session, session_factory, es, FakeLlm(2), s)
    assert es.created["chunks-new-1"]["refresh_interval"] == reembed.COPY_REFRESH
    assert (
        es.created["chunks-new-1"]["number_of_replicas"] == 1
    )  # never 0: mutations hit the target all along
    assert es.index_settings["chunks-new-1"] == {"refresh_interval": "7s", "number_of_replicas": 1}
    assert "chunks-new-1" in es.refreshes and es.merges == ["chunks-new-1"]
    swap_index = next(i for i, c in enumerate(es.indices.alias_calls) if c)
    assert swap_index == 0  # restored (put_settings) happened in the cutover that did the single swap


def test_settings_are_restored_even_when_the_cutover_fails_afterwards(
    db_session, session_factory, es, base, document, corpus, monkeypatch
):
    seed(es, document, corpus, 1)
    reembed.start(db_session, es, base, "new", 2)
    db_session.commit()
    monkeypatch.setattr(
        es.indices, "update_aliases", lambda actions: (_ for _ in ()).throw(RuntimeError("es down"))
    )
    with pytest.raises(RuntimeError):
        drain(db_session, session_factory, es, FakeLlm(2), base)
    assert es.index_settings["chunks-new-1"]["refresh_interval"] == base.es_refresh_interval  # restored FIRST


def test_permanent_copy_failure_restores_the_target_too(
    db_session, session_factory, es, base, document, corpus, monkeypatch
):
    from roracle.queue import NonRetryable

    seed(es, document, corpus, 1)
    reembed.start(db_session, es, base, "new", 2)
    db_session.commit()
    j = jobs(db_session)[0]
    monkeypatch.setattr(reembed, "_copy", lambda *a, **k: (_ for _ in ()).throw(NonRetryable("dims")))
    with pytest.raises(NonRetryable):
        reembed.handle_reembed(
            db_session, j.payload, es=es, llm=FakeLlm(2), session_factory=session_factory, settings=base
        )
    assert es.index_settings["chunks-new-1"]["refresh_interval"] == base.es_refresh_interval


def test_a_page_is_one_bulk_without_refresh_and_verify_refreshes_only_when_it_must_fix_something(
    db_session, session_factory, es, base, document, corpus, monkeypatch
):
    seen = {}
    monkeypatch.setattr(
        reembed.helpers, "bulk", lambda client, actions, **kw: (seen.update(kw), (len(list(actions)), []))[1]
    )
    seed(es, document, corpus, 2)
    reembed.start(db_session, es, base, "new", 2)
    db_session.commit()
    j = jobs(db_session)[0]
    reembed.handle_reembed(
        db_session, j.payload, es=es, llm=FakeLlm(2), session_factory=session_factory, settings=base
    )
    assert seen["refresh"] is False and es.refreshes == []  # nothing to correct: no refresh at all


def test_embed_runs_requests_concurrently_and_keeps_order():
    live, peak, lock = [0], [0], threading.Lock()

    def create(model, input):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05 if input[0] == "t0" else 0.01)  # the first batch is the slowest
        with lock:
            live[0] -= 1
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[float(t[1:])]) for i, t in enumerate(input)]
        )

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    texts = [f"t{i}" for i in range(320)]
    out = embed(client, "m", texts, 1, batch=32, concurrency=4)
    assert [v[0] for v in out] == [float(i) for i in range(320)]  # input order despite completion order
    assert 2 <= peak[0] <= 4
    assert embed(client, "m", texts[:40], 1, batch=32, concurrency=1) == [[float(i)] for i in range(40)]
