"""Public API surface for az-scout plugins.

A plugin is any pip-installable package that registers a
``az_scout.plugins`` entry point pointing to an object that
satisfies the :class:`AzScoutPlugin` protocol.

Example ``pyproject.toml`` entry::

    [project.entry-points."az_scout.plugins"]
    my_plugin = "az_scout_myplugin:plugin"
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter


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
