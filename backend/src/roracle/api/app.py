from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from roracle import __version__
from roracle.api.routes import admin, documents, health, me, near_dup
from roracle.auth import JwksCache
from roracle.config import Settings, get_settings
from roracle.db import make_session_factory


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.session_factory.kw["bind"].dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="rOracle", version=__version__, lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.session_factory = make_session_factory(app.state.settings.database_url)
    app.state.jwks = JwksCache(app.state.settings.oidc_jwks_url)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(me.router)
    app.include_router(documents.router)
    app.include_router(near_dup.router)
    return app
