from fastapi import FastAPI

from roracle import __version__
from roracle.api.routes import health, me
from roracle.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="rOracle", version=__version__)
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    app.include_router(me.router)
    return app
