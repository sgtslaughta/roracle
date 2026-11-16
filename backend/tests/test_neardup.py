import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from roracle.auth import Principal
from roracle.ingest.embed import to_bytes
from roracle.ingest.neardup import cosine, find_near_duplicate, minhash, similarity
from roracle.models import Blob, Document, DocumentVersion, Job, User

TEXT = " ".join(f"word{i}" for i in range(200))
OTHER = " ".join(f"zebra{i}" for i in range(200))


def test_similarity():
    assert similarity(minhash(TEXT), minhash(TEXT.upper())) >= 0.99
    assert similarity(minhash(TEXT), minhash(OTHER)) < 0.2


def test_signature_bytes_are_pinned():
    sig = minhash("the quick brown fox jumps over the lazy dog again")
    assert len(sig) == 128 * 8
    assert sig[:16].hex() == "6269a7e91a8cce17d8bb82ccc19c6f03"


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
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97).id == existing.id


def test_private_doc_of_other_user_never_matched(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    _doc(db_session, bob, TEXT, visibility="private")
    _, mine = _doc(db_session, ana, TEXT)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97) is None


def test_excludes_same_document_old_versions_and_empty(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    _, mine = _doc(db_session, ana, TEXT)
    _doc(db_session, bob, TEXT, is_latest=False)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97) is None
    _doc(db_session, bob, "")
    _, e2 = _doc(db_session, ana, "")
    assert find_near_duplicate(db_session, _principal(ana), e2, 0.85, 0.97) is None


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
    mine.ingest_attempts = 4
    db_session.flush()
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 200, r.text
    db_session.refresh(mine)
    db_session.refresh(match)
    assert (mine.document_id, mine.version, mine.is_latest, mine.stage) == (md.id, 2, True, "deduped")
    # the old version stays latest until the new one is searchable (the index step demotes it)
    assert mine.near_dup_of == match.id and match.is_latest is True
    assert mine.ingest_attempts == 0
    assert db_session.get(Document, ud.id) is None
    assert [j.payload for j in _jobs(db_session)] == [{"version_id": str(mine.id)}]


def test_new_version_on_foreign_document_is_403(db_session, client, as_ana):
    _, _match, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 403
    assert r.json() == {"detail": "only the document owner can add versions"}
    db_session.refresh(mine)
    assert mine.stage == "review" and not _jobs(db_session)


def test_foreign_deleted_match_is_403_not_409(db_session, client, as_ana):
    md, _match, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    md.deleted_at = datetime.now(UTC)
    db_session.flush()
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 403


def test_own_deleted_match_is_409(db_session, client, as_ana):
    md, _match, ud, mine = _setup(db_session, client, as_ana)
    md.deleted_at = datetime.now(UTC)
    db_session.flush()
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 409 and r.json() == {"detail": "matched document no longer exists"}


def test_separate(db_session, client, as_ana):
    _, _, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    mine.ingest_attempts = 4
    db_session.flush()
    assert _post(client, as_ana, ud, mine, "separate").status_code == 200
    db_session.refresh(mine)
    assert mine.stage == "deduped" and mine.document_id == ud.id and mine.ingest_attempts == 0
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


def test_new_version_survives_foreign_library_rows(db_session, client, as_ana):
    from roracle.models import Collection, CollectionItem, Tag, Vote

    md, _, ud, mine = _setup(db_session, client, as_ana)
    bob = db_session.scalar(select(User).where(User.sub == "bob"))
    c = Collection(owner_id=bob.id, name="mine")
    db_session.add(c)
    db_session.flush()
    db_session.add_all(
        [
            Tag(document_id=ud.id, tag="x"),
            Tag(document_id=ud.id, tag="shared"),
            Tag(document_id=md.id, tag="shared"),
            CollectionItem(collection_id=c.id, document_id=ud.id),
            Vote(user_id=bob.id, document_id=ud.id, value=1),
            Vote(user_id=bob.id, document_id=md.id, value=-1, reason="superseded", superseded_by=ud.id),
        ]
    )
    db_session.flush()
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert sorted(db_session.scalars(select(Tag.tag).where(Tag.document_id == md.id))) == ["shared", "x"]
    assert db_session.scalars(select(CollectionItem.document_id)).all() == [md.id]
    assert db_session.scalar(select(Vote.superseded_by)) is None
    assert db_session.scalars(select(Vote.document_id)).all() == [md.id]
    from roracle.models import Job

    assert [j.payload for j in db_session.scalars(select(Job).where(Job.kind == "acl_sync"))] == [
        {"doc_id": str(md.id)}
    ]


def test_merge_caps_tags_at_max(db_session, client, as_ana):
    from roracle.api.routes.library import MAX_TAGS
    from roracle.models import Tag

    md, _, ud, mine = _setup(db_session, client, as_ana)
    db_session.add_all([Tag(document_id=md.id, tag=f"a{i:02}") for i in range(40)])
    db_session.add_all([Tag(document_id=ud.id, tag=f"b{i:02}") for i in range(30)])
    db_session.flush()
    assert _post(client, as_ana, ud, mine, "new_version").status_code == 200
    db_session.expire_all()
    tags = sorted(db_session.scalars(select(Tag.tag).where(Tag.document_id == md.id)))
    assert len(tags) == MAX_TAGS and tags[:40] == [f"a{i:02}" for i in range(40)]
    assert tags[40:] == [f"b{i:02}" for i in range(10)]


def test_cosine():
    a, b = to_bytes([1.0, 0.0]), to_bytes([0.0, 1.0])
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine(a, b) == 0.0
    assert cosine(a, to_bytes([1.0, 0.0, 0.0])) == 0.0
    assert cosine(None, a) == cosine(b"", a) == cosine(to_bytes([0.0, 0.0]), a) == 0.0


def test_summary_vector_matches_despite_different_text(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    vec = to_bytes([0.3, 0.4, 0.5])
    _, existing = _doc(db_session, bob, OTHER, summary_embedding=vec)
    _, mine = _doc(db_session, ana, TEXT, summary_embedding=vec)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97).id == existing.id


def test_summary_only_candidate_and_version(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    vec = to_bytes([1.0, 2.0])
    _, existing = _doc(db_session, bob, "", summary_embedding=vec)
    _, mine = _doc(db_session, ana, TEXT, summary_embedding=vec)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97).id == existing.id
    _, no_min = _doc(db_session, ana, "", summary_embedding=vec)
    assert find_near_duplicate(db_session, _principal(ana), no_min, 0.85, 0.97).id == existing.id
    _, neither = _doc(db_session, ana, "")
    assert find_near_duplicate(db_session, _principal(ana), neither, 0.85, 0.97) is None


def test_invisible_doc_with_identical_vector_not_matched(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    vec = to_bytes([0.3, 0.4])
    _doc(db_session, bob, OTHER, visibility="private", summary_embedding=vec)
    _, mine = _doc(db_session, ana, TEXT, summary_embedding=vec)
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97) is None


def test_dissimilar_vectors_not_matched(db_session):
    ana, bob = _user(db_session, "ana"), _user(db_session, "bob")
    _doc(db_session, bob, OTHER, summary_embedding=to_bytes([1.0, 0.0]))
    _, mine = _doc(db_session, ana, TEXT, summary_embedding=to_bytes([0.0, 1.0]))
    assert find_near_duplicate(db_session, _principal(ana), mine, 0.85, 0.97) is None


def test_new_version_repoints_superseded_votes(db_session, client, as_ana):
    from roracle.models import Vote

    md, _, ud, mine = _setup(db_session, client, as_ana)
    bob = db_session.scalar(select(User).where(User.sub == "bob"))
    other, _ = _doc(db_session, bob, "other text", sha="c" * 64)
    db_session.add_all(
        [
            Vote(user_id=bob.id, document_id=other.id, value=-1, reason="superseded", superseded_by=ud.id),
            Vote(user_id=bob.id, document_id=md.id, value=-1, reason="superseded", superseded_by=ud.id),
        ]
    )
    db_session.flush()
    assert _post(client, as_ana, ud, mine, "new_version").status_code == 200
    db_session.expire_all()
    moved = db_session.scalar(select(Vote).where(Vote.document_id == other.id))
    assert (moved.superseded_by, moved.reason) == (md.id, "superseded")
    selfref = db_session.scalar(select(Vote).where(Vote.document_id == md.id))
    assert (selfref.superseded_by, selfref.reason) == (None, None)


def _with_v2(db_session, client, as_ana):
    """Ana's doc X (searchable v1 + v2 in review, near-dup of Y)."""
    md, match, xd, v2 = _setup(db_session, client, as_ana)
    v2.version, v2.is_latest = 2, False
    db_session.add(Blob(sha256="c" * 64, size=1))
    db_session.flush()
    v1 = DocumentVersion(
        document_id=xd.id, version=1, blob_sha256="c" * 64, filename="f", content_type="x", size=1,
        stage="searchable", is_latest=True,
    )  # fmt: skip
    db_session.add(v1)
    db_session.flush()
    return md, match, xd, v1, v2


def test_cancel_later_version_keeps_doc_and_v1(db_session, client, as_ana):
    _, _, xd, v1, v2 = _with_v2(db_session, client, as_ana)
    assert _post(client, as_ana, xd, v2, "cancel").status_code == 200
    db_session.expire_all()
    assert v2.stage == "cancelled" and v1.stage == "searchable"
    assert xd.deleted_at is None
    assert client.get(f"/documents/{xd.id}", headers=as_ana).status_code == 200


def test_new_version_of_later_version_moves_only_that_version(db_session, client, as_ana):
    md, _, xd, v1, v2 = _with_v2(db_session, client, as_ana)
    r = _post(client, as_ana, xd, v2, "new_version")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert (v2.document_id, v2.version) == (md.id, 2)
    assert v1.document_id == xd.id and db_session.get(Document, xd.id) is not None


def _draft(db_session, xd, owner_id):
    from roracle.models_writing import Draft, DraftVersion

    db_session.add(Draft(document_id=xd.id, status="submitted"))
    db_session.flush()
    db_session.add(DraftVersion(document_id=xd.id, version=1, content="# hi", author_id=owner_id))
    db_session.flush()


def test_new_version_on_published_draft_is_409_and_keeps_draft(db_session, client, as_ana):
    from roracle.models_writing import Draft

    _, _, ud, mine = _setup(db_session, client, as_ana)
    _draft(db_session, ud, ud.owner_id)
    r = _post(client, as_ana, ud, mine, "new_version")
    assert r.status_code == 409, r.text
    db_session.expire_all()
    assert db_session.get(Draft, ud.id) is not None and mine.document_id == ud.id
    assert mine.stage == "review"


def test_cancel_on_published_draft_keeps_doc(db_session, client, as_ana):
    _, _, ud, mine = _setup(db_session, client, as_ana)
    _draft(db_session, ud, ud.owner_id)
    assert _post(client, as_ana, ud, mine, "cancel").status_code == 200
    db_session.expire_all()
    assert mine.stage == "cancelled" and ud.deleted_at is None


def test_new_version_keeps_owner_notes(db_session, client, as_ana):
    from roracle.models import Annotation

    md, _, ud, mine = _setup(db_session, client, as_ana)
    r = client.post(f"/documents/{ud.id}/annotations", headers=as_ana, json={"body": "my note"})
    assert r.status_code == 201, r.text
    other = db_session.get(Document, md.id)
    db_session.add(
        Annotation(
            document_id=md.id, version_id=match_id(db_session, md), author_id=ud.owner_id, kind="relation",
            body="r", relation_type="extends", target_document_id=ud.id, visibility="private",
        )
    )  # fmt: skip
    db_session.flush()
    assert other is not None
    assert _post(client, as_ana, ud, mine, "new_version").status_code == 200
    db_session.expire_all()
    notes = db_session.scalars(select(Annotation)).all()
    assert len(notes) == 2 and {n.document_id for n in notes} == {md.id}
    assert all(n.target_document_id in (None, md.id) for n in notes)


def match_id(db, doc):
    return db.scalar(select(DocumentVersion.id).where(DocumentVersion.document_id == doc.id))
