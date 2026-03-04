"""MCP tools for the Deployment Planner internal plugin."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from pydantic import Field

from az_scout import azure_api
from az_scout.scoring.deployment_confidence import (
    best_spot_label,
    compute_deployment_confidence,
    enrich_skus_with_confidence,
    signals_from_sku,
)

logger = logging.getLogger(__name__)


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

    enrich_skus_with_confidence(result)

    return json.dumps(result, indent=2)


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


def get_sku_deployment_confidence(
    region: Annotated[str, Field(description="Azure region name (e.g. eastus).")],
    subscription_id: Annotated[str, Field(description="Subscription ID to query.")],
    skus: Annotated[list[str], Field(description="List of VM SKU names to score.")],
    prefer_spot: Annotated[
        bool,
        Field(
            description=(
                "Include Spot Placement Scores in the confidence calculation. "
                "When true, the tool fetches spot scores and produces a "
                "'basic+spot' scoreType; otherwise 'basic'."
            )
        ),
    ] = False,
    instance_count: Annotated[
        int, Field(description="Instance count for spot evaluation (default: 1).")
    ] = 1,
    currency_code: Annotated[
        str, Field(description="Currency code for pricing signals (default: USD).")
    ] = "USD",
    include_signals: Annotated[
        bool,
        Field(description="Include raw signal values used to compute the score (default: true)."),
    ] = True,
    include_provenance: Annotated[
        bool,
        Field(description="Include provenance metadata for each signal (default: true)."),
    ] = True,
    tenant_id: Annotated[str | None, Field(description="Optional tenant ID.")] = None,
) -> str:
    """Compute Deployment Confidence Scores for one or more VM SKUs.

    Fetches all required signals (quotas, zones, restrictions, pricing,
    optionally spot placement scores) and returns a deterministic
    confidence score (0\u2013100) with label for each SKU.

    This is the **canonical scoring endpoint** \u2013 the same module powers
    the web UI, MCP server, and REST API.

    Set ``prefer_spot`` to ``True`` to include Spot Placement Scores
    in the calculation (produces ``scoreType: 'basic+spot'``).
    Without it, only basic signals are used (``scoreType: 'basic'``).

    Use ``include_signals`` and ``include_provenance`` to control
    response verbosity for conversational contexts.
    """
    all_skus = azure_api.get_skus(region, subscription_id, tenant_id, "virtualMachines")
    azure_api.enrich_skus_with_quotas(all_skus, region, subscription_id, tenant_id)
    azure_api.enrich_skus_with_prices(all_skus, region, currency_code)
    sku_map = {s["name"]: s for s in all_skus}

    spot_scores: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    if prefer_spot:
        try:
            spot_result = azure_api.get_spot_placement_scores(
                region, subscription_id, skus, instance_count, tenant_id
            )
            spot_scores = spot_result.get("scores", {})
        except Exception:
            logger.warning("Spot placement score fetch failed; continuing without spot")
            warnings.append("Spot placement scores unavailable")

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for sku_name in skus:
        sku_data = sku_map.get(sku_name)
        if sku_data is None:
            errors.append(f"SKU '{sku_name}' not found in region '{region}'")
            continue

        sku_spot_zones = spot_scores.get(sku_name, {})
        spot_label = best_spot_label(sku_spot_zones)
        if prefer_spot and sku_spot_zones and spot_label is None:
            warnings.append(
                f"Spot data for '{sku_name}' returned non-scorable values; "
                "excluded from confidence."
            )
        elif prefer_spot and not sku_spot_zones and not warnings:
            warnings.append(f"No Spot Placement Score data available for '{sku_name}'.")

        sig = signals_from_sku(
            sku_data,
            spot_score_label=spot_label,
            instance_count=instance_count,
        )
        result = compute_deployment_confidence(sig)

        exclude: set[str] = set()
        if not include_provenance:
            exclude.add("provenance")

        entry: dict[str, Any] = {
            "sku": sku_name,
            "deploymentConfidence": result.model_dump(exclude=exclude),
        }
        if include_signals:
            entry["rawSignals"] = sig.model_dump()
        results.append(entry)

    return json.dumps(
        {
            "region": region,
            "subscriptionId": subscription_id,
            "results": results,
            "warnings": warnings,
            "errors": errors,
        },
        indent=2,
    )


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
    actual_name = result.get("skuName", sku_name)
    if subscription_id:
        profile = azure_api.get_sku_profile(region, subscription_id, actual_name, tenant_id)
        if profile is not None:
            result["profile"] = profile
    return json.dumps(result, indent=2)
