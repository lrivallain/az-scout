"""Core SKU detail route – shared by all plugins.

Provides ``GET /api/sku-detail`` which combines the VM profile, pricing,
quota, and deployment confidence for a single SKU into one response.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from starlette.responses import JSONResponse

from az_scout import azure_api
from az_scout.auth import require_auth
from az_scout.scoring.deployment_confidence import (
    compute_deployment_confidence,
    signals_from_sku,
)

router = APIRouter(tags=["Plugin: planner"], dependencies=[Depends(require_auth)])


@router.get(
    "/sku-detail",
    tags=["SKU"],
    summary="Get full detail for a single SKU",
)
async def get_sku_detail(
    region: str = Query(..., description="Azure region name."),
    sku: str = Query(..., alias="sku", description="ARM SKU name (e.g. Standard_D2s_v3)."),
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID for profile and quota data."
    ),
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID."
    ),
    currencyCode: str = Query(  # noqa: N803
        "USD", description="ISO 4217 currency code."
    ),
    instanceCount: int = Query(  # noqa: N803
        1, description="Instance count for confidence scoring.", ge=1
    ),
) -> JSONResponse:
    """Return VM profile, pricing, quota, and confidence for a single SKU.

    Combines ``get_sku_profile()``, ``get_sku_pricing_detail()``, and
    ``compute_deployment_confidence()`` into a single response.
    This is the canonical endpoint for SKU detail modals across all plugins.
    """
    # Fetch pricing (unauthenticated, always available)
    result = await asyncio.to_thread(azure_api.get_sku_pricing_detail, region, sku, currencyCode)

    # Fetch profile + quota if subscription is provided
    if subscriptionId:
        profile = await asyncio.to_thread(
            azure_api.get_sku_profile, region, subscriptionId, sku, tenantId
        )
        if profile is not None:
            result["profile"] = profile

            # Compute confidence from profile data
            sig = signals_from_sku(profile, instance_count=instanceCount)
            confidence = compute_deployment_confidence(sig)
            result["confidence"] = confidence.model_dump()

    return JSONResponse(result)
