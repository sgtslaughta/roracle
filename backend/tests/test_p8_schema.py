import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from roracle.models import (
    Annotation,
    AnnotationReport,
    AnnotationShare,
    AnnotationVote,
    Document,
    DocumentVersion,
    Evidence,
    EvidenceReproduction,
    Proposal,
)


@pytest.fixture
def version(db_session, document, blob):
    v = DocumentVersion(
        document_id=document.id,
        version=1,
        blob_sha256=blob.sha256,
        filename="a.pdf",
        content_type="x",
        size=1,
    )
    db_session.add(v)
    db_session.flush()
    return v


def _note(user, document, version, **kw):
    kw.setdefault("kind", "comment")
    kw.setdefault("visibility", "everyone")
    return Annotation(document_id=document.id, version_id=version.id, author_id=user.id, body="b", **kw)


@pytest.fixture
def note(db_session, user, document, version):
    n = _note(user, document, version)
    db_session.add(n)
    db_session.flush()
    return n


def _bad(db_session, obj):
    db_session.add(obj)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_note_defaults(db_session, note):
    db_session.refresh(note)
    assert (note.status, note.agent_assisted, note.anchor, note.created_at) == (
        "visible",
        False,
        None,
        note.created_at,
    )
    assert note.created_at is not None


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "x"},
        {"visibility": "x"},
        {"status": "x"},
        {"kind": "finding", "relation_type": "extends", "target_document_id": "DOC"},
    ],
)
def test_note_checks(db_session, user, document, version, kw):
    if kw.get("target_document_id") == "DOC":
        kw["target_document_id"] = document.id
    _bad(db_session, _note(user, document, version, **kw))


def test_relation_consistency(db_session, user, document, version):
    _bad(db_session, _note(user, document, version, kind="relation"))


def test_relation_ok_and_bad_type(db_session, user, document, version):
    ok = _note(
        user, document, version, kind="relation", relation_type="extends", target_document_id=document.id
    )
    db_session.add(ok)
    db_session.flush()
    bad = _note(
        user, document, version, kind="relation", relation_type="bogus", target_document_id=document.id
    )
    _bad(db_session, bad)


def test_share_vote_report_checks(db_session, user, note):
    _bad(db_session, AnnotationShare(annotation_id=note.id, principal_kind="team", principal="x"))
    db_session.rollback()


@pytest.mark.parametrize("v", [0, 2])
def test_bad_vote(db_session, user, note, v):
    _bad(db_session, AnnotationVote(annotation_id=note.id, user_id=user.id, value=v))


def test_bad_report(db_session, user, note):
    _bad(db_session, AnnotationReport(annotation_id=note.id, user_id=user.id, reason="x"))


@pytest.mark.parametrize("kw", [{"kind": "x"}, {"status": "x"}])
def test_bad_proposal(db_session, user, kw):
    _bad(db_session, Proposal(user_id=user.id, **{"kind": "note", "payload": {}, **kw}))


def test_proposal_defaults(db_session, user):
    p = Proposal(user_id=user.id, kind="note", payload={})
    db_session.add(p)
    db_session.flush()
    db_session.refresh(p)
    assert (p.status, p.client, p.annotation_id) == ("pending", "", None)


@pytest.mark.parametrize("kw", [{"stance": "x"}, {"scan_status": "x"}, {"uri": "u", "blob_sha256": "a" * 64}])
def test_bad_evidence(db_session, user, note, blob, kw):
    kw = {"stance": "supports", **kw}
    _bad(db_session, Evidence(annotation_id=note.id, creator_id=user.id, crate={}, **kw))


def test_bad_reproduction(db_session, user, note):
    e = Evidence(annotation_id=note.id, creator_id=user.id, stance="supports", crate={})
    db_session.add(e)
    db_session.flush()
    db_session.refresh(e)
    assert (e.scan_status, e.filename) == ("none", "")
    _bad(db_session, EvidenceReproduction(evidence_id=e.id, user_id=user.id, outcome="x"))


def test_delete_document_cascades(db_session, user, document, version, note):
    e = Evidence(annotation_id=note.id, creator_id=user.id, stance="refutes", crate={})
    db_session.add_all(
        [
            e,
            AnnotationShare(annotation_id=note.id, principal_kind="user", principal="bob"),
            AnnotationVote(annotation_id=note.id, user_id=user.id, value=1),
            AnnotationReport(annotation_id=note.id, user_id=user.id, reason="spam"),
        ]
    )
    db_session.flush()
    db_session.add(EvidenceReproduction(evidence_id=e.id, user_id=user.id, outcome="failed"))
    db_session.flush()
    db_session.execute(delete(DocumentVersion).where(DocumentVersion.id == version.id))
    db_session.execute(delete(Document).where(Document.id == document.id))
    for m in (Annotation, AnnotationShare, AnnotationVote, AnnotationReport, Evidence, EvidenceReproduction):
        assert db_session.scalar(select(func.count()).select_from(m)) == 0


def test_migration_roundtrip(session_factory, settings):
    from alembic import command
    from conftest import _alembic_config

    cfg = _alembic_config()
    command.downgrade(cfg, "0007")
    with session_factory() as s:
        assert s.scalar(text("select to_regclass('annotations')")) is None
    command.upgrade(cfg, "head")
    with session_factory() as s:
        assert s.scalar(text("select to_regclass('evidence')")) is not None
