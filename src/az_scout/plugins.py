"""Plugin discovery and registration for az-scout.

At startup the application calls :func:`discover_plugins` to find all
installed packages that expose an ``az_scout.plugins`` entry point.
Then :func:`register_plugins` wires up routes, static files, MCP tools,
and chat modes contributed by each plugin.

After install/uninstall/update, :func:`reload_plugins` performs an
in-process hot-reload: it tears down old routes, static mounts, MCP tools,
and chat modes, flushes the Python module cache for plugin packages, then
re-discovers and re-registers everything.

Plugins may be installed in the main environment or in the dedicated
``plugin-packages`` directory managed by the Plugin Manager UI.
"""

import importlib
import importlib.metadata
import logging
import sys
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from az_scout.internal_plugins import discover_internal_plugins
from az_scout.plugin_api import AzScoutPlugin, AzScoutPromptContributor, ChatMode
from az_scout.plugin_manager import _PACKAGES_DIR

logger = logging.getLogger(__name__)

# Module-level registries populated by register_plugins()
_loaded_plugins: list[AzScoutPlugin] = []
_plugin_dist_names: dict[str, str] = {}  # plugin.name → pip distribution name
_plugin_chat_modes: dict[str, ChatMode] = {}
_plugin_system_prompt_addenda: dict[str, str] = {}  # plugin.name → addendum text
_plugin_mcp_tool_names: dict[str, list[str]] = {}  # plugin.name → list of MCP tool names
_plugin_route_prefixes: set[str] = set()  # tracked prefixes for route cleanup


def _ensure_plugin_packages_on_path() -> None:
    """Add ``plugin-packages`` directory to ``sys.path`` if it exists."""
    if not _PACKAGES_DIR.exists():
        return
    pkg_str = str(_PACKAGES_DIR)
    if pkg_str not in sys.path:
        sys.path.insert(0, pkg_str)
        logger.info("Added plugin packages to sys.path: %s", pkg_str)


def _satisfies_plugin_protocol(obj: Any) -> bool:
    """Check if *obj* has the minimum attributes required by the plugin protocol.

    Uses a manual check instead of ``isinstance(obj, AzScoutPlugin)`` because
    ``runtime_checkable`` Protocols reject objects missing *any* method — even
    optional ones added in newer versions.  This allows older plugins (that
    don't implement newly-added optional methods like ``get_navbar_actions``)
    to still load correctly.
    """
    return hasattr(obj, "name") and hasattr(obj, "version") and hasattr(obj, "get_router")


def _discover_plugin_packages_entry_points() -> list[importlib.metadata.EntryPoint]:
    """Discover ``az_scout.plugins`` entry points from ``plugin-packages``."""
    if not _PACKAGES_DIR.exists():
        return []
    eps: list[importlib.metadata.EntryPoint] = []
    for dist in importlib.metadata.distributions(path=[str(_PACKAGES_DIR)]):
        for ep in dist.entry_points:
            if ep.group == "az_scout.plugins":
                eps.append(ep)
    return eps


def discover_plugins() -> list[AzScoutPlugin]:
    """Discover installed plugins via the ``az_scout.plugins`` entry-point group.

    Scans internal plugins first (shipped with the core package), then
    external plugins from the main environment and the ``plugin-packages``
    directory.
    """
    # Internal plugins – always loaded first
    plugins: list[AzScoutPlugin] = discover_internal_plugins()

    _ensure_plugin_packages_on_path()

    # Collect entry points from main env + plugin packages, deduplicating by name
    seen: set[str] = {p.name for p in plugins}
    all_eps: list[importlib.metadata.EntryPoint] = []

    for ep in importlib.metadata.entry_points(group="az_scout.plugins"):
        if ep.name not in seen:
            seen.add(ep.name)
            all_eps.append(ep)

    for ep in _discover_plugin_packages_entry_points():
        if ep.name not in seen:
            seen.add(ep.name)
            all_eps.append(ep)

    for ep in all_eps:
        try:
            obj = ep.load()
            if _satisfies_plugin_protocol(obj):
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


def _is_internal(plugin: AzScoutPlugin) -> bool:
    """Return True if *plugin* is an internal plugin (shipped with core)."""
    return bool(getattr(plugin, "internal", False))


def _register_one(app: FastAPI, mcp_server: Any, plugin: AzScoutPlugin) -> None:
    """Wire a single plugin into the application."""
    name = plugin.name
    internal = _is_internal(plugin)

    # Configure the plugin's logger to use the same format as core
    from az_scout.logging_config import setup_plugin_logger

    setup_plugin_logger(name)

    # API routes — internal plugins mount at /api, external at /plugins/{name}
    try:
        router = plugin.get_router()
        if router is not None:
            from az_scout.auth import require_auth

            prefix = "/api" if internal else f"/plugins/{name}"
            # Inject auth guard on all plugin API routes
            router.dependencies = [*router.dependencies, Depends(require_auth)]
            if internal:
                app.include_router(router, prefix=prefix)
            else:
                app.include_router(router, prefix=prefix, tags=[f"Plugin: {name}"])
            if internal:
                # Track individual route paths so _unregister_all doesn't nuke
                # all /api/* routes (which includes core routes).
                for route in router.routes:
                    route_path = prefix + getattr(route, "path", "")
                    _plugin_route_prefixes.add(route_path)
            else:
                _plugin_route_prefixes.add(prefix)
            logger.info("Registered API routes for plugin '%s' (internal=%s)", name, internal)
    except Exception:
        logger.exception("Failed to register routes for plugin '%s'", name)

    # Static assets — internal plugins mount at /internal/{name}/static
    try:
        static_dir = plugin.get_static_dir()
        if static_dir is not None:
            mount_path = f"/internal/{name}/static" if internal else f"/plugins/{name}/static"
            app.mount(
                mount_path,
                StaticFiles(directory=str(static_dir)),
                name=f"{'internal' if internal else 'plugin'}-{name}-static",
            )
            logger.info("Mounted static assets for plugin '%s'", name)
    except Exception:
        logger.exception("Failed to mount static assets for plugin '%s'", name)

    # MCP tools
    try:
        tools = plugin.get_mcp_tools()
        if tools:
            tool_names: list[str] = []
            for fn in tools:
                mcp_server.tool()(fn)
                tool_names.append(fn.__name__)
            _plugin_mcp_tool_names[name] = tool_names
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

    # Optional base system prompt addendum
    try:
        if isinstance(plugin, AzScoutPromptContributor):
            addendum = plugin.get_system_prompt_addendum()
            if addendum:
                normalized_addendum = addendum.strip()
                if normalized_addendum:
                    _plugin_system_prompt_addenda[name] = normalized_addendum
                    logger.info("Registered system prompt addendum for plugin '%s'", name)
    except Exception:
        logger.exception("Failed to register system prompt addendum for plugin '%s'", name)


def get_loaded_plugins() -> list[AzScoutPlugin]:
    """Return the list of plugins loaded at startup."""
    return list(_loaded_plugins)


def is_in_packages_dir(dist_name: str) -> bool:
    """Return True if *dist_name* is installed in the plugin packages directory."""
    if not dist_name or not _PACKAGES_DIR.exists():
        return False
    for dist in importlib.metadata.distributions(path=[str(_PACKAGES_DIR)]):
        if dist.name == dist_name:
            return True
    return False


def get_plugin_chat_modes() -> dict[str, ChatMode]:
    """Return all chat modes contributed by plugins, keyed by mode ID."""
    return dict(_plugin_chat_modes)


def get_plugin_system_prompt_addenda() -> list[str]:
    """Return registered base-system-prompt addenda from plugins.

    The list is sorted by plugin name for deterministic prompt assembly.
    """
    return [text for _, text in sorted(_plugin_system_prompt_addenda.items())]


# ---------------------------------------------------------------------------
# Hot-reload helpers
# ---------------------------------------------------------------------------


def _unregister_all(app: FastAPI, mcp_server: Any) -> None:
    """Remove all plugin routes, static mounts, MCP tools, and chat modes."""
    # Remove FastAPI routes whose path starts with a known plugin prefix
    prefixes_to_remove = set(_plugin_route_prefixes)
    # Also remove static mounts
    for plugin in _loaded_plugins:
        if _is_internal(plugin):
            prefixes_to_remove.add(f"/internal/{plugin.name}/static")
        else:
            prefixes_to_remove.add(f"/plugins/{plugin.name}/static")

    if prefixes_to_remove:
        app.routes[:] = [
            r
            for r in app.routes
            if not any(getattr(r, "path", "").startswith(p) for p in prefixes_to_remove)
        ]
        logger.debug("Removed plugin routes for prefixes: %s", prefixes_to_remove)

    # Remove MCP tools
    for tool_names in _plugin_mcp_tool_names.values():
        for name in tool_names:
            try:
                mcp_server.remove_tool(name)
                logger.debug("Removed MCP tool '%s'", name)
            except Exception:
                logger.debug("MCP tool '%s' already absent", name)

    # Clear registries
    _loaded_plugins.clear()
    _plugin_dist_names.clear()
    _plugin_chat_modes.clear()
    _plugin_system_prompt_addenda.clear()
    _plugin_mcp_tool_names.clear()
    _plugin_route_prefixes.clear()


def _flush_plugin_modules() -> None:
    """Remove plugin-related modules from ``sys.modules`` so reimport picks up new code."""
    if not _PACKAGES_DIR.exists():
        return
    pkg_str = str(_PACKAGES_DIR)
    stale = [
        name
        for name, mod in sys.modules.items()
        if mod is not None
        and hasattr(mod, "__file__")
        and mod.__file__ is not None
        and mod.__file__.startswith(pkg_str)
    ]
    for name in stale:
        del sys.modules[name]
    if stale:
        logger.info("Flushed %d plugin module(s) from sys.modules", len(stale))
    # Also invalidate importlib caches so fresh distributions are found
    importlib.invalidate_caches()


def reload_plugins(app: FastAPI, mcp_server: Any) -> list[AzScoutPlugin]:
    """Hot-reload all plugins: tear down, flush caches, re-discover, re-register.

    Call this after plugin install/uninstall/update to pick up changes
    without restarting the process.
    """
    logger.info("Hot-reloading plugins …")
    _unregister_all(app, mcp_server)
    _flush_plugin_modules()
    return register_plugins(app, mcp_server)


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
        actions = (p.get_navbar_actions() if hasattr(p, "get_navbar_actions") else None) or []
        internal = _is_internal(p)
        result.append(
            {
                "name": p.name,
                "version": p.version,
                "homepage": _get_plugin_homepage(p.name),
                "internal": internal,
                "static_prefix": (
                    f"/internal/{p.name}/static" if internal else f"/plugins/{p.name}/static"
                ),
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
                "navbar_actions": [
                    {
                        "id": a.id,
                        "icon": a.icon,
                        "label": a.label,
                        "js_entry": a.js_entry,
                        "css_entry": a.css_entry,
                        "width": a.width,
                    }
                    for a in actions
                ],
            }
        )
    return result
