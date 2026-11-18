"""F1: near-dup detection runs with the uploading principal's view (agent gate, never admin)."""

import uuid

import pytest
from conftest import bearer
from ingest_helpers import docling_doc, make_version
from sqlalchemy import select

from roracle import pats
from roracle.ingest import pipeline
from roracle.ingest.neardup import mark_lsh_ready, minhash, write_bands
from roracle.models import Document, DocumentVersion, Job, User
from roracle.models_labels import Label

pytestmark = pytest.mark.usefixtures("_blob_store_override")

_TEXT: list[str] = []


def _text():
    if not _TEXT:
        _TEXT.append(pipeline.document_text(docling_doc(paras=12)))
    return _TEXT[0]


def _user(db, sub, admin=False):
    u = User(id=uuid.uuid4(), sub=sub, username=sub, groups=[], is_admin=admin)
    db.add(u)
    db.flush()
    return u


def _match(db, store, owner, **doc_kw):
    v = make_version(db, store, owner, **doc_kw)
    mh = minhash(_text())
    db.query(DocumentVersion).filter_by(id=v.id).update({"stage": "searchable", "minhash": mh})
    write_bands(db, v.id, mh)
    db.commit()
    return v


def _candidate(db, store, owner):
    v = make_version(db, store, owner)
    v.stage = "summarized"
    db.commit()
    return v


def _dedupe(session_factory, settings, vid, agent):
    with session_factory() as s:
        ver = s.get(DocumentVersion, vid)
        d = s.get(Document, ver.document_id)
        cfg = settings.model_copy(update={"embed_dims": 4})
        out = pipeline._dedupe(s, ver, d, _text(), cfg, session_factory, agent=agent)
        s.commit()
        return out


@pytest.fixture(autouse=True)
def _ready(db_session):
    mark_lsh_ready(db_session)
    db_session.commit()


def test_admin_owner_agent_upload_does_not_match_other_users_private_paper(
    db_session, session_factory, settings, store
):
    admin, other = _user(db_session, "adm", admin=True), _user(db_session, "oth")
    _match(db_session, store, other, visibility="private")
    human = _candidate(db_session, store, admin)
    assert _dedupe(session_factory, settings, human.id, None) is True  # the admin's own view: a match
    agent = _candidate(db_session, store, admin)
    assert _dedupe(session_factory, settings, agent.id, {"groups": []}) is False
    db_session.expire_all()
    got = db_session.get(DocumentVersion, agent.id)
    assert (got.stage, got.near_dup_of) == ("deduped", None)


def test_agent_upload_does_not_match_block_agents_paper(db_session, session_factory, settings, store):
    owner = _user(db_session, "own")
    lab = Label(name="NoAgentF1", colour="#aabbcc", rank=7, block_agents=True)
    db_session.add(lab)
    db_session.flush()
    _match(db_session, store, owner, label_id=lab.id)
    agent = _candidate(db_session, store, owner)
    assert _dedupe(session_factory, settings, agent.id, {"groups": []}) is False
    human = _candidate(db_session, store, owner)
    assert _dedupe(session_factory, settings, human.id, None) is True


def test_pat_upload_marks_ingest_job_as_agent(client, db_session, ana):
    _, token = pats.create(db_session, ana, "t", ["read", "write"], 30)
    db_session.commit()
    r = client.post(
        "/documents", headers=bearer(token), files={"file": ("p.pdf", b"%PDF-1 f1", "application/pdf")}
    )
    assert r.status_code == 201, r.text
    job = db_session.scalar(
        select(Job).where(Job.kind == "ingest", Job.payload["version_id"].astext == r.json()["version_id"])
    )
    assert job.payload["agent"] == {"groups": ["/Mathematics/Spectral Theory"]}  # the PAT's group snapshot


def test_jwt_upload_job_has_no_agent_marker(client, db_session, as_ana):
    r = client.post("/documents", headers=as_ana, files={"file": ("p.pdf", b"%PDF-1 f1b", "application/pdf")})
    assert r.status_code == 201, r.text
    job = db_session.scalar(
        select(Job).where(Job.kind == "ingest", Job.payload["version_id"].astext == r.json()["version_id"])
    )
    assert "agent" not in job.payload


def test_agent_new_version_onto_hidden_foreign_match_is_409_not_403(client, db_session, as_ana):
    """The agent check runs before the owner check: a 403 would tell the agent the hidden match is someone else's."""
    from test_neardup import _post, _setup

    md, _match, ud, mine = _setup(db_session, client, as_ana, owner_of_match="bob")
    lab = Label(name="NoAgentF1b", colour="#aabbcc", rank=7, block_agents=True)
    db_session.add(lab)
    db_session.flush()
    md.label_id = lab.id
    _, token = pats.create(db_session, ud.owner_id, "t", ["read", "write"], 30)
    db_session.commit()
    r = _post(client, bearer(token), ud, mine, "new_version")
    assert r.status_code == 409 and r.json() == {"detail": "matched document no longer exists"}
