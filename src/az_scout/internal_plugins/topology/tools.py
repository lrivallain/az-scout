"""MCP tools for the AZ Topology internal plugin."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field

from az_scout import azure_api


def get_zone_mappings(
    region: Annotated[str, Field(description="Azure region name (e.g. eastus).")],
    subscription_ids: Annotated[list[str], Field(description="List of subscription IDs to query.")],
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
) -> str:
    """Get logical-to-physical Availability Zone mappings.

    Shows how each subscription maps logical zone numbers (1, 2, 3) to
    physical zone identifiers (e.g. ``eastus-az1``).  This is essential
    for understanding whether two subscriptions share the same physical
    zone when they reference the same logical zone number.
    """
    result = azure_api.get_mappings(region, subscription_ids, tenant_id)
    return json.dumps(result, indent=2)
