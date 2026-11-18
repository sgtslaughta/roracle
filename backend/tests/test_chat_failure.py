"""A failed, timed-out or empty answer: a plain reason in the stream and on the saved row; progress frames."""

import httpx
import openai
import pytest
from api_seed import seed
from sqlalchemy import select
from test_chat_stream import FakeStreamLLM, hit, new_chat, setup, sse  # noqa: F401 (setup is a fixture)

from roracle.chat.answer import EmptyAnswer, failure_reason
from roracle.models import ChatMessage

pytestmark = pytest.mark.usefixtures("_blob_store_override")
_REQ = httpx.Request("POST", "http://llm/v1/chat/completions")


class TimeoutLLM(FakeStreamLLM):
    def _create(self, **kw):
        raise openai.APITimeoutError(request=_REQ)


def _assistant(db_session):
    db_session.expire_all()
    return db_session.scalars(select(ChatMessage).where(ChatMessage.role == "assistant")).one()


def test_status_frame_before_the_model_writes(client, as_ana, ana, db_session, setup):  # noqa: F811
    d, v = seed(db_session, ana)
    setup(hits=[hit(d, v)])
    cid = new_chat(client, as_ana)
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "q"}), status=True)
    assert ev[0] == ("status", {"stage": "writing"})
    assert [e for e, _ in ev][1:] == ["token", "token", "token", "citations", "done"]


def test_timeout_is_a_failure_with_a_plain_reason(client, as_ana, ana, db_session, setup):  # noqa: F811
    d, v = seed(db_session, ana)
    setup(TimeoutLLM(), hits=[hit(d, v)])
    cid = new_chat(client, as_ana)
    r = client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "q"})
    assert r.status_code == 200
    ev = sse(r)
    assert ev[-1] == ("error", {"detail": "the language model took too long to respond"})
    m = _assistant(db_session)
    assert (m.status, m.content, m.error) == ("failed", "", "the language model took too long to respond")
    got = client.get(f"/chats/{cid}", headers=as_ana).json()["messages"]
    assert got[-1]["status"] == "failed" and got[-1]["error"] == "the language model took too long to respond"
    assert got[0]["error"] is None


@pytest.mark.parametrize("tokens", [(), ("",), (" ", "\n")])
def test_empty_completion_is_failed_not_saved_as_an_answer(client, as_ana, ana, db_session, setup, tokens):  # noqa: F811
    d, v = seed(db_session, ana)
    setup(FakeStreamLLM(tokens=tokens), hits=[hit(d, v)])
    cid = new_chat(client, as_ana)
    ev = sse(client.post(f"/chats/{cid}/messages", headers=as_ana, json={"content": "q"}))
    assert ev[-1] == ("error", {"detail": "the language model returned an empty answer"})
    assert "done" not in [e for e, _ in ev]
    m = _assistant(db_session)
    assert m.status == "failed" and m.error == "the language model returned an empty answer"


def test_failure_reasons_are_plain():
    resp = httpx.Response(503, request=_REQ)
    assert failure_reason(EmptyAnswer()) == "the language model returned an empty answer"
    assert (
        failure_reason(openai.APITimeoutError(request=_REQ)) == "the language model took too long to respond"
    )
    assert failure_reason(httpx.ReadTimeout("x")) == "the language model took too long to respond"
    assert (
        failure_reason(openai.APIConnectionError(request=_REQ)) == "the language model could not be reached"
    )
    assert failure_reason(httpx.ConnectError("x")) == "the language model could not be reached"
    busy = openai.APIStatusError("busy", response=resp, body=None)
    assert failure_reason(busy) == "the language model is busy"
    assert failure_reason(RuntimeError("boom secret-host:1234")) == "something went wrong while writing it"
