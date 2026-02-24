"""Plugin registry – namespace management and collision detection.

The registry holds the aggregated metadata from all loaded plugins and
provides helper functions to compute global (namespaced) identifiers so
that multiple plugins can coexist without collisions.
"""

import logging
from dataclasses import dataclass, field

from az_scout.plugins.api import (
    AzScoutPlugin,
    ChatMode,
    McpToolDef,
    PluginCapabilities,
    TabDefinition,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------


def tab_id_global(plugin_id: str, tab_id: str) -> str:
    """Return the global tab identifier (for internal lookups)."""
    return f"{plugin_id}:{tab_id}"


def tab_dom_id(plugin_id: str, tab_id: str) -> str:
    """Return the DOM ``id`` attribute for a tab pane."""
    return f"tab-{plugin_id}-{tab_id}"


def tab_trigger_id(plugin_id: str, tab_id: str) -> str:
    """Return the DOM ``id`` for the tab's ``<button>`` trigger."""
    return f"{plugin_id}-{tab_id}-tab"


def chat_mode_id_global(plugin_id: str, mode_id: str) -> str:
    """Return the global chat-mode identifier."""
    return f"{plugin_id}:{mode_id}"


def mcp_tool_name_global(plugin_id: str, tool_name: str) -> str:
    """Return the global MCP tool name."""
    return f"{plugin_id}.{tool_name}"


# ---------------------------------------------------------------------------
# Registry dataclass
# ---------------------------------------------------------------------------


@dataclass
class _RegisteredTab:
    """Internal record for a tab contributed by a plugin."""

    plugin_id: str
    plugin_version: str
    definition: TabDefinition
    global_id: str
    dom_id: str
    trigger_id: str


@dataclass
class _RegisteredChatMode:
    """Internal record for a chat mode contributed by a plugin."""

    plugin_id: str
    definition: ChatMode
    global_id: str


@dataclass
class _RegisteredTool:
    """Internal record for an MCP tool contributed by a plugin."""

    plugin_id: str
    definition: McpToolDef
    global_name: str


@dataclass
class PluginInfo:
    """Serialisable summary of a loaded plugin (for /health or introspection)."""

    plugin_id: str
    name: str
    version: str
    priority: int
    capabilities: PluginCapabilities


@dataclass
class PluginRegistry:
    """Aggregated metadata from all loaded plugins.

    Built during :func:`~az_scout.plugins.bootstrap.register_plugins` and
    passed to the Jinja2 template context so the UI can render plugin-
    contributed tabs, CSS/JS assets, and chat modes.
    """

    plugins: list[PluginInfo] = field(default_factory=list)
    tabs: list[_RegisteredTab] = field(default_factory=list)
    chat_modes: list[_RegisteredChatMode] = field(default_factory=list)
    tools: list[_RegisteredTool] = field(default_factory=list)

    # Internal sets for collision detection (not serialised)
    _seen_plugin_ids: set[str] = field(default_factory=set, repr=False)
    _seen_tool_names: set[str] = field(default_factory=set, repr=False)

    # ---- mutation helpers (used during bootstrap) ----

    def add_plugin(self, plugin: AzScoutPlugin) -> bool:
        """Register a plugin.  Returns ``False`` if its ``plugin_id`` collides."""
        if plugin.plugin_id in self._seen_plugin_ids:
            logger.error(
                "Plugin id collision: '%s' already registered – skipping %s",
                plugin.plugin_id,
                plugin.name,
            )
            return False
        self._seen_plugin_ids.add(plugin.plugin_id)
        self.plugins.append(
            PluginInfo(
                plugin_id=plugin.plugin_id,
                name=plugin.name,
                version=plugin.version,
                priority=plugin.priority,
                capabilities=plugin.get_capabilities(),
            )
        )
        return True

    def add_tab(self, plugin: AzScoutPlugin, tab: TabDefinition) -> None:
        gid = tab_id_global(plugin.plugin_id, tab.id)
        did = tab_dom_id(plugin.plugin_id, tab.id)
        tid = tab_trigger_id(plugin.plugin_id, tab.id)
        self.tabs.append(
            _RegisteredTab(
                plugin_id=plugin.plugin_id,
                plugin_version=plugin.version,
                definition=tab,
                global_id=gid,
                dom_id=did,
                trigger_id=tid,
            )
        )

    def add_chat_mode(self, plugin: AzScoutPlugin, mode: ChatMode) -> _RegisteredChatMode:
        gid = chat_mode_id_global(plugin.plugin_id, mode.id)
        registered = _RegisteredChatMode(
            plugin_id=plugin.plugin_id,
            definition=mode,
            global_id=gid,
        )
        self.chat_modes.append(registered)
        return registered

    def add_tool(self, plugin: AzScoutPlugin, tool: McpToolDef) -> bool:
        """Register an MCP tool.  Returns ``False`` on name collision."""
        gname = mcp_tool_name_global(plugin.plugin_id, tool.name)
        if gname in self._seen_tool_names:
            logger.error("MCP tool name collision: '%s' – skipping", gname)
            return False
        self._seen_tool_names.add(gname)
        self.tools.append(
            _RegisteredTool(
                plugin_id=plugin.plugin_id,
                definition=tool,
                global_name=gname,
            )
        )
        return True
