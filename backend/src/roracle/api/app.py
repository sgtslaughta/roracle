import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from roracle import __version__, http_pool
from roracle.api import input_guard
from roracle.api.routes import (
    admin,
    admin_connectors,
    admin_eval,
    admin_index,
    admin_labels,
    admin_ops,
    admin_settings,
    admin_writing,
    annotations,
    chats,
    chats_read,
    citations,
    directory,
    documents,
    documents_manage,
    documents_read,
    documents_write,
    drafts,
    drafts_build,
    drafts_checks,
    drafts_submit,
    evidence,
    export,
    handoffs,
    health,
    labels,
    library,
    me,
    near_dup,
    notifications,
    proposals,
    search,
    skills,
    tokens,
)
from roracle.auth import JwksCache
from roracle.config import Settings, get_settings
from roracle.db import api_session_factory
from roracle.ingest.embed import EmbedUnavailable
from roracle.labels.policy import ExportBlocked, export_blocked_handler
from roracle.mcp.auth import BearerAuth
from roracle.mcp.server import build_mcp
from roracle.metrics import http_middleware

log = logging.getLogger(__name__)


def _ensure_tmp_lifecycle(settings: Settings) -> None:
    if not settings.s3_endpoint:
        return
    from botocore.config import Config

    from roracle.storage import blob_store_from, ensure_tmp_lifecycle

    cfg = Config(
        s3={"addressing_style": "path"}, connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}
    )
    try:
        ensure_tmp_lifecycle(blob_store_from(settings, config=cfg))
    except Exception:
        log.warning("tmp/ lifecycle check failed", exc_info=True)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # every sync route and every streamed answer holds one worker thread: anyio's default of 40 tokens would
    # stall /search, MCP and the readiness checks behind ~40 concurrent chats
    anyio.to_thread.current_default_thread_limiter().total_tokens = app.state.settings.api_threads
    await anyio.to_thread.run_sync(_ensure_tmp_lifecycle, app.state.settings)
    async with app.state.mcp.session_manager.run():
        yield
    http_pool.close()
    app.state.session_factory.kw["bind"].dispose()


async def _embed_unavailable(_request, _exc):
    """The query embedding endpoint is throttled/down: fail fast (no retries on the interactive path)."""
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": "inference unavailable"}, status_code=503, headers={"Retry-After": "2"})


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="rOracle", version=__version__, lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.session_factory = api_session_factory(app.state.settings)
    app.state.jwks = JwksCache(app.state.settings.oidc_jwks_url)
    app.state.http = http_pool.init()  # shared reranker client: created at startup, closed in the lifespan
    app.middleware("http")(http_middleware)
    input_guard.install(app)
    app.add_exception_handler(ExportBlocked, export_blocked_handler)
    app.add_exception_handler(EmbedUnavailable, _embed_unavailable)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_labels.router)
    app.include_router(admin_eval.router)
    app.include_router(labels.router)
    app.include_router(admin_connectors.router)
    app.include_router(admin_index.router)
    app.include_router(admin_ops.router)
    app.include_router(admin_writing.router)
    app.include_router(directory.router)
    app.include_router(drafts.router)
    app.include_router(drafts_build.router)
    app.include_router(drafts_checks.router)
    app.include_router(drafts_submit.router)
    app.include_router(skills.router)
    app.include_router(me.router)
    app.include_router(notifications.router)
    app.include_router(tokens.router)
    app.include_router(handoffs.router)
    app.include_router(documents.router)
    app.include_router(near_dup.router)
    app.include_router(library.router)
    app.include_router(annotations.router)
    app.include_router(proposals.router)
    app.include_router(evidence.router)
    app.include_router(export.router)
    app.include_router(documents_read.router)
    app.include_router(documents_write.router)
    app.include_router(documents_manage.router)
    app.include_router(citations.router)
    app.include_router(search.router)
    app.include_router(chats_read.router)
    app.include_router(chats.router)
    _mount_mcp(app)
    return app


def _no_get(inner):
    """Stateless JSON mode has nothing to stream: a GET would hold an SSE connection open forever."""

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "GET":
            return await PlainTextResponse("method not allowed", 405, {"Allow": "POST"})(scope, receive, send)
        await inner(scope, receive, send)

    return app


def _mount_mcp(app: FastAPI) -> None:
    """Stateless JSON MCP at exactly POST /mcp (a Mount would 307 to /mcp/); bearer-only so no DNS-rebinding guard (R5)."""
    s = app.state.settings
    mcp = app.state.mcp = build_mcp(app)
    inner = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        max_request_body_size=s.mcp_max_upload_bytes * 4 // 3 + 64 * 1024,
    )
    app.router.routes.append(Route("/mcp", BearerAuth(_no_get(inner), app)))

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    def protected_resource(request: Request) -> dict:  # R6
        return {
            "resource": f"{(s.public_base_url or str(request.base_url)).rstrip('/')}/mcp",
            "authorization_servers": [s.oidc_issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["read", "write"],
        }
