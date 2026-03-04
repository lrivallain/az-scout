"""Azure Scout – FastAPI web application.

Interactive web tool to visualize how Azure maps logical availability zones
to physical zones across subscriptions in a given region.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from az_scout import __version__, azure_api
from az_scout.plugin_manager import reconcile_installed_plugins
from az_scout.plugins import get_plugin_metadata, register_plugins
from az_scout.routes import router as plugin_manager_router
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

app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# Plugin manager API routes
app.include_router(plugin_manager_router)

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
    if mgr._has_started:  # type: ignore[attr-defined]
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
# Colored logging (reuse uvicorn's formatter)
# ---------------------------------------------------------------------------


class _CategoryFilter(logging.Filter):
    """Inject a ``category`` field into every log record.

    * Loggers under ``az_scout.*``       → ``core``
    * Loggers under ``az_scout_<plugin>.*`` → ``plugin:<plugin>``
    * Loggers under ``uvicorn.*``         → ``server``
    * Loggers under ``httpx.*``           → ``http``
    * Everything else                     → ``ext``
    """

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if name.startswith("az_scout_"):
            # e.g. "az_scout_batch_sku.routes" → plugin name "batch_sku"
            suffix = name[len("az_scout_") :]
            plugin_name = suffix.split(".")[0]
            record.category = f"plugin:{plugin_name}"  # type: ignore[attr-defined]
        elif name.startswith("az_scout"):
            record.category = "core"  # type: ignore[attr-defined]
        elif name.startswith("uvicorn"):
            record.category = "server"  # type: ignore[attr-defined]
        elif name.startswith("httpx"):
            record.category = "http"  # type: ignore[attr-defined]
        elif name.startswith("mcp"):
            record.category = "mcp"  # type: ignore[attr-defined]
        else:
            record.category = "ext"  # type: ignore[attr-defined]
        return True


# Shared handler & filter – reused by setup_plugin_logger()
_log_handler: logging.Handler | None = None
_log_filter: _CategoryFilter = _CategoryFilter()


def _setup_logging(level: int | None = None) -> None:
    """Configure the root ``az_scout`` logger with uvicorn-style colours.

    *level* overrides the default.  When *level* is ``None`` the function
    reads the ``AZ_SCOUT_LOG_LEVEL`` environment variable (``DEBUG``,
    ``INFO``, ``WARNING``, …) so that uvicorn reload workers inherit the
    log level set by the CLI.
    """
    global _log_handler  # noqa: PLW0603

    import os

    from uvicorn.logging import DefaultFormatter

    if level is None:
        level = getattr(logging, os.environ.get("AZ_SCOUT_LOG_LEVEL", "WARNING"))

    handler = logging.StreamHandler()
    handler.setFormatter(
        DefaultFormatter(
            fmt="%(levelprefix)s [%(category)s] %(name)s - %(message)s",
            use_colors=True,
        )
    )
    handler.addFilter(_log_filter)
    _log_handler = handler

    app_logger = logging.getLogger("az_scout")
    app_logger.handlers = [handler]
    app_logger.setLevel(level)
    app_logger.propagate = False

    # Unify uvicorn, httpx and mcp loggers under the same format
    for third_party in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "mcp"):
        tp_logger = logging.getLogger(third_party)
        tp_logger.handlers = [handler]
        tp_logger.propagate = False
    # uvicorn.access stays at INFO (request lines); uvicorn.error follows app level
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.INFO)

    # Silence noisy third-party loggers
    logging.getLogger("azure").setLevel(logging.WARNING)

    # Remove any stray handlers on the root logger (e.g. rich, basicConfig)
    # so that plugin loggers with propagate=False don't get duplicated.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


def setup_plugin_logger(plugin_name: str) -> None:
    """Configure the ``az_scout_<plugin_name>`` logger to share the core format.

    Called automatically during plugin registration.  Plugin authors do not
    need to call this themselves — use :func:`az_scout.plugin_api.get_plugin_logger`
    to obtain a correctly-namespaced logger.
    """
    if _log_handler is None:
        return  # logging not yet initialised
    module_name = f"az_scout_{plugin_name.replace('-', '_')}"
    plugin_logger = logging.getLogger(module_name)
    if _log_handler not in plugin_logger.handlers:
        plugin_logger.handlers = [_log_handler]
    plugin_logger.setLevel(logging.getLogger("az_scout").level)
    plugin_logger.propagate = False


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


@app.get("/api/tenants", tags=["Discovery"], summary="List Azure AD tenants")
async def list_tenants() -> JSONResponse:
    """Return Azure AD tenants accessible by the current credential.

    Returns all tenants with their authentication status and the default
    tenant ID for the current auth context.
    """
    try:
        return JSONResponse(await asyncio.to_thread(azure_api.list_tenants))
    except Exception as exc:
        logger.exception("Failed to list tenants")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/subscriptions", tags=["Discovery"], summary="List enabled Azure subscriptions")
async def list_subscriptions(
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID to scope the query."
    ),
) -> JSONResponse:
    """Return all enabled Azure subscriptions, sorted alphabetically."""
    try:
        return JSONResponse(await asyncio.to_thread(azure_api.list_subscriptions, tenantId))
    except Exception as exc:
        logger.exception("Failed to list subscriptions")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/regions", tags=["Discovery"], summary="List AZ-enabled regions")
async def list_regions(
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return Azure regions that support Availability Zones."""
    try:
        return JSONResponse(
            await asyncio.to_thread(azure_api.list_regions, subscriptionId, tenantId)
        )
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to list regions")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/locations", tags=["Discovery"], summary="List all ARM locations")
async def list_locations(
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return all Azure ARM locations, including those without Availability Zones."""
    try:
        return JSONResponse(
            await asyncio.to_thread(azure_api.list_locations, subscriptionId, tenantId)
        )
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Failed to list locations")
        return JSONResponse({"error": str(exc)}, status_code=502)


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
