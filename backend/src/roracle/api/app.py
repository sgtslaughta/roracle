from fastapi import FastAPI

from roracle import __version__
from roracle.api.routes import health


def create_app(settings: None = None) -> FastAPI:
    app = FastAPI(title="rOracle", version=__version__)
    app.include_router(health.router)
    return app
