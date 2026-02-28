"""Plugin manager API routes – thin wrappers over :mod:`az_scout.plugin_manager`."""

from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from az_scout import plugin_manager
from az_scout.plugins import get_loaded_plugins

router = APIRouter(prefix="/api/plugins", tags=["Plugin Manager"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    repo_url: str
    ref: str


class InstallRequest(BaseModel):
    repo_url: str
    ref: str


class UninstallRequest(BaseModel):
    distribution_name: str


class UpdateRequest(BaseModel):
    distribution_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor(request: Request) -> tuple[str, str, str]:
    """Extract actor, client_ip, and user_agent from a request."""
    actor = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "anonymous")
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("User-Agent", "")
    return actor, client_ip, user_agent


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", summary="List installed and loaded plugins")
async def list_plugins() -> JSONResponse:
    """Return UI-installed plugins and runtime-loaded plugins."""
    installed = plugin_manager.load_installed()
    loaded = get_loaded_plugins()
    return JSONResponse(
        {
            "installed": [asdict(r) for r in installed],
            "loaded": [{"name": p.name, "version": p.version} for p in loaded],
        }
    )


@router.post("/validate", summary="Validate a plugin repository")
async def validate_plugin(body: ValidateRequest) -> JSONResponse:
    """Fetch and validate ``pyproject.toml`` from a GitHub repository."""
    result = plugin_manager.validate_plugin_repo(body.repo_url, body.ref)
    return JSONResponse(asdict(result))


@router.post("/install", summary="Install a plugin")
async def install_plugin(body: InstallRequest, request: Request) -> JSONResponse:
    """Install a plugin from a GitHub repository at a pinned SHA."""
    actor, client_ip, user_agent = _actor(request)
    ok, warnings, errors = plugin_manager.install_plugin(
        body.repo_url,
        body.ref,
        actor,
        client_ip,
        user_agent,
    )
    return JSONResponse(
        {
            "ok": ok,
            "restart_required": ok,
            "warnings": warnings,
            "errors": errors,
        },
    )


@router.post("/uninstall", summary="Uninstall a plugin")
async def uninstall_plugin(body: UninstallRequest, request: Request) -> JSONResponse:
    """Uninstall a plugin by its distribution name."""
    actor, client_ip, user_agent = _actor(request)
    ok, errors = plugin_manager.uninstall_plugin(
        body.distribution_name,
        actor,
        client_ip,
        user_agent,
    )
    return JSONResponse(
        {
            "ok": ok,
            "restart_required": ok,
            "errors": errors,
        },
    )


@router.get("/updates", summary="Check for plugin updates")
async def check_updates(request: Request) -> JSONResponse:
    """Check all installed plugins for available updates."""
    actor, client_ip, user_agent = _actor(request)
    results = plugin_manager.check_updates(actor, client_ip, user_agent)
    return JSONResponse({"plugins": results})


@router.post("/update", summary="Update a single plugin")
async def update_plugin(body: UpdateRequest, request: Request) -> JSONResponse:
    """Update a single plugin to the latest GitHub release/tag."""
    actor, client_ip, user_agent = _actor(request)
    ok, errors = plugin_manager.update_plugin(
        body.distribution_name,
        actor,
        client_ip,
        user_agent,
    )
    return JSONResponse(
        {
            "ok": ok,
            "restart_required": ok,
            "errors": errors,
        },
    )


@router.post("/update-all", summary="Update all plugins")
async def update_all_plugins(request: Request) -> JSONResponse:
    """Update all installed plugins that have available updates."""
    actor, client_ip, user_agent = _actor(request)
    updated, failed, details = plugin_manager.update_all_plugins(
        actor,
        client_ip,
        user_agent,
    )
    return JSONResponse(
        {
            "ok": failed == 0,
            "restart_required": updated > 0,
            "updated": updated,
            "failed": failed,
            "details": details,
        },
    )
