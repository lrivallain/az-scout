"""MCP server for Azure Availability Zone Mapping.

Exposes the same Azure ARM capabilities as the web UI – tenants,
subscriptions, regions, zone mappings and SKU availability – as
MCP tools so that AI agents can query them directly.

Run with:
    az-scout mcp            # stdio transport (default)
    az-scout mcp --http     # Streamable HTTP transport on port 8080

Or add to your MCP client config (e.g. Claude Desktop):
    {
      "mcpServers": {
        "az-scout": {
          "command": "az-scout",
          "args": ["mcp"]
        }
      }
    }
"""

import json
import logging
import os
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from az_scout import azure_api
from az_scout.scoring.admission_confidence import compute_admission_confidence
from az_scout.scoring.deployment_confidence import (
    compute_deployment_confidence,
    signals_from_sku,
)
from az_scout.scoring.fragmentation import (
    estimate_fragmentation_risk,
    fragmentation_to_normalized,
)
from az_scout.services.eviction_rate import get_spot_eviction_rate
from az_scout.services.region_latency import get_rtt_ms
from az_scout.services.volatility import compute_volatility, volatility_to_normalized

logger = logging.getLogger(__name__)

# When deployed behind a reverse proxy (e.g. Azure Container Apps), DNS
# rebinding protection must either be disabled or the external hostname(s)
# added to the allow-list.  Set FASTMCP_ALLOWED_HOSTS to a comma-separated
# list of allowed Host header values (e.g. "myapp.azurecontainerapps.io").
# If empty/unset, rebinding protection is disabled for remote deployments.
_allowed_hosts_env = os.environ.get("FASTMCP_ALLOWED_HOSTS", "")
if _allowed_hosts_env:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in _allowed_hosts_env.split(",")],
    )
else:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

mcp = FastMCP(
    "az-scout",
    instructions=(
        "Azure Availability Zone mapping tools. "
        "Use these tools to discover Azure tenants, subscriptions and regions, "
        "then query logical-to-physical zone mappings and VM SKU availability. "
        "All tools require valid Azure credentials via DefaultAzureCredential "
        "(e.g. `az login`)."
    ),
    transport_security=_transport_security,
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
def list_subscriptions(
    tenant_id: Annotated[
        str | None, Field(description="Optional tenant ID to scope the query.")
    ] = None,
) -> str:
    """List enabled Azure subscriptions.

    Returns a JSON array of ``{"id": ..., "name": ...}`` objects sorted
    alphabetically.
    """
    result = azure_api.list_subscriptions(tenant_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_regions(
    subscription_id: Annotated[
        str | None, Field(description="Subscription ID. Auto-discovered if omitted.")
    ] = None,
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
) -> str:
    """List Azure regions that support Availability Zones.

    Returns a JSON array of ``{"name": ..., "displayName": ...}`` for each
    AZ-enabled region.
    """
    result = azure_api.list_regions(subscription_id, tenant_id)
    return json.dumps(result, indent=2)


@mcp.tool()
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


@mcp.tool()
def get_sku_availability(
    region: Annotated[str, Field(description="Azure region name (e.g. eastus).")],
    subscription_id: Annotated[str, Field(description="Subscription ID to query.")],
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
    resource_type: Annotated[
        str, Field(description="ARM resource type (default: virtualMachines).")
    ] = "virtualMachines",
    name: Annotated[
        str | None,
        Field(
            description=(
                "Fuzzy filter on SKU name (case-insensitive). "
                "Supports multi-part matching: 'FX48-v2' matches "
                "Standard_FX48mds_v2. Use the shortest distinctive "
                "prefix when unsure of the exact ARM name."
            )
        ),
    ] = None,
    family: Annotated[
        str | None, Field(description="Substring filter on SKU family (case-insensitive).")
    ] = None,
    min_vcpus: Annotated[int | None, Field(description="Minimum vCPU count (inclusive).")] = None,
    max_vcpus: Annotated[int | None, Field(description="Maximum vCPU count (inclusive).")] = None,
    min_memory_gb: Annotated[
        float | None, Field(description="Minimum memory in GB (inclusive).")
    ] = None,
    max_memory_gb: Annotated[
        float | None, Field(description="Maximum memory in GB (inclusive).")
    ] = None,
    include_prices: Annotated[
        bool,
        Field(
            description=(
                "Include retail pricing (PAYGO, Spot). "
                "Defaults to false — set to true whenever pricing information is needed."
            )
        ),
    ] = False,
    currency_code: Annotated[
        str, Field(description="Currency code for prices (default: USD).")
    ] = "USD",
) -> str:
    """Get VM SKU availability per zone for a region and subscription.

    Returns resource SKUs (VM sizes by default) with their zone availability,
    zone restrictions, key capabilities (vCPUs, memory), quotas, and a
    deployment confidence score (0–100).

    Set ``include_prices`` to ``True`` to also get retail pricing (PAYGO,
    Spot) — **without this flag, NO pricing data is returned**.

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

    # Compute Deployment Confidence Score for each SKU (canonical module)
    for sku in result:
        sig = signals_from_sku(sku)
        sku["confidence"] = compute_deployment_confidence(sig).model_dump()

    return json.dumps(result, indent=2)


@mcp.tool()
def get_spot_scores(
    region: Annotated[str, Field(description="Azure region name (e.g. eastus).")],
    subscription_id: Annotated[str, Field(description="Subscription ID to query.")],
    vm_sizes: Annotated[list[str], Field(description="List of VM size names.")],
    instance_count: Annotated[
        int, Field(description="Number of instances to evaluate (default: 1).")
    ] = 1,
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
) -> str:
    """Get Spot Placement Scores for VM sizes in a region.

    Returns a score (High / Medium / Low) for each requested VM size,
    indicating the likelihood of successful Spot VM allocation.
    This is **not** a measure of datacenter capacity.

    Works for **any** VM SKU — always call this tool when Spot is
    discussed; never assume a SKU lacks Spot scores without checking.
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
    region: Annotated[str, Field(description="Azure region name (e.g. swedencentral).")],
    sku_name: Annotated[
        str,
        Field(
            description=(
                "Exact ARM SKU name (e.g. Standard_M128s_v2). "
                "Call get_sku_availability first to discover the correct name — "
                "user-friendly names like 'M128' will NOT work."
            )
        ),
    ],
    currency_code: Annotated[str, Field(description="Currency code (default: USD).")] = "USD",
    subscription_id: Annotated[
        str | None,
        Field(
            description=(
                "Subscription ID — provide this to unlock the full VM profile "
                "(capabilities, zones, restrictions). Without it, only pricing is returned."
            )
        ),
    ] = None,
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
) -> str:
    """Get detailed Linux pricing AND full technical profile for a single VM SKU.

    Returns per-hour prices for every pricing model: pay-as-you-go, Spot,
    Reserved Instance (1 Year / 3 Years) and Savings Plan (1 Year / 3 Years).

    All prices are **per hour, Linux only**.

    When ``subscription_id`` is provided, the response also includes a
    ``profile`` object with full VM capabilities (compute, storage, network),
    deployment info (zones, restrictions, HyperV generation) and more.

    **Important:** ``sku_name`` must be the exact ARM SKU name
    (e.g. ``Standard_M128s_v2``, **not** ``M128``). Call
    ``get_sku_availability`` first to discover correct ARM names.
    """
    result = azure_api.get_sku_pricing_detail(region, sku_name, currency_code)
    # Use the actual matched ARM name for profile lookup (fuzzy match may differ)
    actual_name = result.get("skuName", sku_name)
    if subscription_id:
        profile = azure_api.get_sku_profile(region, subscription_id, actual_name, tenant_id)
        if profile is not None:
            result["profile"] = profile
    return json.dumps(result, indent=2)


@mcp.tool()
def region_latency(
    source_region: Annotated[
        str, Field(description="Source Azure region name (e.g. francecentral).")
    ],
    target_region: Annotated[str, Field(description="Target Azure region name (e.g. westeurope).")],
) -> str:
    """Return indicative RTT latency between two Azure regions.

    Uses Microsoft published statistics from:
    https://learn.microsoft.com/en-us/azure/networking/azure-network-latency
    """
    rtt = get_rtt_ms(source_region, target_region)
    result = {
        "sourceRegion": source_region,
        "targetRegion": target_region,
        "rttMs": rtt,
        "source": "https://learn.microsoft.com/en-us/azure/networking/azure-network-latency",
        "disclaimer": (
            "Latency values are indicative and must be validated with in-tenant measurements."
        ),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def sku_admission_intelligence(
    region: Annotated[str, Field(description="Azure region name (e.g. francecentral).")],
    sku_name: Annotated[str, Field(description="ARM SKU name (e.g. Standard_D2s_v3).")],
    subscription_id: Annotated[str, Field(description="Subscription ID for quota/spot queries.")],
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
) -> str:
    """Return Admission Intelligence signals for a single VM SKU.

    Combines fragmentation risk, price/score volatility, spot eviction rate,
    and a composite Admission Confidence Score with detailed breakdown.

    All metrics are heuristic estimates derived from publicly observable
    Azure signals – they do NOT represent internal Azure capacity data.
    """
    import contextlib

    spot_score_label: str | None = None
    zones_count: int | None = None
    restrictions_present: bool | None = None
    vcpus: int | None = None
    memory_gb: float | None = None
    gpu_count = 0
    paygo_price: float | None = None
    spot_price: float | None = None
    quota_remaining: int | None = None
    require_zonal = False

    try:
        skus = azure_api.get_skus(
            region, subscription_id, tenant_id, "virtualMachines", name=sku_name
        )
        for s in skus:
            if s.get("name") == sku_name:
                zones_count = len(s.get("zones", []))
                restrictions_present = len(s.get("restrictions", [])) > 0
                caps = s.get("capabilities", {})
                with contextlib.suppress(TypeError, ValueError):
                    vcpus = int(caps.get("vCPUs", 0))
                with contextlib.suppress(TypeError, ValueError):
                    memory_gb = float(caps.get("MemoryGB", 0))
                try:
                    gpu_count = int(caps.get("GPUs", 0))
                except (TypeError, ValueError):
                    gpu_count = 0
                require_zonal = zones_count is not None and zones_count > 0
                q = s.get("quota", {})
                quota_remaining = q.get("remaining")
                break
    except Exception:
        pass

    try:
        spot_result = azure_api.get_spot_placement_scores(
            region, subscription_id, [sku_name], 1, tenant_id
        )
        sku_scores = spot_result.get("scores", {}).get(sku_name, {})
        if sku_scores:
            rank = {"High": 3, "Medium": 2, "Low": 1}
            best = max(sku_scores.values(), key=lambda s: rank.get(s, 0))
            spot_score_label = best
    except Exception:
        pass

    try:
        all_prices = azure_api.get_retail_prices(region)
        sku_pricing = all_prices.get(sku_name)
        if sku_pricing:
            paygo_price = sku_pricing.get("paygo")
            spot_price = sku_pricing.get("spot")
    except Exception:
        pass

    price_ratio: float | None = None
    if paygo_price and spot_price and paygo_price > 0:
        price_ratio = spot_price / paygo_price

    frag = estimate_fragmentation_risk(
        vcpu=vcpus,
        memory_gb=memory_gb,
        gpu_count=gpu_count,
        require_zonal=require_zonal,
        spot_score=spot_score_label,
        price_ratio=price_ratio,
    )
    vol24 = compute_volatility(region, sku_name, window="24h")
    vol7d = compute_volatility(region, sku_name, window="7d")
    eviction = get_spot_eviction_rate(
        region, sku_name, subscription_id=subscription_id, tenant_id=tenant_id
    )

    admission = compute_admission_confidence(
        spot_score_label=spot_score_label,
        eviction_rate_normalized=eviction.get("normalizedScore"),
        volatility_normalized=volatility_to_normalized(vol24.get("label", "")),
        fragmentation_normalized=fragmentation_to_normalized(frag.get("label", "")),
        quota_remaining_vcpu=quota_remaining,
        vcpus=vcpus,
        zones_supported_count=zones_count,
        restrictions_present=restrictions_present,
    )
    return json.dumps(
        {
            "admissionConfidence": admission,
            "fragmentationRisk": frag,
            "volatility24h": vol24,
            "volatility7d": vol7d,
            "evictionRate": eviction,
        },
        indent=2,
    )
