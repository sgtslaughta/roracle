import time
from collections.abc import Callable, Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def make_session_factory(url: str, connect_timeout_s: int = 5) -> sessionmaker[Session]:
    """`?connect_timeout=` in the URL wins over `connect_timeout_s` (ROR_DB_CONNECT_TIMEOUT_S)."""
    args = {} if "connect_timeout" in make_url(url).query else {"connect_timeout": connect_timeout_s}
    return sessionmaker(create_engine(url, pool_pre_ping=True, connect_args=args), expire_on_commit=False)


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
