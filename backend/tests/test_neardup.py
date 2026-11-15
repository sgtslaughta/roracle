import uuid

import pytest
from sqlalchemy import select

from roracle.auth import Principal
from roracle.ingest.neardup import find_near_duplicate, minhash, similarity
from roracle.models import Blob, Document, DocumentVersion, Job, User

TEXT = " ".join(f"word{i}" for i in range(200))
OTHER = " ".join(f"zebra{i}" for i in range(200))


def test_similarity():
    assert similarity(minhash(TEXT), minhash(TEXT.upper())) >= 0.99
    assert similarity(minhash(TEXT), minhash(OTHER)) < 0.2


def test_short_and_empty_text():
    assert minhash("") is None and minhash("  ... ") is None
    assert minhash("two words") is not None
    assert similarity(None, None) == 0.0
    assert similarity(minhash(""), minhash("")) == 0.0
    assert similarity(minhash("café über"), minhash("CAFÉ ÜBER")) == 1.0


def _user(db, sub):
    u = User(id=uuid.uuid4(), sub=sub, username=sub, groups=[])
    db.add(u)
    db.flush()
    return u


def _principal(u, admin=False):
    return Principal(user_id=u.id, sub=u.sub, username=u.username, groups=(), is_admin=admin)


def _doc(db, owner, text, visibility="everyone", sha=None, stage="searchable", **kw):
    sha = sha or uuid.uuid4().hex + uuid.uuid4().hex
    db.add(Blob(sha256=sha, size=1))
    d = Document(owner_id=owner.id, title="t", org_unit="/", visibility=visibility)
    db.add(d)
    db.flush()
    v = DocumentVersion(
        document_id=d.id, version=1, blob_sha256=sha, filename="f", content_type="x", size=1,
        stage=stage, minhash=minhash(text), **kw,
    )  # fmt: skip
    db.add(v)
    db.flush()
    return d, v


def test_finds_best_visible_match(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    _, existing = _doc(db_session, bob, TEXT)
    _doc(db_session, bob, OTHER)
    _, mine = _doc(db_session, ana, TEXT, stage="deduped")
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85).id == existing.id


def test_private_doc_of_other_user_never_matched(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    _doc(db_session, bob, TEXT, visibility="private")
    _, mine = _doc(db_session, ana, TEXT)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85) is None


def test_excludes_same_document_old_versions_and_empty(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    _, mine = _doc(db_session, ana, TEXT)
    _doc(db_session, bob, TEXT, is_latest=False)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85) is None
    _doc(db_session, bob, "")
    _, e2 = _doc(db_session, ana, "")
    assert find_near_duplicate(db_session, _principal(ana), e2, 0.85) is None


def _setup(db_session, client, as_ana, owner_of_match="ana"):
    client.get("/me", headers=as_ana)  # upserts ana
    ana = db_session.scalar(select(User).where(User.sub == "ana"))
    bob = _user(db_session, "bob")
    md, match = _doc(db_session, ana if owner_of_match == "ana" else bob, TEXT)
    ud, mine = _doc(db_session, ana, TEXT, stage="review", sha="b" * 64)
    mine.near_dup_of = match.id
    db_session.flush()
    return md, match, ud, mine


def _post(client, hdr, d, v, action):
    return client.post(f"/documents/{d.id}/versions/{v.id}/near-dup", json={"action": action}, headers=hdr)


def _jobs(db):
    return db.scalars(select(Job).where(Job.kind == "ingest")).all()


def test_new_version(db_session, client, as_ana):
    md, match, ud, mine = _setup(db_session, client, as_ana)
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 200, r.text
    db_session.refresh(mine)
    db_session.refresh(match)
    assert (mine.document_id, mine.version, mine.is_latest, mine.stage) == (md.id, 2, True, "deduped")
    assert mine.near_dup_of == match.id and match.is_latest is False
    assert db_session.get(Document, ud.id) is None
    assert [j.payload for j in _jobs(db_session)] == [{"version_id": str(mine.id)}]


def test_new_version_on_foreign_document_is_403(db_session, client, as_ana):
    _, _match, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 403
    assert r.json() == {"detail": "only the document owner can add versions"}
    db_session.refresh(mine)
    assert mine.stage == "review" and not _jobs(db_session)


def test_separate(db_session, client, as_ana):
    _, _, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    assert _post(client, as_ana, ud, mine, "separate").status_code == 200
    db_session.refresh(mine)
    assert mine.stage == "deduped" and mine.document_id == ud.id
    assert len(_jobs(db_session)) == 1


def test_cancel_and_refcount_floor(db_session, client, as_ana):
    _, _, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    assert _post(client, as_ana, ud, mine, "cancel").status_code == 200
    db_session.refresh(mine)
    db_session.refresh(ud)
    blob = db_session.get(Blob, mine.blob_sha256)
    assert mine.stage == "cancelled" and ud.deleted_at is not None
    assert blob.refcount == 0 and not _jobs(db_session)

    mine.stage = "review"
    db_session.flush()
    ud.deleted_at = None
    db_session.flush()
    assert _post(client, as_ana, ud, mine, "cancel").status_code == 200
    db_session.refresh(blob)
    assert blob.refcount == 0


def test_non_owner_404(db_session, client, as_ana, as_bob):
    _, _, ud, mine = _setup(db_session, client, as_ana)
    r = _post(client, as_bob, ud, mine, "cancel")
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    db_session.refresh(mine)
    assert mine.stage == "review"


@pytest.mark.parametrize("stage", ["searchable", "deduped"])
def test_wrong_stage_409(db_session, client, as_ana, stage):
    _, _, ud, mine = _setup(db_session, client, as_ana)
    mine.stage = stage
    db_session.flush()
    r = _post(client, as_ana, ud, mine, "separate")
    assert r.status_code == 409 and r.json() == {"detail": "not awaiting a decision"}


def test_invalid_action_422(db_session, client, as_ana):
    _, _, ud, mine = _setup(db_session, client, as_ana)
    assert _post(client, as_ana, ud, mine, "bogus").status_code == 422
