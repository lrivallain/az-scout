"""Plugin manager API routes – thin wrappers over :mod:`az_scout.plugin_manager`."""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from az_scout import plugin_manager
from az_scout.plugin_manager._installer import has_new_native_extensions, snapshot_native_files
from az_scout.plugins import (
    _plugin_dist_names,
    get_loaded_plugins,
    is_in_packages_dir,
    reload_plugins,
)

_RESTART_WARNING = (
    "This plugin installed native compiled extensions (e.g. numpy). "
    "A container restart is required for them to work correctly."
)

router = APIRouter(prefix="/api/plugins", tags=["Plugin Manager"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    repo_url: str  # GitHub URL or PyPI package name
    ref: str = ""  # Version/ref — optional for PyPI (auto-resolves to latest)


class InstallRequest(BaseModel):
    repo_url: str  # GitHub URL or PyPI package name
    ref: str = ""  # Version/ref — optional for PyPI (auto-resolves to latest)


class UninstallRequest(BaseModel):
    distribution_name: str


class UpdateRequest(BaseModel):
    distribution_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor(request: Request) -> tuple[str, str, str]:
    """Extract actor, client_ip, and user_agent from a request."""
    # Use OBO session user if available, else EasyAuth header
    from az_scout.routes.auth import get_session

    session = get_session(request)
    if session:
        actor = session.get("user_email", session.get("user_name", "anonymous"))
    else:
        actor = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "anonymous")
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("User-Agent", "")
    return actor, client_ip, user_agent


def _require_admin(request: Request) -> None:
    """Raise 403 if the current user is not an admin.

    Admin role is only valid from the home tenant — roles from other
    tenants are ignored. When OBO is not enabled, all users are treated
    as admin (single-user / managed identity mode).
    """
    from az_scout.azure_api._obo import is_obo_enabled
    from az_scout.routes.auth import get_session

    if not is_obo_enabled():
        return  # No auth configured — all users are admin

    session = get_session(request)
    if not session or not session.get("is_admin", False):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin role required")


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
            "loaded": [
                {
                    "name": p.name,
                    "display_name": getattr(p, "display_name", ""),
                    "version": p.version,
                    "internal": bool(getattr(p, "internal", False)),
                    "distribution_name": _plugin_dist_names.get(p.name, ""),
                    "description": getattr(p, "description", ""),
                    "in_packages_dir": is_in_packages_dir(_plugin_dist_names.get(p.name, "")),
                }
                for p in loaded
            ],
        }
    )


@router.post("/validate", summary="Validate a plugin source")
async def validate_plugin(body: ValidateRequest, request: Request) -> JSONResponse:
    """Validate a plugin from a GitHub repository or PyPI package."""
    _require_admin(request)
    if plugin_manager.is_pypi_source(body.repo_url):
        result = await asyncio.to_thread(
            plugin_manager.validate_pypi_plugin, body.repo_url.strip(), body.ref.strip()
        )
    else:
        result = await asyncio.to_thread(
            plugin_manager.validate_plugin_repo, body.repo_url, body.ref.strip()
        )
    return JSONResponse(asdict(result))


@router.post("/install", summary="Install a plugin")
async def install_plugin(body: InstallRequest, request: Request) -> JSONResponse:
    """Install a plugin from a GitHub repository or PyPI."""
    _require_admin(request)
    actor, client_ip, user_agent = _actor(request)
    before = snapshot_native_files()
    if plugin_manager.is_pypi_source(body.repo_url):
        ok, warnings, errors = await asyncio.to_thread(
            plugin_manager.install_pypi_plugin,
            body.repo_url.strip(),
            body.ref.strip(),
            actor,
            client_ip,
            user_agent,
        )
    else:
        ok, warnings, errors = await asyncio.to_thread(
            plugin_manager.install_plugin,
            body.repo_url,
            body.ref.strip(),
            actor,
            client_ip,
            user_agent,
        )
    restart_required = ok and has_new_native_extensions(before)
    if ok:
        reload_plugins(request.app, request.app.state.mcp_server)
    if restart_required:
        warnings.append(_RESTART_WARNING)
    return JSONResponse(
        {
            "ok": ok,
            "warnings": warnings,
            "errors": errors,
            "restart_required": restart_required,
        },
    )


@router.post("/uninstall", summary="Uninstall a plugin")
async def uninstall_plugin(body: UninstallRequest, request: Request) -> JSONResponse:
    """Uninstall a plugin by its distribution name."""
    _require_admin(request)
    actor, client_ip, user_agent = _actor(request)
    ok, errors = await asyncio.to_thread(
        plugin_manager.uninstall_plugin,
        body.distribution_name,
        actor,
        client_ip,
        user_agent,
    )
    if ok:
        reload_plugins(request.app, request.app.state.mcp_server)
    return JSONResponse(
        {
            "ok": ok,
            "errors": errors,
        },
    )


@router.get("/updates", summary="Check for plugin updates")
async def check_updates(request: Request) -> JSONResponse:
    """Check all installed plugins for available updates."""
    actor, client_ip, user_agent = _actor(request)
    results = await asyncio.to_thread(plugin_manager.check_updates, actor, client_ip, user_agent)
    return JSONResponse({"plugins": results})


@router.post("/update", summary="Update a single plugin")
async def update_plugin(body: UpdateRequest, request: Request) -> JSONResponse:
    """Update a single plugin to the latest GitHub release/tag."""
    _require_admin(request)
    actor, client_ip, user_agent = _actor(request)
    before = snapshot_native_files()
    ok, errors = await asyncio.to_thread(
        plugin_manager.update_plugin,
        body.distribution_name,
        actor,
        client_ip,
        user_agent,
    )
    restart_required = ok and has_new_native_extensions(before)
    if ok:
        reload_plugins(request.app, request.app.state.mcp_server)
    return JSONResponse(
        {
            "ok": ok,
            "errors": errors,
            "restart_required": restart_required,
        },
    )


@router.get("/recommended", summary="List recommended plugins")
async def list_recommended() -> JSONResponse:
    """Return the curated list of recommended plugins with install status."""
    plugins = plugin_manager.load_recommended_plugins()
    return JSONResponse({"plugins": plugins})


@router.post("/update-all", summary="Update all plugins")
async def update_all_plugins(request: Request) -> JSONResponse:
    """Update all installed plugins that have available updates."""
    _require_admin(request)
    actor, client_ip, user_agent = _actor(request)
    before = snapshot_native_files()
    updated, failed, details = await asyncio.to_thread(
        plugin_manager.update_all_plugins,
        actor,
        client_ip,
        user_agent,
    )
    restart_required = updated > 0 and has_new_native_extensions(before)
    if updated > 0:
        reload_plugins(request.app, request.app.state.mcp_server)
    return JSONResponse(
        {
            "ok": failed == 0,
            "updated": updated,
            "failed": failed,
            "details": details,
            "restart_required": restart_required,
        },
    )
