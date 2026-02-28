"""Plugin discovery and registration for az-scout.

At startup the application calls :func:`discover_plugins` to find all
installed packages that expose an ``az_scout.plugins`` entry point.
Then :func:`register_plugins` wires up routes, static files, MCP tools,
and chat modes contributed by each plugin.
"""

import importlib.metadata
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from az_scout.plugin_api import AzScoutPlugin, ChatMode

logger = logging.getLogger(__name__)

# Module-level registries populated by register_plugins()
_loaded_plugins: list[AzScoutPlugin] = []
_plugin_dist_names: dict[str, str] = {}  # plugin.name → pip distribution name
_plugin_chat_modes: dict[str, ChatMode] = {}


def discover_plugins() -> list[AzScoutPlugin]:
    """Discover installed plugins via the ``az_scout.plugins`` entry-point group."""
    plugins: list[AzScoutPlugin] = []
    for ep in importlib.metadata.entry_points(group="az_scout.plugins"):
        try:
            obj = ep.load()
            if isinstance(obj, AzScoutPlugin):
                plugins.append(obj)
                # Remember the pip distribution name for metadata lookups
                if ep.dist is not None:
                    _plugin_dist_names[obj.name] = ep.dist.name
                logger.info("Loaded plugin: %s v%s", obj.name, obj.version)
            else:
                logger.warning(
                    "Plugin entry point '%s' does not satisfy AzScoutPlugin protocol — skipped",
                    ep.name,
                )
        except Exception:
            logger.exception("Failed to load plugin entry point: %s", ep.name)
    return plugins


def register_plugins(app: FastAPI, mcp_server: Any) -> list[AzScoutPlugin]:
    """Discover and register all plugins with the FastAPI app and MCP server.

    Returns the list of successfully registered plugins.
    """
    plugins = discover_plugins()
    for plugin in plugins:
        _register_one(app, mcp_server, plugin)

    _loaded_plugins.clear()
    _loaded_plugins.extend(plugins)

    # Rebuild AI chat tool definitions so plugin MCP tools are available
    if plugins:
        try:
            from az_scout.services.ai_chat import refresh_tool_definitions

            refresh_tool_definitions()
            logger.info("Refreshed AI chat tool definitions with plugin tools")
        except ImportError:
            pass  # ai_chat module not available (e.g. no OpenAI config)

    return plugins


def _register_one(app: FastAPI, mcp_server: Any, plugin: AzScoutPlugin) -> None:
    """Wire a single plugin into the application."""
    name = plugin.name

    # API routes
    try:
        router = plugin.get_router()
        if router is not None:
            app.include_router(router, prefix=f"/plugins/{name}", tags=[f"Plugin: {name}"])
            logger.info("Registered API routes for plugin '%s'", name)
    except Exception:
        logger.exception("Failed to register routes for plugin '%s'", name)

    # Static assets
    try:
        static_dir = plugin.get_static_dir()
        if static_dir is not None:
            app.mount(
                f"/plugins/{name}/static",
                StaticFiles(directory=str(static_dir)),
                name=f"plugin-{name}-static",
            )
            logger.info("Mounted static assets for plugin '%s'", name)
    except Exception:
        logger.exception("Failed to mount static assets for plugin '%s'", name)

    # MCP tools
    try:
        tools = plugin.get_mcp_tools()
        if tools:
            for fn in tools:
                mcp_server.tool()(fn)
            logger.info("Registered %d MCP tool(s) for plugin '%s'", len(tools), name)
    except Exception:
        logger.exception("Failed to register MCP tools for plugin '%s'", name)

    # Chat modes
    try:
        modes = plugin.get_chat_modes()
        if modes:
            for mode in modes:
                _plugin_chat_modes[mode.id] = mode
            logger.info("Registered %d chat mode(s) for plugin '%s'", len(modes), name)
    except Exception:
        logger.exception("Failed to register chat modes for plugin '%s'", name)


def get_loaded_plugins() -> list[AzScoutPlugin]:
    """Return the list of plugins loaded at startup."""
    return list(_loaded_plugins)


def get_plugin_chat_modes() -> dict[str, ChatMode]:
    """Return all chat modes contributed by plugins, keyed by mode ID."""
    return dict(_plugin_chat_modes)


def _get_plugin_homepage(plugin_name: str) -> str:
    """Look up the Homepage URL from a plugin's pip distribution metadata."""
    dist_name = _plugin_dist_names.get(plugin_name)
    if not dist_name:
        return ""
    try:
        dist = importlib.metadata.distribution(dist_name)
        for raw in dist.metadata.get_all("Project-URL") or []:
            label, _, url = raw.partition(",")
            if label.strip().lower() == "homepage":
                return str(url).strip()
    except importlib.metadata.PackageNotFoundError:
        pass
    return ""


def get_plugin_metadata() -> list[dict[str, Any]]:
    """Return serialisable metadata for all loaded plugins (for template context)."""
    result: list[dict[str, Any]] = []
    for p in _loaded_plugins:
        tabs = p.get_tabs() or []
        modes = p.get_chat_modes() or []
        result.append(
            {
                "name": p.name,
                "version": p.version,
                "homepage": _get_plugin_homepage(p.name),
                "tabs": [
                    {
                        "id": t.id,
                        "label": t.label,
                        "icon": t.icon,
                        "js_entry": t.js_entry,
                        "css_entry": t.css_entry,
                    }
                    for t in tabs
                ],
                "chat_modes": [
                    {
                        "id": m.id,
                        "label": m.label,
                        "welcome_message": m.welcome_message,
                    }
                    for m in modes
                ],
            }
        )
    return result
