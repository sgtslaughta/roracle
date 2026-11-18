import uuid
from datetime import UTC, datetime

import pytest

from roracle.acl_sync import handle_acl_sync


class _Indices:
    def put_mapping(self, **_):
        return {}


class StubES:
    def __init__(self, fail=False):
        self.fail, self.deleted, self.updated = fail, [], []
        self.indices = _Indices()

    def delete_by_query(self, *, index, query, **_):
        if self.fail:
            raise ConnectionError("es down")
        self.deleted.append(query)
        return {}

    def update_by_query(self, **kw):
        self.updated.append(kw)
        return {}


def _run(db_session, session_factory, doc_id, es):
    handle_acl_sync(db_session, {"doc_id": str(doc_id)}, es=es, session_factory=session_factory)


def test_acl_sync_deletes_chunks_of_deleted_doc(db_session, session_factory, document):
    document.deleted_at = datetime.now(UTC)
    db_session.commit()
    es = StubES()
    _run(db_session, session_factory, document.id, es)
    assert es.deleted == [{"term": {"doc_id": str(document.id)}}]
    assert es.updated == []


def test_acl_sync_delete_failure_raises_for_retry(db_session, session_factory, document):
    document.deleted_at = datetime.now(UTC)
    db_session.commit()
    with pytest.raises(ConnectionError):
        _run(db_session, session_factory, document.id, StubES(fail=True))


def test_live_doc_still_syncs_acl(db_session, session_factory, document):
    db_session.commit()
    es = StubES()
    _run(db_session, session_factory, document.id, es)
    assert es.deleted == [] and len(es.updated) == 1


def test_purged_doc_is_noop(db_session, session_factory):
    es = StubES()
    _run(db_session, session_factory, uuid.uuid4(), es)
    assert es.deleted == []
