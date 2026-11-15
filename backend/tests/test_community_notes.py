import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_seed import FakeES, seed
from test_chat_api import cite, retr
from test_chat_retrieve import FakeES as ChatES
from test_chat_retrieve import FakeLLM
from test_search_api import hit

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import Principal
from roracle.chat.access import recheck
from roracle.chat.retrieve import Viewer, retrieve_for_chat
from roracle.models import Annotation, AnnotationReport, AnnotationShare, AnnotationVote, Chat, ChatMessage
from roracle.notes.community import community_notes, readable_note_ids

pytestmark = pytest.mark.usefixtures("_blob_store_override")


def note(db, d, v, author, *, body="a note", n=0, page=2, **kw):
    kw.setdefault("kind", "finding")
    kw.setdefault("visibility", "everyone")
    a = Annotation(
        document_id=d.id, version_id=v.id, author_id=author, body=body,
        anchor={"quote": "q", "prefix": "", "suffix": "", "page": page, "bbox": None} if page else None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=n), **kw,
    )  # fmt: skip
    db.add(a)
    db.flush()
    return a


def vote(db, a, users, value=1):
    for u in users:
        db.add(AnnotationVote(annotation_id=a.id, user_id=u, value=value))
    db.flush()


@pytest.fixture
def pr(ana):
    return Principal(ana, "ana", "researcher", (), False)


@pytest.fixture
def world(db_session, ana, bob):
    d, v = seed(db_session, bob, title="Doc")
    return d, v


def got(db, pr, d, pages=(2,)):
    return community_notes(db, pr, {str(d.id): set(pages)}).get(str(d.id), [])


def test_eligibility(db_session, pr, ana, bob, world):
    d, v = world
    ok = note(db_session, d, v, bob, body="ok")
    note(db_session, d, v, bob, body="private", visibility="private")
    note(db_session, d, v, bob, body="hidden", status="hidden")
    note(db_session, d, v, bob, body="stale", stale_version=2)
    note(db_session, d, v, bob, body="reply", parent_id=ok.id)
    low = note(db_session, d, v, bob, body="low")
    vote(db_session, low, [ana, bob], -1)  # (0+1)/(2+2)=0.25 < 0.4
    out = got(db_session, pr, d)
    assert [x["body"] for x in out] == ["ok"]
    assert set(out[0]) == {"id", "kind", "body", "author", "score", "page"}
    assert out[0]["page"] == 2 and out[0]["score"] == 0.5 and out[0]["author"] == "bob"


def test_reports_three_excludes(db_session, pr, ana, bob, world):
    from roracle.models import User

    d, v = world
    n = note(db_session, d, v, bob, body="spammy")
    us = [User(id=uuid.uuid4(), sub=f"s{i}", username=f"u{i}", email=f"u{i}@x.org") for i in range(3)]
    db_session.add_all(us)
    db_session.flush()
    db_session.add_all([AnnotationReport(annotation_id=n.id, user_id=u.id, reason="spam") for u in us[:2]])
    db_session.flush()
    assert len(got(db_session, pr, d)) == 1
    db_session.add(AnnotationReport(annotation_id=n.id, user_id=us[2].id, reason="spam"))
    db_session.flush()
    assert got(db_session, pr, d) == []


def test_unreadable_doc_and_shared(db_session, pr, ana, bob):
    d, v = seed(db_session, bob, title="Priv", visibility="private")
    note(db_session, d, v, bob)
    assert got(db_session, pr, d) == []
    d2, v2 = seed(db_session, bob, title="Pending", stage="embedded")
    note(db_session, d2, v2, bob)
    assert got(db_session, pr, d2) == []


def test_caps_and_ranking(db_session, pr, bob):
    docs = [seed(db_session, bob, title=f"D{i}") for i in range(4)]
    for d, v in docs:
        for i in range(3):
            note(db_session, d, v, bob, body=f"{d.title}-{i}", n=i, page=1 if i < 2 else 2)
    out = community_notes(db_session, pr, {str(d.id): {2} for d, _ in docs})
    assert all(len(x) <= 2 for x in out.values()) and sum(map(len, out.values())) == 6
    d0 = docs[0][0]
    assert out[str(d0.id)][0]["page"] == 2  # anchor page in passage pages ranks first


def test_body_truncated_and_batched(db_session, pr, bob, world):
    d, v = world
    note(db_session, d, v, bob, body="x" * 900)
    assert len(got(db_session, pr, d)[0]["body"]) == 500


def test_query_count_constant(db_session, pr, bob):
    from sqlalchemy import event

    docs = [seed(db_session, bob, title=f"D{i}") for i in range(5)]
    for d, v in docs:
        note(db_session, d, v, bob)
    n = []
    event.listen(db_session.get_bind(), "before_cursor_execute", lambda *a: n.append(1))
    community_notes(db_session, pr, {str(d.id): {2} for d, _ in docs})
    assert len(n) <= 4


def test_readable_note_ids(db_session, pr, ana, bob, world):
    d, v = world
    a = note(db_session, d, v, bob)
    b = note(db_session, d, v, bob, visibility="private")
    assert readable_note_ids(db_session, pr, [str(a.id), str(b.id), "junk"]) == {str(a.id)}
    assert readable_note_ids(db_session, pr, []) == set()


def test_shared_note_not_community(db_session, pr, ana, bob, world):
    d, v = world
    n = note(db_session, d, v, bob, visibility="shared")
    db_session.add(AnnotationShare(annotation_id=n.id, principal_kind="user", principal=str(ana)))
    db_session.flush()
    assert got(db_session, pr, d) == []  # R8: everyone only


def test_search_notes(client, as_ana, ana, bob, db_session):
    d, v = seed(db_session, bob, title="S")
    note(db_session, d, v, bob, body="hello")
    f = FakeES([hit(d, v)])
    client.app.dependency_overrides[get_es] = lambda: f
    client.app.dependency_overrides[get_llm] = lambda: FakeLLM()
    r = client.get("/search", headers=as_ana, params={"q": "alpha"}).json()["results"][0]
    assert [n["body"] for n in r["notes"]] == ["hello"]
    r = client.get("/search", headers=as_ana, params={"q": "alpha", "notes": "false"}).json()["results"][0]
    assert r["notes"] == []


def test_chat_retrieve_appends_notes(db_session, settings, pr, ana, bob):
    d, v = seed(db_session, bob, title="Chatdoc")
    n = note(db_session, d, v, bob, body="n" * 2000)
    from test_chat_retrieve import hit as chit

    es = ChatES([chit(d, v, pages=(2,))])
    args = (db_session, es, FakeLLM(), settings, pr, "alpha", Viewer(), "all", [], [])
    out = retrieve_for_chat(*args)
    assert [x.chunk_id for x in out][-1] == f"note:{n.id}" and len(out) == 2
    np = out[-1]
    assert np.note_id == str(n.id) and np.doc_id == str(d.id) and np.version_id == str(v.id)
    assert np.title == "Community note on: Chatdoc" and len(np.text) == 1200
    out = retrieve_for_chat(*args, include_notes=False)
    assert len(out) == 1 and out[0].note_id is None


def test_citation_item_note_id():
    from roracle.chat.answer import citation_item, retrieved_item
    from roracle.retrieval import Passage

    x = Passage("note:1", "d", "v", "t", "", [2], [], 0.0, note_id="1")
    assert citation_item(1, x)["note_id"] == "1" and retrieved_item(1, x)["note_id"] == "1"
    y = Passage("c", "d", "v", "t", "", [2], [], 0.0)
    assert "note_id" not in citation_item(1, y)


def test_recheck_fails_closed():
    items = [
        {"n": 1, "doc_id": "d", "version_id": "v", "note_id": "a"},
        {"n": 2, "doc_id": "d", "version_id": "v"},
    ]
    ok = {("d", "v")}
    out = recheck(items, ok, drop=False)
    assert out[0] == {"n": 1, "available": False} and out[1]["available"] is True
    assert recheck(items, ok, drop=False, notes={"a"})[0]["available"] is True


def test_chat_read_note_citation(client, as_ana, as_bob, ana, bob, db_session):
    d, v = seed(db_session, ana, title="P")
    vis = note(db_session, d, v, ana, body="v")
    gone = note(db_session, d, v, ana, body="g", visibility="private")
    chat = Chat(owner_id=ana, title="t")
    db_session.add(chat)
    db_session.flush()
    cs = [cite(1, d, v, note_id=str(vis.id)), cite(2, d, v, note_id=str(gone.id))]
    db_session.add(
        ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content="x",
            citations=cs,
            retrieved=[{**retr(1, d, v), "note_id": str(gone.id)}],
        )
    )
    from test_chat_api import share_user

    share_user(db_session, chat, bob)
    db_session.flush()
    m = client.get(f"/chats/{chat.id}", headers=as_bob).json()["messages"][0]
    assert [c["available"] for c in m["citations"]] == [True, False] and m["retrieved"] == []
    m = client.get(f"/chats/{chat.id}", headers=as_ana).json()["messages"][0]
    assert [c["available"] for c in m["citations"]] == [True, True]
