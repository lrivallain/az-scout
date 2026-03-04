"""Azure Scout – FastAPI web application.

Interactive web tool to visualize how Azure maps logical availability zones
to physical zones across subscriptions in a given region.
"""

import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response, StreamingResponse

from az_scout import __version__, azure_api
from az_scout.plugin_manager import reconcile_installed_plugins
from az_scout.plugins import get_plugin_metadata, register_plugins
from az_scout.routes import router as plugin_manager_router
from az_scout.routes.discovery import router as discovery_router
from az_scout.services.ai_chat import is_chat_enabled

_PKG_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Lifespan – preload discovery caches on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Warm the tenant cache, reconcile & register plugins, and start the MCP session manager."""
    # Store MCP server ref so route handlers can call reload_plugins()
    _app.state.mcp_server = _mcp_server
    t = threading.Thread(target=azure_api.preload_discovery, daemon=True)
    t.start()
    # Reconcile plugins: reinstall any that are in installed.json but missing
    # from the packages dir (e.g. after a container restart).
    reconcile_installed_plugins()
    # Discover and register plugins (routes, static, MCP tools, chat modes)
    register_plugins(_app, _mcp_server)
    # The StreamableHTTP session manager needs a running task group;
    # sub-app lifespans are not invoked by FastAPI, so we start it here.
    # Re-create the session manager if a previous instance was already used
    # (e.g. across multiple TestClient contexts in tests).
    _ensure_fresh_session_manager()
    async with _mcp_server.session_manager.run():
        yield


app = FastAPI(
    title="az-scout API",
    version=__version__,
    description=(
        "REST API for the Azure Scout. "
        "Provides endpoints to discover Azure tenants, subscriptions, "
        "AZ-enabled regions, logical-to-physical zone mappings, and "
        "resource SKU availability with optional filtering.\n\n"
        "An **MCP server** (Streamable HTTP transport) is also available at "
        "`/mcp` for AI agent integration."
    ),
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Content-Security-Policy
# ---------------------------------------------------------------------------

_CSP_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net d3js.org",
        "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net",
        "font-src 'self' cdn.jsdelivr.net",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
    ]
)


class _CSPMiddleware(BaseHTTPMiddleware):
    """Add Content-Security-Policy header to all HTML responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if "text/html" in ct:
            response.headers["Content-Security-Policy"] = _CSP_POLICY
        return response


app.add_middleware(_CSPMiddleware)

app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# Plugin manager API routes
app.include_router(plugin_manager_router)

# Discovery API routes (tenants, subscriptions, regions, locations)
app.include_router(discovery_router, prefix="/api")

# ---------------------------------------------------------------------------
# MCP – mount the MCP server as an ASGI sub-app under /mcp
# ---------------------------------------------------------------------------

from az_scout.mcp_server import mcp as _mcp_server  # noqa: E402

# Override the internal path so that mounting at "/mcp" gives a clean
# "/mcp" endpoint (instead of the default "/mcp/mcp").
_mcp_server.settings.streamable_http_path = "/"
_mcp_starlette = _mcp_server.streamable_http_app()
app.mount("/mcp", _mcp_starlette)


def _ensure_fresh_session_manager() -> None:
    """Re-create the StreamableHTTP session manager if already used.

    ``StreamableHTTPSessionManager.run()`` can only be called once per
    instance.  When the FastAPI lifespan is re-entered (e.g. across
    multiple ``TestClient`` contexts in tests) we need a fresh manager.
    """
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    mgr = _mcp_server.session_manager
    if mgr._has_started:
        new_mgr = StreamableHTTPSessionManager(
            app=_mcp_server._mcp_server,
            event_store=_mcp_server._event_store,
            json_response=_mcp_server.settings.json_response,
            stateless=_mcp_server.settings.stateless_http,
            security_settings=_mcp_server.settings.transport_security,
        )
        _mcp_server._session_manager = new_mgr
        # Also patch the ASGI handler used by the mounted Starlette app
        for route in _mcp_starlette.routes:
            if hasattr(route, "app") and hasattr(route.app, "session_manager"):
                route.app.session_manager = new_mgr


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

from az_scout.logging_config import _setup_logging, setup_plugin_logger  # noqa: F401, E402

_setup_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    """Serve the main page."""
    # EasyAuth injects the authenticated user's display name via this header.
    auth_user = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "")
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "auth_user": auth_user,
            "chat_enabled": is_chat_enabled(),
            "plugins": get_plugin_metadata(),
        },
    )


if __name__ == "__main__":
    from az_scout.cli import cli

    cli()


# ---------------------------------------------------------------------------
# POST /api/chat – AI chat with tool calling (streaming SSE)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    messages: list[ChatMessage]
    mode: str = "discussion"
    tenant_id: str | None = None
    region: str | None = None
    subscription_id: str | None = None


@app.post(
    "/api/chat",
    tags=["AI Chat"],
    summary="AI chat with Azure Scout tools",
    responses={503: {"description": "AI chat not configured"}},
)
async def chat(body: ChatRequest) -> StreamingResponse:
    """Stream AI chat completions with tool-calling support.

    Requires ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY``, and
    ``AZURE_OPENAI_DEPLOYMENT`` environment variables.
    """
    if not is_chat_enabled():
        return JSONResponse(  # type: ignore[return-value]
            {"error": "AI chat is not configured. Set AZURE_OPENAI_* environment variables."},
            status_code=503,
        )

    from az_scout.services.ai_chat import chat_stream

    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    return StreamingResponse(
        chat_stream(
            messages,
            tenant_id=body.tenant_id,
            region=body.region,
            subscription_id=body.subscription_id,
            mode=body.mode,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
