"""AZ Topology – internal plugin for zone mapping visualisation.

Provides the ``/api/mappings`` endpoint, the ``get_zone_mappings`` MCP tool,
and the AZ Topology tab UI (D3.js graph + table).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from az_scout import __version__
from az_scout.plugin_api import AzScoutPlugin, ChatMode, NavbarAction, TabDefinition

_STATIC_DIR = Path(__file__).parent / "static"


class TopologyPlugin:
    """Internal plugin: AZ Topology tab."""

    name = "topology"
    display_name = "AZ Topology"
    version = __version__
    internal = True  # Flag for registration logic
    description = "Visualize logical-to-physical availability zone mappings across subscriptions."

    def get_router(self) -> APIRouter | None:
        from az_scout.internal_plugins.topology.routes import router

        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        from az_scout.internal_plugins.topology.tools import get_zone_mappings

        return [get_zone_mappings]

    def get_static_dir(self) -> Path | None:
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        return [
            TabDefinition(
                id="topology",
                label="AZ Topology",
                icon="bi bi-diagram-3",
                js_entry="js/az-mapping.js",
                css_entry="css/topology.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        return None

    def get_navbar_actions(self) -> list[NavbarAction] | None:
        return None


plugin: AzScoutPlugin = TopologyPlugin()  # module-level instance
