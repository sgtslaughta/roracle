"""Regression tests: latest-version metadata (I4/M1), evidence refcounts (I5), draft GET cost + payloads (I7/M9)."""

import json
import uuid

import pytest
from api_seed import seed
from sqlalchemy import select

from roracle.auth import Principal
from roracle.models import Blob, DocumentVersion, Evidence, User

PROV = {
    "code": {"repo": "https://git.example.org/x.git", "commit": "abc1234"},
    "environment": {"image_digest": "sha256:" + "a" * 64},
    "inputs": [{"name": "in.csv", "sha256": "b" * 64}],
    "outputs": [{"name": "out.csv"}],
    "parameters": {"lr": 0.1},
    "seed": 42,
    "agent": {"client": "c", "model": "m"},
    "started_at": "2026-01-01T00:00:00Z",
    "ended_at": "2026-01-01T01:00:00Z",
    "stance": "supports",
}


def _principal(db, uid):
    u = db.get(User, uid)
    return Principal(u.id, u.sub, u.username, (), False)


def _v2(db, d, v1, stage_, **meta):
    v2 = DocumentVersion(
        document_id=d.id, version=2, blob_sha256=v1.blob_sha256, filename="p.pdf",
        content_type="application/pdf", size=1, stage=stage_, meta=meta,
    )  # fmt: skip
    db.add(v2)
    db.flush()
    return v2


# --- T4: I5 ------------------------------------------------------------------------------------
@pytest.mark.usefixtures("_blob_store_override")
def test_note_delete_releases_evidence_blobs_with_replies(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    nid = client.post(
        f"/documents/{d.id}/annotations", json={"body": "hi", "kind": "finding"}, headers=as_ana
    ).json()["id"]
    client.post(f"/annotations/{nid}/replies", json={"body": "re"}, headers=as_ana)  # replies cascade too
    for target in (nid, nid):
        r = client.post(
            f"/annotations/{target}/evidence", headers=as_ana, data={"meta": json.dumps(PROV)},
            files={"file": ("x.bin", uuid.uuid4().bytes, "application/octet-stream")},
        )  # fmt: skip
        assert r.status_code == 201, r.text
    shas = list(db_session.scalars(select(Evidence.blob_sha256)))
    assert len(shas) == 2
    assert client.delete(f"/annotations/{nid}", headers=as_ana).status_code == 204
    db_session.expire_all()
    assert db_session.scalar(select(Evidence)) is None
    assert [db_session.get(Blob, s).refcount for s in shas] == [0, 0]
