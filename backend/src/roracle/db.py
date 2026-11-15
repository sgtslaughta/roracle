from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from roracle.config import get_settings


def make_session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(create_engine(url, pool_pre_ping=True), expire_on_commit=False)


@lru_cache
def _default_factory() -> sessionmaker[Session]:
    return make_session_factory(get_settings().database_url)


def get_session() -> Iterator[Session]:
    with _default_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
