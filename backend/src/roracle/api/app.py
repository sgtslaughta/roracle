from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from roracle import __version__
from roracle.api import input_guard
from roracle.api.routes import (
    admin,
    admin_connectors,
    admin_index,
    admin_ops,
    admin_settings,
    admin_writing,
    annotations,
    chats,
    chats_read,
    citations,
    documents,
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
from roracle.db import make_session_factory
from roracle.mcp.auth import BearerAuth
from roracle.mcp.server import build_mcp
from roracle.metrics import http_middleware


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with app.state.mcp.session_manager.run():
        yield
    app.state.session_factory.kw["bind"].dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="rOracle", version=__version__, lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.session_factory = make_session_factory(
        app.state.settings.database_url, app.state.settings.db_connect_timeout_s
    )
    app.state.jwks = JwksCache(app.state.settings.oidc_jwks_url)
    app.middleware("http")(http_middleware)
    input_guard.install(app)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_connectors.router)
    app.include_router(admin_index.router)
    app.include_router(admin_ops.router)
    app.include_router(admin_writing.router)
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
    app.include_router(citations.router)
    app.include_router(search.router)
    app.include_router(chats_read.router)
    app.include_router(chats.router)
    _mount_mcp(app)
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
    app.router.routes.append(Route("/mcp", BearerAuth(inner, app)))

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    def protected_resource(request: Request) -> dict:  # R6
        return {
            "resource": f"{(s.public_base_url or str(request.base_url)).rstrip('/')}/mcp",
            "authorization_servers": [s.oidc_issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["read", "write"],
        }
