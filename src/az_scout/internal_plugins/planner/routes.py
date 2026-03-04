"""API routes for the Deployment Planner internal plugin."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel
from starlette.responses import JSONResponse

from az_scout import azure_api
from az_scout.models.deployment_plan import DeploymentIntentRequest
from az_scout.scoring.deployment_confidence import (
    best_spot_label,
    compute_deployment_confidence,
    signals_from_sku,
)
from az_scout.services.deployment_planner import plan_deployment

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/skus
# ---------------------------------------------------------------------------


@router.get("/skus", tags=["SKUs"], summary="Get SKU availability per zone")
async def get_skus(
    region: str | None = Query(None, description="Azure region name."),
    subscriptionId: str | None = Query(None, description="Subscription ID."),  # noqa: N803
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
    resourceType: str = Query(  # noqa: N803
        "virtualMachines", description="ARM resource type to filter."
    ),
    name: str | None = Query(
        None,
        description=(
            "Case-insensitive substring match on SKU name (e.g. D2s matches Standard_D2s_v3)."
        ),
    ),
    family: str | None = Query(
        None,
        description="Case-insensitive substring match on SKU family (e.g. DSv3).",
    ),
    minVcpus: int | None = Query(  # noqa: N803
        None, description="Minimum vCPU count (inclusive).", ge=1
    ),
    maxVcpus: int | None = Query(  # noqa: N803
        None, description="Maximum vCPU count (inclusive).", ge=1
    ),
    minMemoryGB: float | None = Query(  # noqa: N803
        None, description="Minimum memory in GB (inclusive).", ge=0
    ),
    maxMemoryGB: float | None = Query(  # noqa: N803
        None, description="Maximum memory in GB (inclusive).", ge=0
    ),
    includePrices: bool = Query(  # noqa: N803
        False, description="Fetch retail prices from the Azure Retail Prices API."
    ),
    currencyCode: str = Query(  # noqa: N803
        "USD", description="ISO 4217 currency code for prices."
    ),
) -> JSONResponse:
    """Return resource SKUs with zone availability, restrictions and capabilities."""
    if not region or not subscriptionId:
        return JSONResponse(
            {"error": "Both 'region' and 'subscriptionId' query parameters are required"},
            status_code=400,
        )

    try:
        skus = await asyncio.to_thread(
            azure_api.get_skus,
            region,
            subscriptionId,
            tenantId,
            resourceType,
            name=name,
            family=family,
            min_vcpus=minVcpus,
            max_vcpus=maxVcpus,
            min_memory_gb=minMemoryGB,
            max_memory_gb=maxMemoryGB,
        )
        await asyncio.to_thread(
            azure_api.enrich_skus_with_quotas, skus, region, subscriptionId, tenantId
        )
        if includePrices:
            await asyncio.to_thread(azure_api.enrich_skus_with_prices, skus, region, currencyCode)

        for sku in skus:
            sig = signals_from_sku(sku)
            sku["confidence"] = compute_deployment_confidence(sig).model_dump()

        return JSONResponse(skus)
    except Exception as exc:
        logger.exception("Failed to fetch SKUs")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# POST /api/deployment-confidence
# ---------------------------------------------------------------------------


class DeploymentConfidenceRequest(BaseModel):
    """Request body for the deployment confidence endpoint."""

    subscriptionId: str
    region: str
    currencyCode: str = "USD"
    preferSpot: bool = False
    instanceCount: int = 1
    skus: list[str]
    includeSignals: bool = True
    includeProvenance: bool = True
    tenantId: str | None = None


@router.post(
    "/deployment-confidence",
    tags=["SKUs"],
    summary="Compute Deployment Confidence Scores (bulk)",
)
async def deployment_confidence(body: DeploymentConfidenceRequest) -> JSONResponse:
    """Compute the canonical Deployment Confidence Score for a set of SKUs."""
    if not body.region or not body.subscriptionId or not body.skus:
        return JSONResponse(
            {"error": "'region', 'subscriptionId' and 'skus' are required"},
            status_code=400,
        )

    evaluated_at = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    results: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    try:
        all_skus = await asyncio.to_thread(
            azure_api.get_skus,
            body.region,
            body.subscriptionId,
            body.tenantId,
            "virtualMachines",
        )
        await asyncio.to_thread(
            azure_api.enrich_skus_with_quotas,
            all_skus,
            body.region,
            body.subscriptionId,
            body.tenantId,
        )
        await asyncio.to_thread(
            azure_api.enrich_skus_with_prices, all_skus, body.region, body.currencyCode
        )

        sku_map = {s["name"]: s for s in all_skus}

        spot_scores: dict[str, dict[str, str]] = {}
        if body.preferSpot:
            try:
                spot_result = await asyncio.to_thread(
                    azure_api.get_spot_placement_scores,
                    body.region,
                    body.subscriptionId,
                    body.skus,
                    body.instanceCount,
                    body.tenantId,
                )
                spot_scores = spot_result.get("scores", {})
            except Exception:
                logger.warning("Spot placement score fetch failed; continuing without spot")
                warnings.append("Spot placement scores unavailable")

        for sku_name in body.skus:
            sku_data = sku_map.get(sku_name)
            if sku_data is None:
                errors.append(f"SKU '{sku_name}' not found in region '{body.region}'")
                continue

            sku_spot_zones = spot_scores.get(sku_name, {})
            spot_label = best_spot_label(sku_spot_zones)
            if body.preferSpot and sku_spot_zones and spot_label is None:
                zone_values = list(sku_spot_zones.values())
                warnings.append(
                    f"Spot data for '{sku_name}' returned non-scorable values "
                    f"({', '.join(zone_values)}); excluded from confidence."
                )
            elif body.preferSpot and not sku_spot_zones and not warnings:
                warnings.append(f"No Spot Placement Score data available for '{sku_name}'.")
            sig = signals_from_sku(
                sku_data,
                spot_score_label=spot_label,
                instance_count=body.instanceCount,
            )
            result = compute_deployment_confidence(sig)

            entry: dict = {
                "sku": sku_name,
                "deploymentConfidence": result.model_dump(
                    exclude={"provenance"} if not body.includeProvenance else set()
                ),
            }
            if body.includeSignals:
                entry["rawSignals"] = sig.model_dump()

            results.append(entry)

    except Exception as exc:
        logger.exception("Failed to compute deployment confidence")
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "region": body.region,
            "subscriptionId": body.subscriptionId,
            "evaluatedAtUtc": evaluated_at,
            "results": results,
            "warnings": warnings,
            "errors": errors,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/spot-scores
# ---------------------------------------------------------------------------


class SpotScoresRequest(BaseModel):
    """Request body for the spot placement scores endpoint."""

    region: str
    subscriptionId: str
    skus: list[str]
    instanceCount: int = 1
    tenantId: str | None = None


@router.post("/spot-scores", tags=["SKUs"], summary="Get Spot Placement Scores")
async def get_spot_scores(body: SpotScoresRequest) -> JSONResponse:
    """Return Spot Placement Scores for a list of VM sizes."""
    if not body.region or not body.subscriptionId or not body.skus:
        return JSONResponse(
            {"error": "'region', 'subscriptionId' and 'skus' are required"},
            status_code=400,
        )
    try:
        result = await asyncio.to_thread(
            azure_api.get_spot_placement_scores,
            body.region,
            body.subscriptionId,
            body.skus,
            body.instanceCount,
            body.tenantId,
        )
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("Failed to fetch spot placement scores")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# GET /api/sku-pricing
# ---------------------------------------------------------------------------


@router.get("/sku-pricing", tags=["SKUs"], summary="Get detailed pricing for a SKU")
async def get_sku_pricing(
    region: str = Query(..., description="Azure region name."),
    skuName: str = Query(..., description="ARM SKU name (e.g. Standard_D2s_v3)."),  # noqa: N803
    currencyCode: str = Query(  # noqa: N803
        "USD", description="ISO 4217 currency code."
    ),
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID for VM profile data."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return detailed Linux pricing for a single VM SKU."""
    try:
        result = await asyncio.to_thread(
            azure_api.get_sku_pricing_detail, region, skuName, currencyCode
        )
        if subscriptionId:
            profile = await asyncio.to_thread(
                azure_api.get_sku_profile, region, subscriptionId, skuName, tenantId
            )
            if profile is not None:
                result["profile"] = profile
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("Failed to fetch SKU pricing detail")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# POST /api/deployment-plan
# ---------------------------------------------------------------------------


@router.post(
    "/deployment-plan",
    tags=["Deployment"],
    summary="Generate a deployment plan",
)
async def deployment_plan(body: DeploymentIntentRequest) -> JSONResponse:
    """Generate a deterministic deployment plan from a deployment intent."""
    try:
        result = await asyncio.to_thread(plan_deployment, body)
        return JSONResponse(result.model_dump())
    except Exception as exc:
        logger.exception("Failed to generate deployment plan")
        return JSONResponse({"error": str(exc)}, status_code=500)
