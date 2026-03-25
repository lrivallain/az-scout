"""Discovery API routes – tenants, subscriptions, regions, locations."""

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from az_scout import azure_api
from az_scout.auth import get_user_token
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
async def list_tenants(request: Request) -> JSONResponse:
    """Return Azure AD tenants accessible by the current user or credential."""
    token = get_user_token(request)
    return JSONResponse(await asyncio.to_thread(azure_api.list_tenants, user_token=token))


@router.get(
    "/subscriptions",
    summary="List enabled Azure subscriptions",
    response_model=list[SubscriptionInfo],
    responses={500: {"model": ErrorResponse}},
)
async def list_subscriptions(
    request: Request,
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID to scope the query."
    ),
) -> JSONResponse:
    """Return all enabled Azure subscriptions, sorted alphabetically."""
    token = get_user_token(request)
    return JSONResponse(
        await asyncio.to_thread(azure_api.list_subscriptions, tenantId, user_token=token)
    )


@router.get(
    "/regions",
    summary="List AZ-enabled regions",
    description=(
        "Return regions that support Availability Zones. "
        "Use this for zone topology and deployment planning. "
        "For all physical regions (including those without AZ support), "
        "use ``/api/locations`` instead."
    ),
    response_model=list[RegionInfo],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def list_regions(
    request: Request,
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return Azure regions that support Availability Zones."""
    token = get_user_token(request)
    try:
        return JSONResponse(
            await asyncio.to_thread(
                azure_api.list_regions,
                subscriptionId,
                tenantId,
                user_token=token,
            )
        )
    except LookupError as exc:
        logger.warning(
            "Failed to list AZ-enabled regions for subscriptionId=%s tenantId=%s",
            subscriptionId,
            tenantId,
            exc_info=exc,
        )
        return JSONResponse(
            {
                "error": (
                    "No enabled subscriptions found. "
                    "Ensure you have at least Reader access on a subscription in this tenant."
                )
            },
            status_code=404,
        )


@router.get(
    "/locations",
    summary="List all physical Azure regions",
    description=(
        "Return all physical Azure regions, including those without "
        "Availability Zone support. Use this for pricing comparison, "
        "latency analysis, or plugin features that operate on any region. "
        "For AZ-enabled regions only, use ``/api/regions`` instead."
    ),
    response_model=list[RegionInfo],
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def list_locations(
    request: Request,
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return all Azure ARM locations, including those without Availability Zones."""
    token = get_user_token(request)
    try:
        return JSONResponse(
            await asyncio.to_thread(
                azure_api.list_locations,
                subscriptionId,
                tenantId,
                user_token=token,
            )
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
