"""API routes for the AZ Topology internal plugin."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from starlette.responses import JSONResponse

from az_scout import azure_api
from az_scout.models.responses import ErrorResponse, SubscriptionMappingResult

router = APIRouter(tags=["Mappings"])


@router.get(
    "/mappings",
    summary="Get zone mappings",
    response_model=list[SubscriptionMappingResult],
    responses={400: {"model": ErrorResponse}},
)
async def get_mappings(
    region: str | None = Query(None, description="Azure region name (e.g. eastus)."),
    subscriptions: str | None = Query(
        None, description="Comma-separated list of subscription IDs."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return logical-to-physical Availability Zone mappings per subscription."""
    if not region or not subscriptions:
        return JSONResponse(
            {"error": "Both 'region' and 'subscriptions' query parameters are required"},
            status_code=400,
        )
    sub_ids = [s.strip() for s in subscriptions.split(",") if s.strip()]
    if not sub_ids:
        return JSONResponse(
            {"error": "Both 'region' and 'subscriptions' query parameters are required"},
            status_code=400,
        )
    return JSONResponse(await asyncio.to_thread(azure_api.get_mappings, region, sub_ids, tenantId))
