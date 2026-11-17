"""Task 10.9: connection and thread budgets."""

import asyncio
import threading
import time

import anyio.to_thread
import httpx
import pytest
from api_seed import FakeES, seed
from test_search_api import FakeLLM, hit

from roracle.api.app import create_app
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.db import api_session_factory, make_session_factory, release_before_io, worker_session_factory


def test_pool_budget_comes_from_settings(settings):
    s = settings.model_copy(
        update={
            "db_pool_size": 7, "db_max_overflow": 2, "db_pool_timeout_s": 3.0,
            "worker_db_pool_size": 2, "worker_db_max_overflow": 1,
        }
    )  # fmt: skip
    api = api_session_factory(s).kw["bind"].pool
    assert (api.size(), api._max_overflow, api._timeout) == (7, 2, 3.0)
    wk = worker_session_factory(s).kw["bind"].pool
    assert (wk.size(), wk._max_overflow) == (2, 1)
    assert api_session_factory(s)().info["release_before_io"] is True
    assert (
        "release_before_io" not in worker_session_factory(s)().info
    )  # job sessions never commit mid-handler


def test_release_before_io_only_commits_flagged_sessions(settings):
    plain = make_session_factory(settings.database_url)()
    flagged = api_session_factory(settings)()
    for s, expect in ((plain, 1), (flagged, 0)):
        s.execute(__import__("sqlalchemy").text("SELECT 1"))
        release_before_io(s)
        assert s.get_bind().pool.checkedout() == expect, s
        s.close()


@pytest.fixture
def es_probe(app, db_session):
    """Fake ES that records how many pooled connections are checked out while the (faked) ES call runs."""
    pool = db_session.get_bind().pool
    db_session.info["release_before_io"] = True  # what the api session factory sets in production

    class Probe(FakeES):
        def __init__(self):
            super().__init__()
            self.checked_out = []

        def search(self, **kw):
            self.checked_out.append(pool.checkedout())
            return super().search(**kw)

    f = Probe()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    return f


@pytest.mark.usefixtures("_blob_store_override")
def test_search_holds_no_connection_during_es_call(client, as_ana, ana, db_session, es_probe):
    d, v = seed(db_session, ana, title="x")
    es_probe.hits = [hit(d, v)]
    db_session.commit()
    assert client.get("/search", headers=as_ana, params={"q": "alpha"}).status_code == 200
    assert es_probe.checked_out and set(es_probe.checked_out) == {0}


def test_healthz_is_async_and_lifespan_raises_the_thread_limiter(settings):
    from roracle.api.routes import health

    assert asyncio.iscoroutinefunction(health.healthz)
    app = create_app(settings.model_copy(update={"api_threads": 123}))

    async def go():
        async with app.router.lifespan_context(app):
            return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert asyncio.run(go()) == 123


def test_100_blocked_streams_do_not_block_healthz(settings):
    """Each in-flight sync stream occupies one worker thread: with api_threads=200 a hundred of them leave the
    probes (and /search, MCP) free; healthz does not even need a thread."""
    app = create_app(settings.model_copy(update={"api_threads": 200}))
    gate = threading.Event()

    def slow():
        gate.wait(20)
        yield b"data: x\n\n"

    from starlette.responses import StreamingResponse

    app.add_api_route("/_slow", lambda: StreamingResponse(slow(), media_type="text/event-stream"))

    async def go():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        async with app.router.lifespan_context(app), client as c:
            streams = [asyncio.create_task(c.get("/_slow", timeout=30)) for _ in range(100)]
            await asyncio.sleep(1.0)
            t = time.monotonic()
            r = await c.get("/healthz")
            took = time.monotonic() - t
            gate.set()
            await asyncio.gather(*streams)
            return r.status_code, took

    status, took = asyncio.run(go())
    assert status == 200 and took < 1.0


def test_disable_prepared_sets_prepare_threshold_none(settings):
    on = settings.model_copy(update={"db_disable_prepared": True})
    engine = api_session_factory(on).kw["bind"]
    seen = {}
    real = engine.dialect.connect

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    engine.dialect.connect = spy
    with engine.connect() as c:
        c.exec_driver_sql("SELECT 1")
    assert seen.get("prepare_threshold", "missing") is None
    off = api_session_factory(settings).kw["bind"]
    assert "prepare_threshold" not in off.dialect.create_connect_args(off.url)[1]


def test_reduce_inputs_never_commits_a_worker_session(settings, monkeypatch):
    """eval_run runs reduce_inputs inside the job transaction: only flagged request sessions release early."""
    from roracle.chat import global_answer

    monkeypatch.setattr(global_answer, "map_docs", lambda *a, **k: [])
    monkeypatch.setattr(global_answer, "build", lambda *a, **k: ([], []))

    class Spy:
        def __init__(self, info):
            self.info, self.commits = info, 0

        def commit(self):
            self.commits += 1

    job = Spy({})
    global_answer.reduce_inputs(job, None, None, settings, None, "q", [])
    assert job.commits == 0
    request = Spy({"release_before_io": True})
    global_answer.reduce_inputs(request, None, None, settings, None, "q", [])
    assert request.commits == 2
