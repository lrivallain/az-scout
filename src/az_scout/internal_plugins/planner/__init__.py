"""Deployment Planner – internal plugin for SKU analysis and deployment planning.

Provides SKU availability/pricing endpoints, deployment confidence scoring,
spot placement scores, deployment planning, and the Deployment Planner tab UI.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from az_scout import __version__
from az_scout.plugin_api import AzScoutPlugin, ChatMode, NavbarAction, TabDefinition

_STATIC_DIR = Path(__file__).parent / "static"


class PlannerPlugin:
    """Internal plugin: Deployment Planner tab."""

    name = "planner"
    display_name = "Deployment Planner"
    version = __version__
    internal = True
    description = (
        "Plan VM deployments with SKU availability, pricing, spot scores, and capacity confidence."
    )

    def get_router(self) -> APIRouter | None:
        from az_scout.internal_plugins.planner.routes import router

        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        from az_scout.internal_plugins.planner.tools import (
            get_sku_availability,
            get_sku_deployment_confidence,
            get_sku_pricing_detail,
            get_spot_scores,
        )

        return [
            get_sku_availability,
            get_sku_deployment_confidence,
            get_sku_pricing_detail,
            get_spot_scores,
        ]

    def get_static_dir(self) -> Path | None:
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        return [
            TabDefinition(
                id="planner",
                label="Deployment Planner",
                icon="bi bi-grid-3x3-gap",
                js_entry="js/planner.js",
                css_entry="css/planner.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        from az_scout.internal_plugins.planner.chat_mode import PLANNER_CHAT_MODE

        return [PLANNER_CHAT_MODE]

    def get_navbar_actions(self) -> list[NavbarAction] | None:
        return None


plugin: AzScoutPlugin = PlannerPlugin()
