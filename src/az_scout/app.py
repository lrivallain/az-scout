"""Azure Scout – FastAPI web application.

Interactive web tool to visualize how Azure maps logical availability zones
to physical zones across subscriptions in a given region.
"""

import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from az_scout import __version__, azure_api
from az_scout.plugin_api import PluginError
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
    # In OBO mode, don't preload discovery with app credentials — each user
    # will authenticate individually and data is fetched with their token.
    from az_scout.azure_api._obo import is_obo_enabled

    if not is_obo_enabled():
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
# Global exception handler – returns a consistent JSON error for unhandled
# exceptions.  HTTPException is already handled natively by FastAPI, and
# RequestValidationError has its own built-in handler as well.
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Return ``{"error": …}`` with status 500 for any unhandled exception."""
    if isinstance(exc, OboTokenError):
        # OBO errors are expected (expired tokens, etc.) — no stacktrace
        return JSONResponse({"error": str(exc)}, status_code=401)
    logging.getLogger(__name__).exception("Unhandled error")
    return JSONResponse({"error": str(exc)}, status_code=500)


from az_scout.azure_api._obo import OboTokenError  # noqa: E402


@app.exception_handler(OboTokenError)
async def _obo_error_handler(_request: Request, exc: OboTokenError) -> JSONResponse:
    """Return 401 with claims challenge or direct-auth signal for OBO errors."""
    if exc.error_code == "claims_challenge":
        return JSONResponse(
            {"error": "claims_challenge", "claims": exc.claims},
            status_code=401,
        )
    if exc.error_code == "mfa_direct_auth":
        return JSONResponse(
            {"error": "mfa_direct_auth"},
            status_code=401,
        )
    return JSONResponse({"error": str(exc)}, status_code=401)


# ---------------------------------------------------------------------------
# Plugin error boundary – catches PluginError and subclasses, returns
# {"error": …, "detail": …} with the status code from the exception.
# Registered *before* the generic handler so it takes priority.
# ---------------------------------------------------------------------------


@app.exception_handler(PluginError)
async def _plugin_error_handler(_request: Request, exc: PluginError) -> JSONResponse:
    """Return ``{"error": …, "detail": …}`` for PluginError exceptions."""
    # If the root cause is an OBO auth error, return 401 (not the plugin's status code)
    cause = exc.__cause__
    if isinstance(cause, OboTokenError):
        return JSONResponse({"error": str(cause)}, status_code=401)

    message = str(exc)
    logging.getLogger(__name__).warning("Plugin error (%d): %s", exc.status_code, message)
    return JSONResponse(
        {"error": message, "detail": message},
        status_code=exc.status_code,
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
        "img-src 'self' data: https://github.com https://*.githubusercontent.com",
        "connect-src 'self' cdn.jsdelivr.net https://plugin-catalog.az-scout.com",
        "frame-ancestors 'none'",
    ]
)


class _CSPMiddleware:
    """Add Content-Security-Policy header to all HTML responses.

    Uses raw ASGI instead of BaseHTTPMiddleware to preserve contextvars
    propagation through the middleware chain.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_csp(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                ct = headers.get(b"content-type", b"").decode("latin-1", errors="replace")
                if "text/html" in ct:
                    h = list(message.get("headers", []))
                    h.append((b"content-security-policy", _CSP_POLICY.encode("latin-1")))
                    message = {**message, "headers": h}
            await send(message)

        await self.app(scope, receive, send_with_csp)


app.add_middleware(_CSPMiddleware)


# ---------------------------------------------------------------------------
# Auth context middleware – populates contextvars for the current request
# so _get_headers() can read user_token/direct_arm without explicit params.
#
# Uses a raw ASGI middleware instead of BaseHTTPMiddleware because Starlette's
# BaseHTTPMiddleware runs call_next in a separate anyio task, which breaks
# contextvars propagation to route handlers.
# ---------------------------------------------------------------------------

from az_scout.auth import clear_request_auth, set_request_auth  # noqa: E402


class _AuthContextMiddleware:
    """Raw ASGI middleware that sets auth context for the current request.

    Reads auth from two sources (in priority order):
    1. Authorization Bearer header (MCP clients, direct API calls)
    2. Session cookie (web browser users via server-side login)
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        token_value: str | None = None
        direct = False

        # 1. Check Authorization header (MCP / direct API clients)
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                val = value.decode("latin-1")
                if val.startswith("Bearer "):
                    token_value = val[7:]
            elif name == b"x-direct-arm":
                direct = value == b"true"

        # 2. Fall back to session cookie (web browser)
        if not token_value:
            from az_scout.azure_api._obo import CLIENT_SECRET
            from az_scout.routes.auth import _COOKIE_NAME, _sessions, _verify_session_id

            if CLIENT_SECRET:
                for name, value in scope.get("headers", []):
                    if name == b"cookie":
                        cookies = {}
                        for part in value.decode("latin-1").split(";"):
                            part = part.strip()
                            if "=" in part:
                                k, v = part.split("=", 1)
                                cookies[k.strip()] = v.strip()
                        cookie_val = cookies.get(_COOKIE_NAME)
                        if cookie_val:
                            session_id = _verify_session_id(cookie_val, CLIENT_SECRET)
                            if session_id:
                                session = _sessions.get(session_id)
                                if session and session.get("access_token"):
                                    token_value = session["access_token"]
                        break

        tokens = set_request_auth(token_value, direct)
        try:
            await self.app(scope, receive, send)
        finally:
            clear_request_auth(tokens)


app.add_middleware(_AuthContextMiddleware)

app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# Auth routes (login, callback, logout, /api/auth/me, /api/auth/config)
from az_scout.routes.auth import router as auth_router  # noqa: E402

app.include_router(auth_router)

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

# Wrap the MCP sub-app with the auth middleware so MCP tool calls
# also pick up the user token from the request headers.
_mcp_with_auth = _AuthContextMiddleware(_mcp_starlette)
app.mount("/mcp", _mcp_with_auth)


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


@app.get("/", response_class=HTMLResponse, response_model=None, include_in_schema=False)
async def index(request: Request) -> HTMLResponse | RedirectResponse:
    """Serve the main page. Redirects to login when OBO is enabled and user is not signed in."""
    from az_scout.azure_api._obo import is_obo_enabled
    from az_scout.routes.auth import get_session

    if is_obo_enabled() and not get_session(request):
        return RedirectResponse("/auth/login")

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
