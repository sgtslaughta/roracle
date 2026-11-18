"""F4: a proposal-approved document label change reaches Elasticsearch before the approval returns."""

import json

import pytest
from api_seed import FakeES, seed
from conftest import bearer
from sqlalchemy import select
from test_labels_notes import lprop, mk

from roracle.api.routes.library import get_es
from roracle.models import Job, Proposal

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class UES(FakeES):
    fail = False

    def update_by_query(self, **kw):
        if self.fail:
            raise RuntimeError("es down")
        self.calls.append(kw)
        return {}


@pytest.fixture
def es(app):
    f = UES()
    app.dependency_overrides[get_es] = lambda: f
    return f


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


@pytest.fixture
def doc(db_session, ana):
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    return d


def _synced(es, d, label):
    return any(
        str(d.id) in json.dumps(c, default=str) and label in json.dumps(c, default=str) for c in es.calls
    )


def test_approve_syncs_label_to_es_before_returning(client, as_ana, ana, admin, doc, es, db_session):
    hi = mk(client, admin, "HighF4", 6, block_agents=True)
    row = lprop(db_session, ana, doc, hi)
    assert client.post(f"/proposals/{row.id}/approve", headers=as_ana).status_code == 200
    assert _synced(es, doc, hi)
    kinds = db_session.scalars(select(Job.kind).where(Job.payload["doc_id"].astext == str(doc.id))).all()
    assert "acl_sync" in kinds  # the job stays as the retry


def test_bulk_approve_syncs_label_to_es(client, as_ana, ana, admin, doc, es, db_session):
    hi = mk(client, admin, "HighF4b", 6, block_agents=True)
    row = lprop(db_session, ana, doc, hi)
    r = client.post("/proposals/bulk-approve", json={"ids": [str(row.id)]}, headers=as_ana)
    assert r.status_code == 200 and r.json()["approved"], r.text
    assert _synced(es, doc, hi)


def test_es_failure_rolls_back_the_approval(client, as_ana, ana, admin, doc, es, db_session):
    hi = mk(client, admin, "HighF4c", 6, block_agents=True)
    row = lprop(db_session, ana, doc, hi)
    es.fail = True
    r = client.post(f"/proposals/{row.id}/approve", headers=as_ana)
    assert r.status_code == 503
    db_session.expire_all()
    assert db_session.get(Proposal, row.id).status == "pending"
    assert str(db_session.get(type(doc), doc.id).label_id) != hi
