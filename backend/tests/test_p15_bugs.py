"""P15 T8: quota ignores deleted papers, admin retry resets `released`, router LLM runs outside a DB transaction."""

from types import SimpleNamespace

import pytest
from conftest import bearer

from roracle.auth import Principal
from roracle.chat.global_answer import route_question
from roracle.db import RELEASE_BEFORE_IO
from roracle.models import Job

LONG = "what do the experiments in these studies say about how that approach behaves under noise"


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.mark.usefixtures("_blob_store_override")
def test_quota_ignores_deleted(client, adm, as_ana):
    up = lambda data: client.post(
        "/documents", headers=as_ana, files={"file": ("a.pdf", data, "application/pdf")}
    )
    client.put("/admin/settings", json={"upload_quota_bytes": 9}, headers=adm)
    a = up(b"12345678")
    assert a.status_code == 201
    assert up(b"abcdefgh").status_code == 413
    assert client.delete(f"/documents/{a.json()['document_id']}", headers=as_ana).status_code == 204
    assert up(b"abcdefgh").status_code == 201


def test_retry_resets_released(client, adm, db_session):
    j = Job(kind="x", payload={}, status="failed", attempts=3, released=5)
    k = Job(kind="x", payload={}, status="failed", attempts=3, released=5)
    db_session.add_all([j, k])
    db_session.commit()
    assert client.post(f"/admin/jobs/{j.id}/retry", headers=adm).status_code == 200
    db_session.refresh(j)
    assert (j.attempts, j.released) == (0, 0)
    assert client.post("/admin/jobs/retry-failed", headers=adm).json() == {"retried": 1}
    db_session.flush()
    db_session.refresh(k)
    assert (k.attempts, k.released) == (0, 0)


class StubSession:
    def __init__(self, info):
        self.info, self.commits = info, 0

    def commit(self):
        self.commits += 1


class StubLLM:
    def __init__(self, on_call):
        self.seen = []

        def create(**kw):
            self.seen.append(on_call())
            msg = SimpleNamespace(content='{"route": "fact"}')
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_router_llm_runs_without_db_transaction(settings):
    s = StubSession(info={RELEASE_BEFORE_IO: True})
    llm = StubLLM(on_call=lambda: s.commits)
    cfg = settings.model_copy(update={"router_llm": True})
    p = Principal(None, "sub", "u", (), False)
    prep, meta = route_question(s, None, llm, cfg, p, LONG, True)
    assert (prep, meta) == (None, {"route": "fact"})
    assert llm.seen == [1]
