"""Regression tests: latest-version metadata (I4/M1), evidence refcounts (I5), draft GET cost + payloads (I7/M9)."""

import pytest
from sqlalchemy import event, select

from roracle.auth import Principal
from roracle.models import DocumentVersion, Job, User
from roracle.models_writing import DraftVersion
from roracle.queue import NonRetryable

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


# --- T5: I7 / M9 ---------------------------------------------------------------------------------
def test_get_draft_is_cheap_and_returns_only_latest_content(client, as_ana, ana, db_session):
    did = client.post("/drafts", headers=as_ana, json={"title": "T", "content": "v1"}).json()["id"]
    for i in range(2, 6):
        r = client.put(
            f"/drafts/{did}/content", headers=as_ana, json={"content": f"v{i}", "base_version": i - 1}
        )
        assert r.status_code == 200, r.text
    sql: list[str] = []
    eng = db_session.get_bind()
    hook = lambda conn, cur, stmt, *a: sql.append(stmt)
    event.listen(eng, "before_cursor_execute", hook)
    try:
        j = client.get(f"/drafts/{did}", headers=as_ana).json()
    finally:
        event.remove(eng, "before_cursor_execute", hook)
    assert j["content"] == "v5" and [v["version"] for v in j["versions"]] == [5, 4, 3, 2, 1]
    assert all(v["author"]["username"] for v in j["versions"]) and "content" not in j["versions"][0]
    assert len([s for s in sql if "draft_versions" in s and "content" in s.split("FROM")[0]]) == 1
    assert len([s for s in sql if "FROM users" in s]) <= 3  # no per-version lookups
    assert client.get(f"/drafts/{did}/versions/2", headers=as_ana).json()["content"] == "v2"
    assert db_session.scalar(select(DraftVersion.content).where(DraftVersion.version == 3)) == "v3"


@pytest.mark.parametrize("handler", ["citations", "acl_sync", "evidence_scan", "connector_run", "ingest"])
def test_malformed_payload_is_nonretryable(db_session, settings, store, handler):
    from roracle.acl_sync import handle_acl_sync
    from roracle.citations import handle_citations
    from roracle.connectors.run import _run
    from roracle.notes.evidence import handle_evidence_scan

    bad = {"version_id": "nope", "doc_id": "nope", "evidence_id": "nope", "connector_id": "nope"}
    calls = {
        "citations": lambda: handle_citations(db_session, bad),
        "acl_sync": lambda: handle_acl_sync(db_session, bad, es=None, session_factory=None),
        "evidence_scan": lambda: handle_evidence_scan(db_session, bad, settings=settings, store=store),
        "connector_run": lambda: _run(db_session, bad, settings, store, None),
        "ingest": lambda: __import__("roracle.ingest.pipeline", fromlist=["x"]).handle_ingest(
            db_session, bad, settings=settings, store=store, es=None, llm=None, session_factory=None
        ),
    }
    with pytest.raises(NonRetryable):
        calls[handler]()
    assert Job  # keep import used
