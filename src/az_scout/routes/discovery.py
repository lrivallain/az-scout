"""Discovery API routes – tenants, subscriptions, regions, locations."""

import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from az_scout import azure_api
from az_scout.models.responses import (
    ErrorResponse,
    RegionInfo,
    SubscriptionInfo,
    TenantListResponse,
)

router = APIRouter(tags=["Discovery"])
logger = logging.getLogger(__name__)


@router.get(
    "/tenants",
    summary="List Azure AD tenants",
    response_model=TenantListResponse,
    responses={500: {"model": ErrorResponse}},
)
async def list_tenants() -> JSONResponse:
    """Return Azure AD tenants accessible by the current credential.

    Returns all tenants with their authentication status and the default
    tenant ID for the current auth context.
    """
    return JSONResponse(await asyncio.to_thread(azure_api.list_tenants))


@router.get(
    "/subscriptions",
    summary="List enabled Azure subscriptions",
    response_model=list[SubscriptionInfo],
    responses={500: {"model": ErrorResponse}},
)
async def list_subscriptions(
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID to scope the query."
    ),
) -> JSONResponse:
    """Return all enabled Azure subscriptions, sorted alphabetically."""
    return JSONResponse(await asyncio.to_thread(azure_api.list_subscriptions, tenantId))


@router.get(
    "/regions",
    summary="List AZ-enabled regions",
    response_model=list[RegionInfo],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def list_regions(
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return Azure regions that support Availability Zones."""
    try:
        return JSONResponse(
            await asyncio.to_thread(azure_api.list_regions, subscriptionId, tenantId)
        )
    except LookupError as exc:
        logger.warning(
            "Failed to list AZ-enabled regions for subscriptionId=%s tenantId=%s",
            subscriptionId,
            tenantId,
            exc_info=exc,
        )
        return JSONResponse(
            {"error": "No enabled subscription available for region discovery."},
            status_code=404,
        )


@router.get(
    "/locations",
    summary="List all ARM locations",
    response_model=list[RegionInfo],
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def list_locations(
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return all Azure ARM locations, including those without Availability Zones."""
    try:
        return JSONResponse(
            await asyncio.to_thread(azure_api.list_locations, subscriptionId, tenantId)
        )
    except LookupError as exc:
        logger.warning(
            "Failed to list ARM locations for subscriptionId=%s tenantId=%s",
            subscriptionId,
            tenantId,
            exc_info=exc,
        )
        return JSONResponse(
            {"error": "No enabled subscription available for location discovery."},
            status_code=400,
        )
