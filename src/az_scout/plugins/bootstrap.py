"""Plugin bootstrap – discover, validate, and register plugins at startup.

Called once during the FastAPI lifespan to:

1. Discover plugins via entry points + the built-in "public" plugin.
2. Mount routers, static directories, MCP tools.
3. Collect tab / chat-mode metadata for the Jinja2 template context.

The resulting :class:`~az_scout.plugins.registry.PluginRegistry` is stored
on ``app.state.plugin_registry`` so routes and templates can access it.
"""

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from az_scout.plugins.api import AzScoutPlugin
from az_scout.plugins.discovery import discover_plugins
from az_scout.plugins.registry import PluginRegistry, mcp_tool_name_global
from az_scout.services.ai_chat import register_chat_mode_prompt

logger = logging.getLogger(__name__)


async def register_plugins(
    app: FastAPI,
    mcp: FastMCP,
    app_state: dict[str, Any],
) -> PluginRegistry:
    """Discover, initialise, and register all plugins.

    Returns a :class:`PluginRegistry` and attaches it to ``app.state``.
    """
    registry = PluginRegistry()

    # 1) Built-in "public" plugin (always loaded, even without pip install)
    from az_scout.plugins.public import PublicPlugin

    builtins: list[AzScoutPlugin] = [PublicPlugin()]

    # 2) External plugins discovered via entry points
    externals = discover_plugins()

    all_plugins = builtins + externals
    # Sort once by priority (builtins already have low priority)
    all_plugins.sort(key=lambda p: p.priority)

    started: list[AzScoutPlugin] = []
    for plugin in all_plugins:
        if not registry.add_plugin(plugin):
            continue  # collision

        # Lifecycle – startup
        try:
            await plugin.startup(app_state)
        except Exception:
            logger.exception("Plugin '%s' startup failed", plugin.plugin_id)
            continue

        started.append(plugin)
        _mount_router(app, plugin)
        _mount_static(app, plugin)
        _register_tabs(registry, plugin)
        _register_chat_modes(registry, plugin)
        _register_mcp_tools(registry, mcp, plugin)

    app.state.plugin_registry = registry
    app.state._plugin_instances = started
    logger.info(
        "Plugin bootstrap complete – %d plugin(s), %d tab(s), %d tool(s)",
        len(registry.plugins),
        len(registry.tabs),
        len(registry.tools),
    )
    return registry


async def shutdown_plugins(
    app: FastAPI,
    app_state: dict[str, Any],
) -> None:
    """Call ``shutdown()`` on every registered plugin."""
    registry: PluginRegistry | None = getattr(app.state, "plugin_registry", None)
    if registry is None:
        return
    # Re-resolve plugin instances – we need the actual objects, not PluginInfo.
    # For safety we store them during register_plugins.
    instances: list[AzScoutPlugin] = getattr(app.state, "_plugin_instances", [])
    for plugin in instances:
        try:
            await plugin.shutdown(app_state)
        except Exception:
            logger.exception("Plugin '%s' shutdown failed", plugin.plugin_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mount_router(app: FastAPI, plugin: AzScoutPlugin) -> None:
    router = plugin.get_router()
    if router is None:
        return
    prefix = f"/plugins/{plugin.plugin_id}"
    app.include_router(router, prefix=prefix)
    logger.debug("Mounted router for '%s' at %s", plugin.plugin_id, prefix)


def _mount_static(app: FastAPI, plugin: AzScoutPlugin) -> None:
    static_dir = plugin.get_static_dir()
    if static_dir is None or not static_dir.is_dir():
        return
    path = f"/plugins/{plugin.plugin_id}/static"
    static_name = f"plugin-static-{plugin.plugin_id}"
    app.mount(path, StaticFiles(directory=str(static_dir)), name=static_name)
    logger.debug("Mounted static dir for '%s' at %s", plugin.plugin_id, path)


def _register_tabs(registry: PluginRegistry, plugin: AzScoutPlugin) -> None:
    tabs = plugin.get_tabs()
    if not tabs:
        return
    for tab in tabs:
        registry.add_tab(plugin, tab)


def _register_chat_modes(registry: PluginRegistry, plugin: AzScoutPlugin) -> None:
    modes = plugin.get_chat_modes()
    if not modes:
        return
    for mode in modes:
        registered = registry.add_chat_mode(plugin, mode)
        if registered and mode.system_prompt:
            register_chat_mode_prompt(registered.global_id, mode.system_prompt)


def _register_mcp_tools(
    registry: PluginRegistry,
    mcp: FastMCP,
    plugin: AzScoutPlugin,
) -> None:
    tools = plugin.get_mcp_tools()
    if not tools:
        return
    for tool_def in tools:
        if not registry.add_tool(plugin, tool_def):
            continue  # collision
        global_name = mcp_tool_name_global(plugin.plugin_id, tool_def.name)
        # Register on the FastMCP server with the global name
        mcp.tool(name=global_name, description=tool_def.description)(tool_def.fn)
        logger.debug("Registered MCP tool '%s'", global_name)
