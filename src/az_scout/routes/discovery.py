"""Discovery API routes – tenants, subscriptions, regions, locations."""

import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from az_scout import azure_api

router = APIRouter(tags=["Discovery"])
logger = logging.getLogger(__name__)


@router.get("/tenants", summary="List Azure AD tenants")
async def list_tenants() -> JSONResponse:
    """Return Azure AD tenants accessible by the current credential.

    Returns all tenants with their authentication status and the default
    tenant ID for the current auth context.
    """
    try:
        return JSONResponse(await asyncio.to_thread(azure_api.list_tenants))
    except Exception as exc:
        logger.exception("Failed to list tenants")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/subscriptions", summary="List enabled Azure subscriptions")
async def list_subscriptions(
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID to scope the query."
    ),
) -> JSONResponse:
    """Return all enabled Azure subscriptions, sorted alphabetically."""
    try:
        return JSONResponse(await asyncio.to_thread(azure_api.list_subscriptions, tenantId))
    except Exception as exc:
        logger.exception("Failed to list subscriptions")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/regions", summary="List AZ-enabled regions")
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
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to list regions")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/locations", summary="List all ARM locations")
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
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Failed to list locations")
        return JSONResponse({"error": str(exc)}, status_code=502)
