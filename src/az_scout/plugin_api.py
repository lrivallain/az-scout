"""Public API surface for az-scout plugins.

A plugin is any pip-installable package that registers a
``az_scout.plugins`` entry point pointing to an object that
satisfies the :class:`AzScoutPlugin` protocol.

Example ``pyproject.toml`` entry::

    [project.entry-points."az_scout.plugins"]
    my_plugin = "az_scout_myplugin:plugin"
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter


def get_plugin_logger(plugin_name: str) -> logging.Logger:
    """Return a logger for a plugin, using the ``az_scout_<name>`` namespace.

    This ensures plugin logs share the same coloured format and category
    labelling (``plugin:<name>``) as the core application.

    Usage in a plugin module::

        from az_scout.plugin_api import get_plugin_logger
        logger = get_plugin_logger("batch-sku")
        logger.info("Loading data")  # → INFO [plugin:batch_sku] az_scout_batch_sku - Loading data
    """
    module_name = f"az_scout_{plugin_name.replace('-', '_')}"
    return logging.getLogger(module_name)


@dataclass
class TabDefinition:
    """Describes a UI tab contributed by a plugin."""

    id: str  # e.g. "cost-analysis"
    label: str  # e.g. "Cost Analysis"
    icon: str  # Bootstrap icon class, e.g. "bi bi-cash-coin"
    js_entry: str  # relative path to JS file inside the plugin's static dir
    css_entry: str | None = None  # optional CSS file path inside the plugin's static dir


@dataclass
class ChatMode:
    """Describes an AI chat mode contributed by a plugin."""

    id: str  # e.g. "cost-advisor"
    label: str  # e.g. "Cost Advisor"
    system_prompt: str  # system prompt for this mode
    welcome_message: str  # markdown shown on mode activation


@dataclass
class NavbarAction:
    """Describes a navbar button + offcanvas panel contributed by a plugin."""

    id: str  # unique ID, e.g. "bdd-sku"
    icon: str  # Bootstrap icon class, e.g. "bi bi-database"
    label: str  # tooltip + offcanvas header, e.g. "SKU DB Cache"
    js_entry: str  # relative path to JS file inside the plugin's static dir
    css_entry: str | None = None  # optional CSS file path
    width: int = 480  # offcanvas panel width in px


@runtime_checkable
class AzScoutPlugin(Protocol):
    """Protocol that every az-scout plugin must satisfy.

    Each method is optional — return ``None`` (or omit) to skip a layer.
    """

    name: str
    version: str

    def get_router(self) -> APIRouter | None: ...
    def get_mcp_tools(self) -> list[Callable[..., Any]] | None: ...
    def get_static_dir(self) -> Path | None: ...
    def get_tabs(self) -> list[TabDefinition] | None: ...
    def get_chat_modes(self) -> list[ChatMode] | None: ...
    def get_navbar_actions(self) -> list[NavbarAction] | None: ...


@runtime_checkable
class AzScoutPromptContributor(Protocol):
    """Optional capability for plugins that augment the default chat system prompt.

    Implement this protocol only when your plugin needs to add extra guidance
    to the built-in ``discussion`` chat mode.
    """

    def get_system_prompt_addendum(self) -> str | None: ...


# ---------------------------------------------------------------------------
# AI completion helper — re-exported for plugin convenience
# ---------------------------------------------------------------------------


def is_ai_enabled() -> bool:
    """Return True if AI chat/completion is configured and available."""
    from az_scout.services.ai_chat._config import is_chat_enabled

    return is_chat_enabled()


async def plugin_ai_complete(
    prompt: str,
    *,
    system_prompt: str | None = None,
    tenant_id: str | None = None,
    region: str | None = None,
    subscription_id: str | None = None,
    tools: bool = True,
    cache_ttl: int = 300,
) -> dict[str, Any]:
    """Run a non-streaming AI completion with optional tool calling.

    Convenience wrapper for plugins that need inline AI recommendations
    outside the chat panel.  Returns a dict with ``content`` (str) and
    ``tool_calls`` (list).

    Example usage in a plugin route::

        from az_scout.plugin_api import plugin_ai_complete

        @router.get("/recommend")
        async def recommend(subscription_id: str, region: str):
            result = await plugin_ai_complete(
                "Analyse the VMs in this subscription and recommend capacity reservations.",
                system_prompt="You are a capacity planning expert.",
                subscription_id=subscription_id,
                region=region,
            )
            return {"recommendation": result["content"]}
    """
    from az_scout.services.ai_chat._complete import ai_complete

    r = await ai_complete(
        prompt,
        system_prompt=system_prompt,
        tenant_id=tenant_id,
        region=region,
        subscription_id=subscription_id,
        tools=tools,
        cache_ttl=cache_ttl,
    )
    return {"content": r.content, "tool_calls": r.tool_calls}


# ---------------------------------------------------------------------------
# Plugin error boundary — typed exceptions for automatic error responses
# ---------------------------------------------------------------------------


class PluginError(Exception):
    """Base exception for plugin route errors.

    Raise from a plugin route handler to produce a consistent JSON error
    response without manual try/except boilerplate.  The global exception
    handler in ``app.py`` catches ``PluginError`` and returns::

        {"error": "<message>", "detail": "<message>"}

    with the HTTP status code specified by ``status_code`` (default 500).

    Subclasses:

    * :class:`PluginValidationError` — 422 (client sent bad input)
    * :class:`PluginUpstreamError` — 502 (upstream API failure)
    """

    status_code: int = 500

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class PluginValidationError(PluginError):
    """Raised when a plugin receives invalid input (HTTP 422)."""

    status_code: int = 422


class PluginUpstreamError(PluginError):
    """Raised when an upstream API call fails (HTTP 502)."""

    status_code: int = 502
