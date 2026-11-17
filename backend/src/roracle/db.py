import time
from collections.abc import Callable, Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

RELEASE_BEFORE_IO = "release_before_io"


def release_before_io(session: Session) -> None:
    """Commit so the pooled connection goes back before a slow external call (ES, rerank, LLM). Only for request
    sessions (api factory sets the flag): worker/eval sessions run inside the job transaction (ADR-0003) and must
    never commit mid-handler."""
    if session.info.get(RELEASE_BEFORE_IO):
        session.commit()


def make_session_factory(
    url: str,
    connect_timeout_s: int = 5,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout_s: float = 30.0,
    release_before_io: bool = False,
    disable_prepared: bool = False,
) -> sessionmaker[Session]:
    """`?connect_timeout=` in the URL wins over `connect_timeout_s` (ROR_DB_CONNECT_TIMEOUT_S).
    The process holds at most `pool_size + max_overflow` connections (the Helm budget multiplies this by the
    replica count); the defaults are SQLAlchemy's, long-running callers pass the settings."""
    args: dict = {} if "connect_timeout" in make_url(url).query else {"connect_timeout": connect_timeout_s}
    if (
        disable_prepared
    ):  # PgBouncer transaction mode: server-side prepared statements do not survive a switch
        args["prepare_threshold"] = None
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args=args,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_s,
    )
    info = {RELEASE_BEFORE_IO: True} if release_before_io else {}
    return sessionmaker(engine, expire_on_commit=False, info=info)


def api_session_factory(settings) -> sessionmaker[Session]:
    return make_session_factory(
        settings.database_url,
        settings.db_connect_timeout_s,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout_s=settings.db_pool_timeout_s,
        release_before_io=True,
        disable_prepared=settings.db_disable_prepared,
    )


def worker_session_factory(settings) -> sessionmaker[Session]:
    return make_session_factory(
        settings.database_url,
        settings.db_connect_timeout_s,
        pool_size=settings.worker_db_pool_size,
        max_overflow=settings.worker_db_max_overflow,
        pool_timeout_s=settings.db_pool_timeout_s,
        disable_prepared=settings.db_disable_prepared,
    )


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def wait_for_db(url: str, seconds: float, sleep: Callable[[float], None] = time.sleep) -> None:
    """Retry connecting every 5 s until `seconds` elapse; SystemExit (non-zero) afterwards."""
    engine = create_engine(url, connect_args={"connect_timeout": 5}, poolclass=NullPool)
    deadline = time.monotonic() + seconds
    while True:
        try:
            engine.connect().close()
            engine.dispose()
            return
        except Exception as e:
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"database {make_url(url).render_as_string()} not reachable after {seconds}s: {type(e).__name__}"
                ) from e
            sleep(5)


def get_stream_session(request: Request) -> Session:
    """A session for work that outlives the request handler (a streamed answer): created lazily (no connection until
    used) and closed by its owner. Tests override this with their own session."""
    return request.app.state.session_factory()
