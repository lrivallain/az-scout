"""Built-in "public" plugin – always available, no authentication required.

Provides capacity strategy, retail pricing, and latency signals using only
public (unauthenticated) Azure APIs and static datasets.  No subscription,
quota, or policy signals are available in this mode.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from az_scout import __version__
from az_scout.plugins.api import (
    AzScoutPlugin as AzScoutPlugin,
)
from az_scout.plugins.api import (
    ChatMode,
    McpToolDef,
    PluginCapabilities,
    TabDefinition,
)


class PublicPlugin:
    """Built-in plugin exposing public (unauthenticated) capacity signals."""

    plugin_id: str = "public"
    name: str = "Public Signals"
    version: str = __version__
    priority: int = 10  # low = loaded first

    def get_capabilities(self) -> PluginCapabilities:
        return PluginCapabilities(mode="public", requires_auth=False)

    def get_router(self) -> APIRouter | None:
        from az_scout.plugins.public.router import router

        return router

    def get_mcp_tools(self) -> list[McpToolDef] | None:
        from az_scout.plugins.public.mcp_tools import get_tool_definitions

        return get_tool_definitions()

    def get_static_dir(self) -> Path | None:
        return Path(__file__).resolve().parent / "static"

    def get_tabs(self) -> list[TabDefinition] | None:
        return [
            TabDefinition(
                id="public-signals",
                label="Public Signals",
                icon="bi-graph-up",
                js_entries=["js/public-signals.js"],
            ),
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        return [
            ChatMode(
                id="capacity-advisor",
                label="Capacity (Public)",
                system_prompt=(
                    "You are **Azure Scout – Public Capacity Advisor**, an assistant "
                    "that helps users explore Azure VM capacity using only publicly "
                    "available signals (retail pricing and inter-region latency).\n\n"
                    "**Important limitations:**\n"
                    "- You do NOT have access to any Azure subscription or tenant.\n"
                    "- You cannot check quotas, policies, zone mappings, or spot "
                    "placement scores.\n"
                    "- All pricing is indicative (published retail list prices).\n"
                    "- Latency values are approximate (Microsoft published statistics).\n\n"
                    "Use the `public.pricing`, `public.latency_matrix`, and "
                    "`public.capacity_strategy` tools to answer questions.\n\n"
                    "Always include disclaimers about the limitations of public-only "
                    "signals.  Never present results as guarantees."
                ),
                welcome_message=(
                    "Welcome to the **Public Capacity Advisor**!  I can help you "
                    "explore Azure VM pricing and regional capacity using publicly "
                    "available data.\n\n"
                    "⚠️ **Limitations:** No subscription context — quota, policy, "
                    "spot placement scores, and zone mappings are not available.  "
                    "Results are indicative and not a guarantee.\n\n"
                    "Try asking:\n"
                    "- *What's the pricing for Standard_D4s_v5 in westeurope?*\n"
                    "- *Compare latency between francecentral and eastus*\n"
                    "- *Recommend regions for 10 instances of Standard_E8s_v4*"
                ),
            ),
        ]

    async def startup(self, app_state: dict[str, Any]) -> None:
        """No-op – public plugin needs no initialisation."""

    async def shutdown(self, app_state: dict[str, Any]) -> None:
        """No-op."""
