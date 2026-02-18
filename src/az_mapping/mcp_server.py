"""MCP server for Azure Availability Zone Mapping.

Exposes the same Azure ARM capabilities as the web UI – tenants,
subscriptions, regions, zone mappings and SKU availability – as
MCP tools so that AI agents can query them directly.

Run with:
    az-mapping mcp            # stdio transport (default)
    az-mapping mcp --sse      # SSE transport on port 8080

Or add to your MCP client config (e.g. Claude Desktop):
    {
      "mcpServers": {
        "az-mapping": {
          "command": "az-mapping",
          "args": ["mcp"]
        }
      }
    }
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from az_mapping import azure_api
from az_mapping.services.capacity_confidence import compute_capacity_confidence

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "az-mapping",
    instructions=(
        "Azure Availability Zone mapping tools. "
        "Use these tools to discover Azure tenants, subscriptions and regions, "
        "then query logical-to-physical zone mappings and VM SKU availability. "
        "All tools require valid Azure credentials via DefaultAzureCredential "
        "(e.g. `az login`)."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_tenants() -> str:
    """List Azure AD tenants accessible by the current credential.

    Returns all tenants with their authentication status and the default
    tenant ID for the current auth context.  Use this first to discover
    available tenants before querying subscriptions.
    """
    result = azure_api.list_tenants()
    return json.dumps(result, indent=2)


@mcp.tool()
def list_subscriptions(tenant_id: str | None = None) -> str:
    """List enabled Azure subscriptions.

    Args:
        tenant_id: Optional tenant ID to scope the query. If omitted the
                   default tenant from the current credential is used.

    Returns a JSON array of ``{"id": ..., "name": ...}`` objects sorted
    alphabetically.
    """
    result = azure_api.list_subscriptions(tenant_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_regions(
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> str:
    """List Azure regions that support Availability Zones.

    Args:
        subscription_id: Subscription to query. If omitted the first enabled
                         subscription is auto-discovered.
        tenant_id: Optional tenant ID to scope the query.

    Returns a JSON array of ``{"name": ..., "displayName": ...}`` for each
    AZ-enabled region.
    """
    result = azure_api.list_regions(subscription_id, tenant_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def get_zone_mappings(
    region: str,
    subscription_ids: list[str],
    tenant_id: str | None = None,
) -> str:
    """Get logical-to-physical Availability Zone mappings.

    Shows how each subscription maps logical zone numbers (1, 2, 3) to
    physical zone identifiers (e.g. ``eastus-az1``).  This is essential
    for understanding whether two subscriptions share the same physical
    zone when they reference the same logical zone number.

    Args:
        region: Azure region name (e.g. ``eastus``, ``westeurope``).
        subscription_ids: List of subscription IDs to query.
        tenant_id: Optional tenant ID to scope the query.
    """
    result = azure_api.get_mappings(region, subscription_ids, tenant_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def get_sku_availability(
    region: str,
    subscription_id: str,
    tenant_id: str | None = None,
    resource_type: str = "virtualMachines",
    name: str | None = None,
    family: str | None = None,
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    min_memory_gb: float | None = None,
    max_memory_gb: float | None = None,
    include_prices: bool = False,
    currency_code: str = "USD",
) -> str:
    """Get VM SKU availability per zone for a region and subscription.

    Returns resource SKUs (VM sizes by default) with their zone availability,
    zone restrictions and key capabilities (vCPUs, memory).

    **Tip:** Use the filter parameters to reduce the output size – especially
    useful in conversational contexts. When no filters are provided, all SKUs
    for the resource type are returned.

    Zone status per SKU:
    - **available**: SKU can be deployed in that zone
    - **restricted**: SKU is listed but restricted (cannot be deployed)
    - **unavailable**: SKU is not offered in that zone

    Each SKU also includes a ``quota`` object with per-family vCPU quota:
    - **limit**: total vCPU quota for the family
    - **used**: currently consumed vCPUs
    - **remaining**: available vCPUs (limit − used)
    Values are ``null`` when the quota could not be resolved.

    When ``include_prices`` is ``True``, each SKU gains a ``pricing`` object:
    - **paygo**: pay-as-you-go price per hour (or ``null``)
    - **spot**: Spot price per hour (or ``null``)
    - **currency**: the currency code used

    Args:
        region: Azure region name (e.g. ``eastus``).
        subscription_id: Subscription ID to query.
        tenant_id: Optional tenant ID to scope the query.
        resource_type: ARM resource type to filter (default: ``virtualMachines``).
                       Other examples: ``disks``, ``snapshots``.
        name: Substring filter on SKU name (case-insensitive).
              E.g. ``"D2s"`` matches ``Standard_D2s_v3``.
        family: Substring filter on SKU family (case-insensitive).
                E.g. ``"DSv3"`` matches ``standardDSv3Family``.
        min_vcpus: Minimum number of vCPUs (inclusive).
        max_vcpus: Maximum number of vCPUs (inclusive).
        min_memory_gb: Minimum memory in GB (inclusive).
        max_memory_gb: Maximum memory in GB (inclusive).
        include_prices: Include retail pricing info (default: ``False``).
        currency_code: Currency for prices (default: ``"USD"``).
    """
    result = azure_api.get_skus(
        region,
        subscription_id,
        tenant_id,
        resource_type,
        name=name,
        family=family,
        min_vcpus=min_vcpus,
        max_vcpus=max_vcpus,
        min_memory_gb=min_memory_gb,
        max_memory_gb=max_memory_gb,
    )
    azure_api.enrich_skus_with_quotas(result, region, subscription_id, tenant_id)
    if include_prices:
        azure_api.enrich_skus_with_prices(result, region, currency_code)

    # Compute Deployment Confidence Score for each SKU
    for sku in result:
        caps = sku.get("capabilities", {})
        quota = sku.get("quota", {})
        pricing = sku.get("pricing", {})
        try:
            vcpus = int(caps.get("vCPUs", 0))
        except (TypeError, ValueError):
            vcpus = None
        remaining = quota.get("remaining")
        sku["confidence"] = compute_capacity_confidence(
            vcpus=vcpus,
            zones_supported_count=len(sku.get("zones", [])),
            restrictions_present=len(sku.get("restrictions", [])) > 0,
            quota_remaining_vcpu=remaining,
            paygo_price=pricing.get("paygo") if pricing else None,
            spot_price=pricing.get("spot") if pricing else None,
        )

    return json.dumps(result, indent=2)


@mcp.tool()
def get_spot_scores(
    region: str,
    subscription_id: str,
    vm_sizes: list[str],
    instance_count: int = 1,
    tenant_id: str | None = None,
) -> str:
    """Get Spot Placement Scores for VM sizes in a region.

    Returns a score (High / Medium / Low) for each requested VM size,
    indicating the likelihood of successful Spot VM allocation.
    This is **not** a measure of datacenter capacity.

    Args:
        region: Azure region name (e.g. ``eastus``).
        subscription_id: Subscription ID to query.
        vm_sizes: List of VM size names (e.g. ``["Standard_D2s_v3"]``).
        instance_count: Number of instances to evaluate (default: 1).
        tenant_id: Optional tenant ID to scope the query.
    """
    result = azure_api.get_spot_placement_scores(
        region,
        subscription_id,
        vm_sizes,
        instance_count,
        tenant_id,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def get_sku_pricing_detail(
    region: str,
    sku_name: str,
    currency_code: str = "USD",
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> str:
    """Get detailed Linux pricing for a single VM SKU.

    Returns per-hour prices for every pricing model: pay-as-you-go, Spot,
    Reserved Instance (1 Year / 3 Years) and Savings Plan (1 Year / 3 Years).

    All prices are **per hour, Linux only**.

    When ``subscription_id`` is provided, the response also includes a
    ``profile`` object with full VM capabilities (compute, storage, network),
    deployment info (zones, restrictions, HyperV generation) and more.

    Args:
        region: Azure region name (e.g. ``swedencentral``).
        sku_name: ARM SKU name (e.g. ``Standard_D2s_v5``).
        currency_code: ISO 4217 currency code (default: ``"USD"``).
        subscription_id: Optional subscription ID to include VM profile data.
        tenant_id: Optional tenant ID to scope the profile query.
    """
    result = azure_api.get_sku_pricing_detail(region, sku_name, currency_code)
    if subscription_id:
        profile = azure_api.get_sku_profile(region, subscription_id, sku_name, tenant_id)
        if profile is not None:
            result["profile"] = profile
    return json.dumps(result, indent=2)
