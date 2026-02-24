"""FastAPI router for the public-signals plugin.

All endpoints are mounted under ``/plugins/public/api`` and require no
authentication.
"""

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from az_scout.plugins.public.services import (
    get_latency_matrix,
    get_public_pricing,
    public_capacity_strategy,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Public Signals"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CapacityStrategyRequest(BaseModel):
    """Request body for the public capacity-strategy endpoint."""

    skuName: str
    instanceCount: int = 1
    regions: list[str] | None = None
    currency: str = "USD"
    constraints: "StrategyConstraints | None" = None


class StrategyConstraints(BaseModel):
    preferSpot: bool = False
    requireZones: bool = False
    maxRegions: int = Field(default=3, ge=1, le=10)
    latencySensitive: bool = False
    targetCountries: list[str] | None = None


class LatencyMatrixRequest(BaseModel):
    """Request body for the latency-matrix endpoint."""

    regions: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/pricing", summary="Public VM retail pricing")
async def pricing(
    skuName: str | None = Query(None, description="SKU name filter (substring match)"),  # noqa: N803
    region: str | None = Query(None, description="Azure region name"),
    currency: str = Query("USD", description="Currency code (USD or EUR)"),
) -> JSONResponse:
    """Return retail VM pricing from the Azure Retail Prices API.

    No authentication required.  Results are indicative and not a guarantee.
    """
    result = get_public_pricing(sku_name=skuName, region=region, currency=currency)
    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)


@router.post("/latency-matrix", summary="Inter-region latency matrix")
async def latency_matrix(body: LatencyMatrixRequest) -> JSONResponse:
    """Return RTT matrix between the requested regions.

    Uses Microsoft published latency statistics (no authentication).
    """
    return JSONResponse(get_latency_matrix(body.regions))


@router.post("/capacity-strategy", summary="Public capacity strategy")
async def capacity_strategy(body: CapacityStrategyRequest) -> JSONResponse:
    """Deterministic capacity recommendation using only public signals.

    Available signals: retail pricing, inter-region latency.
    Missing signals: subscription, quota, policy, spot placement scores.
    """
    constraints = body.constraints or StrategyConstraints()
    result = public_capacity_strategy(
        sku_name=body.skuName,
        instance_count=body.instanceCount,
        regions=body.regions,
        currency=body.currency,
        prefer_spot=constraints.preferSpot,
        require_zones=constraints.requireZones,
        max_regions=constraints.maxRegions,
        latency_sensitive=constraints.latencySensitive,
        target_countries=constraints.targetCountries,
    )
    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)
