"""Regression tests: latest-version metadata (I4/M1), evidence refcounts (I5), draft GET cost + payloads (I7/M9)."""

from api_seed import seed
from sqlalchemy import select

from roracle.auth import Principal
from roracle.ingest.intake import add_document, add_version
from roracle.models import DocumentVersion, User
from roracle.storage import stage
from roracle.writing import build

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


# --- T3: I4 / M1 -------------------------------------------------------------------------------
def test_new_versions_are_not_latest_until_ingest(db_session, ana, store, staged):
    doc, v1 = add_document(
        db_session, store, staged, owner_id=ana, org_unit="/", visibility="private", filename="a.pdf",
        content_type="application/pdf",
    )  # fmt: skip
    v2 = add_version(
        db_session, store, stage(__import__("io").BytesIO(b"two"), 100), doc, filename="a.pdf",
        content_type="application/pdf",
    )  # fmt: skip
    assert v1.is_latest and not v2.is_latest


def test_load_refs_ignores_unsearchable_version_even_if_flagged(db_session, bob, ana):
    d, v1 = seed(db_session, ana, title=None, authors=("Public",))
    _v2(db_session, d, v1, "rejected", authors=["Secret Author"], year=1999, title="Secret v2 title")
    assert db_session.scalar(
        select(DocumentVersion.is_latest).where(DocumentVersion.version == 2)
    )  # legacy rows
    refs = build.load_refs(db_session, _principal(db_session, bob), f"See [@doc:{d.id}].", 50)
    assert [r.authors for r in refs] == [["Public"]] and "Secret" not in refs[0].title


def test_load_refs_picks_highest_searchable_version(db_session, bob, ana):
    d, v1 = seed(db_session, ana, title=None, authors=("Old",))
    _v2(db_session, d, v1, "searchable", authors=["New"])  # both flagged latest
    refs = build.load_refs(db_session, _principal(db_session, bob), f"See [@doc:{d.id}].", 50)
    assert refs[0].authors == ["New"]


def test_owner_detail_ignores_rejected_newer_version(client, as_ana, ana, db_session):
    d, v1 = seed(db_session, ana, visibility="everyone")
    v2 = _v2(db_session, d, v1, "rejected")
    assert v2.is_latest  # legacy rows: both flagged
    db_session.commit()
    j = client.get(f"/documents/{d.id}", headers=as_ana).json()
    assert j["stage"] == "searchable"
