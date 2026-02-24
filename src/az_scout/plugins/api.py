"""Plugin API contract – protocol, dataclasses, and capability definitions.

Every az-scout plugin must satisfy the :class:`AzScoutPlugin` runtime-checkable
protocol.  The dataclasses here describe the "manifest" objects that plugins
return so the core can register routes, tabs, tools, and chat modes without
knowing anything about the plugin's internals.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter  # noqa: TC002

# ---------------------------------------------------------------------------
# Descriptors returned by plugin methods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TabDefinition:
    """A UI tab contributed by a plugin.

    ``id`` is the *local* tab name (e.g. ``"public-signals"``).
    The global / DOM ids are derived by :mod:`az_scout.plugins.registry`.
    """

    id: str
    label: str
    icon: str  # Bootstrap Icons class, e.g. "bi-graph-up"
    js_entries: list[str]
    css_entries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChatMode:
    """An AI-chat mode contributed by a plugin."""

    id: str
    label: str
    system_prompt: str
    welcome_message: str


@dataclass(frozen=True)
class McpToolDef:
    """An MCP tool contributed by a plugin.

    ``name`` is the *local* tool name (e.g. ``"pricing"``).  The global
    name used in the MCP server will be ``{plugin_id}.{name}``.
    """

    name: str
    description: str
    fn: Callable[..., Any]


@dataclass(frozen=True)
class PluginCapabilities:
    """Declares the authentication / access mode a plugin requires."""

    mode: str  # "public" | "tenant_app_only" | "tenant_obo" | "other"
    requires_auth: bool = False


# ---------------------------------------------------------------------------
# Plugin protocol (runtime-checkable)
# ---------------------------------------------------------------------------


@runtime_checkable
class AzScoutPlugin(Protocol):
    """Contract every az-scout plugin must satisfy.

    All methods are *optional* in the sense that they may return ``None``
    to indicate the plugin does not contribute that capability.  The
    lifecycle hooks (``startup`` / ``shutdown``) default to no-ops.
    """

    plugin_id: str  # stable slug used for namespacing
    name: str  # human-readable display name
    version: str
    priority: int  # lower = loaded first (default 100)

    def get_capabilities(self) -> PluginCapabilities: ...

    # Optional contributions – return None to skip
    def get_router(self) -> APIRouter | None: ...
    def get_mcp_tools(self) -> list[McpToolDef] | None: ...
    def get_static_dir(self) -> Path | None: ...
    def get_tabs(self) -> list[TabDefinition] | None: ...
    def get_chat_modes(self) -> list[ChatMode] | None: ...

    # Lifecycle hooks (default no-ops)
    async def startup(self, app_state: dict[str, Any]) -> None: ...
    async def shutdown(self, app_state: dict[str, Any]) -> None: ...
