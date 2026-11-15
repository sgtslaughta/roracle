"""Bearer auth for the MCP mount: pure ASGI wrapper + `current()` principal contextvar.

Tests override `get_jwks`/`get_session`/`get_app_settings` via `app.dependency_overrides`; `dep()`/`open_session()`
honour those overrides and otherwise read `app.state` (same behaviour as the FastAPI dependencies).
Overrides must be plain zero-arg callables returning the value."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import anyio.to_thread
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from roracle.auth import Principal, get_app_settings, get_jwks, resolve_principal
from roracle.db import get_session

_current: ContextVar[Principal | None] = ContextVar("mcp_principal", default=None)


def current() -> Principal:
    p = _current.get()
    if p is None:
        raise RuntimeError("no authenticated principal")
    return p


def dep(app: FastAPI, fn: Callable):
    """Resolve a request-scoped FastAPI dependency (get_es, get_llm, get_jwks, ...) outside a request."""
    override = app.dependency_overrides.get(fn)
    return override() if override else fn(SimpleNamespace(app=app))


@contextmanager
def open_session(app: FastAPI) -> Iterator[Session]:
    override = app.dependency_overrides.get(get_session)
    if override:  # tests: the fixture owns the session
        yield override()
        return
    with app.state.session_factory() as session:
        yield session


def _resolve(app: FastAPI, authorization: str | None) -> Principal:
    with open_session(app) as session:
        try:
            p = resolve_principal(
                session,
                dep(app, get_jwks),
                dep(app, get_app_settings),
                authorization,
                app.state.session_factory,
            )
            session.commit()
            return p
        except Exception:
            session.rollback()
            raise


class BearerAuth:
    def __init__(self, inner: ASGIApp, app: FastAPI):
        self.inner, self.app = inner, app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        req = Request(scope)
        base = self.app.state.settings.public_base_url or str(req.base_url)
        meta = f"{base.rstrip('/')}/.well-known/oauth-protected-resource"
        try:
            p = await anyio.to_thread.run_sync(_resolve, self.app, req.headers.get("authorization"))
        except HTTPException as e:
            resp = JSONResponse(
                {"detail": e.detail},
                status_code=e.status_code,
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'},
            )
            return await resp(scope, receive, send)
        token = _current.set(p)
        try:
            await self.inner(scope, receive, send)
        finally:
            _current.reset(token)
