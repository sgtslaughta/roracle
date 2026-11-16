"""Cutover vs stale workers (S5, R2-I): `chunks` follows the Postgres-active index, never a stale ensure_index."""
# ruff: noqa: F811

import pytest
from test_reembed import SRC, FakeLlm, base, drain, es, vid  # noqa: F401

from roracle import reembed, search_index

TARGET = "chunks-new-model-1"


@pytest.fixture
def es2(es):
    es.indices.get_mapping = lambda index: {
        index: {"mappings": {"properties": search_index._mapping(2)["properties"]}}
    }
    return es


def _stale_ensure(es, sf, base):  # a worker still on the old model/index
    search_index.ensure_index(es, "old", 2, name=SRC, session_factory=sf, settings=base)


def test_stale_ensure_index_does_not_move_read_alias_after_cutover(
    db_session,
    session_factory,
    es2,
    base,
    document,
    vid,
):
    db_session.commit()
    es2.put(f"{vid}:0", document.id)
    reembed.start(db_session, es2, base, "new-model", 2)
    db_session.commit()
    drain(db_session, session_factory, es2, FakeLlm(2), base)
    assert es2.aliases["chunks"] == {TARGET}
    _stale_ensure(es2, session_factory, base)
    assert es2.aliases["chunks"] == {TARGET} and SRC in es2.aliases["chunks-all"]


def test_ensure_index_moves_read_alias_to_the_active_index(db_session, es2, session_factory, base):
    es2.aliases["chunks"] = set()
    _stale_ensure(es2, session_factory, base)  # SRC is the PG/env-active index here
    assert es2.aliases["chunks"] == {SRC}


def test_read_alias_is_target_with_racing_old_ingest(
    db_session,
    session_factory,
    es2,
    base,
    document,
    vid,
    monkeypatch,
):
    db_session.commit()
    es2.put(f"{vid}:0", document.id)
    seen = {}
    real = reembed.clear_summary_embeddings

    def slow_clear(sf):  # the slow cleanup: an old-settings ingest calls ensure_index(source) meanwhile
        real(sf)
        _stale_ensure(es2, sf, base)
        seen["during"] = set(es2.aliases["chunks"])

    monkeypatch.setattr(reembed, "clear_summary_embeddings", slow_clear)
    reembed.start(db_session, es2, base, "new-model", 2)
    db_session.commit()
    drain(db_session, session_factory, es2, FakeLlm(2), base)
    assert seen["during"] == {SRC}  # swap had not happened yet: slow work runs first
    assert es2.aliases["chunks"] == {TARGET}
    _stale_ensure(es2, session_factory, base)
    assert es2.aliases["chunks"] == {TARGET}


def test_catchup_fails_when_read_alias_drifted(
    db_session,
    session_factory,
    es2,
    base,
    document,
    vid,
):
    db_session.commit()
    es2.put(f"{vid}:0", document.id)
    reembed.start(db_session, es2, base, "new-model", 2)
    db_session.commit()
    es2.indices.update_aliases = lambda actions: None  # a swap that never took effect
    with pytest.raises(reembed.AliasMismatch):
        drain(db_session, session_factory, es2, FakeLlm(2), base)
