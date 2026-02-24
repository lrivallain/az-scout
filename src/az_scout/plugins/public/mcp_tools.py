"""MCP tool definitions for the public-signals plugin.

Each function here becomes an MCP tool registered with the global name
``public.<local_name>`` (e.g. ``public.pricing``).
"""

import json
from typing import Annotated

from pydantic import Field

from az_scout.plugins.api import McpToolDef
from az_scout.plugins.public.services import (
    get_latency_matrix,
    get_public_pricing,
    public_capacity_strategy,
)

# ---------------------------------------------------------------------------
# Tool functions (called by the MCP server)
# ---------------------------------------------------------------------------


async def _pricing(
    region: Annotated[str, Field(description="Azure region name (e.g. 'eastus')")],
    sku_name: Annotated[
        str | None,
        Field(description="Optional SKU name filter (case-insensitive substring)"),
    ] = None,
    currency: Annotated[str, Field(description="Currency code: USD or EUR")] = "USD",
) -> str:
    """Return retail VM pricing (PayGo + Spot) from the Azure Retail Prices API.

    No authentication required.  Results are indicative list prices.
    """
    result = get_public_pricing(sku_name=sku_name, region=region, currency=currency)
    return json.dumps(result, indent=2)


async def _latency_matrix(
    regions: Annotated[
        list[str],
        Field(description="List of Azure region names to compare"),
    ],
) -> str:
    """Return indicative RTT latency matrix between Azure regions.

    Based on Microsoft published network latency statistics.
    """
    result = get_latency_matrix(regions)
    return json.dumps(result, indent=2)


async def _capacity_strategy(
    sku_name: Annotated[str, Field(description="VM SKU name (e.g. 'Standard_D2s_v3')")],
    instance_count: Annotated[int, Field(description="Number of VM instances")] = 1,
    regions: Annotated[
        list[str] | None,
        Field(description="Candidate regions (auto-selected if omitted)"),
    ] = None,
    currency: Annotated[str, Field(description="Currency code: USD or EUR")] = "USD",
    prefer_spot: Annotated[bool, Field(description="Prefer Spot VMs when available")] = False,
    require_zones: Annotated[bool, Field(description="Require Availability Zones")] = False,
    max_regions: Annotated[int, Field(description="Maximum number of regions to recommend")] = 3,
    latency_sensitive: Annotated[bool, Field(description="Workload is latency-sensitive")] = False,
    target_countries: Annotated[
        list[str] | None,
        Field(description="Target countries (ISO 2-letter codes, e.g. ['FR', 'DE'])"),
    ] = None,
) -> str:
    """Deterministic capacity recommendation using only public signals.

    Uses retail pricing and inter-region latency.  Missing signals include
    subscription, quota, policy, and spot placement scores.
    """
    result = public_capacity_strategy(
        sku_name=sku_name,
        instance_count=instance_count,
        regions=regions,
        currency=currency,
        prefer_spot=prefer_spot,
        require_zones=require_zones,
        max_regions=max_regions,
        latency_sensitive=latency_sensitive,
        target_countries=target_countries,
    )
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tool definitions exported to the plugin
# ---------------------------------------------------------------------------


def get_tool_definitions() -> list[McpToolDef]:
    """Return the MCP tool definitions for the public plugin."""
    return [
        McpToolDef(
            name="pricing",
            description=(
                "Return retail VM pricing (PayGo + Spot) for a region. "
                "No authentication required. Indicative list prices only."
            ),
            fn=_pricing,
        ),
        McpToolDef(
            name="latency_matrix",
            description=(
                "Return indicative RTT latency matrix between Azure regions. "
                "Based on Microsoft published statistics."
            ),
            fn=_latency_matrix,
        ),
        McpToolDef(
            name="capacity_strategy",
            description=(
                "Deterministic capacity recommendation using public signals only "
                "(retail pricing + inter-region latency). No subscription or quota "
                "data — missing signals are listed explicitly."
            ),
            fn=_capacity_strategy,
        ),
    ]
