"""Azure Availability Zone Mapping Viewer – FastAPI web application.

Interactive web tool to visualize how Azure maps logical availability zones
to physical zones across subscriptions in a given region.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from az_mapping import azure_api

_PKG_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="az-mapping API",
    description=(
        "REST API for the Azure Availability Zone Mapping Viewer. "
        "Provides endpoints to discover Azure tenants, subscriptions, "
        "AZ-enabled regions, logical-to-physical zone mappings, and "
        "resource SKU availability with optional filtering."
    ),
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# ---------------------------------------------------------------------------
# Colored logging (reuse uvicorn's formatter)
# ---------------------------------------------------------------------------


def _setup_logging(level: int = logging.WARNING) -> None:
    """Configure the root ``az_mapping`` logger with uvicorn-style colours."""
    from uvicorn.logging import DefaultFormatter

    handler = logging.StreamHandler()
    handler.setFormatter(
        DefaultFormatter(fmt="%(levelprefix)s %(name)s - %(message)s", use_colors=True)
    )
    app_logger = logging.getLogger("az_mapping")
    app_logger.handlers = [handler]
    app_logger.setLevel(level)
    app_logger.propagate = False

    # Silence noisy third-party loggers
    logging.getLogger("azure").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    """Serve the main page."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/tenants", tags=["Discovery"], summary="List Azure AD tenants")
async def list_tenants() -> JSONResponse:
    """Return Azure AD tenants accessible by the current credential.

    Returns all tenants with their authentication status and the default
    tenant ID for the current auth context.
    """
    try:
        return JSONResponse(azure_api.list_tenants())
    except Exception as exc:
        logger.exception("Failed to list tenants")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/subscriptions", tags=["Discovery"], summary="List enabled Azure subscriptions")
async def list_subscriptions(
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID to scope the query."
    ),
) -> JSONResponse:
    """Return all enabled Azure subscriptions, sorted alphabetically."""
    try:
        return JSONResponse(azure_api.list_subscriptions(tenantId))
    except Exception as exc:
        logger.exception("Failed to list subscriptions")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/regions", tags=["Discovery"], summary="List AZ-enabled regions")
async def list_regions(
    subscriptionId: str | None = Query(  # noqa: N803
        None, description="Subscription ID. Auto-discovered if omitted."
    ),
    tenantId: str | None = Query(None, description="Optional tenant ID."),  # noqa: N803
) -> JSONResponse:
    """Return Azure regions that support Availability Zones."""
    try:
        return JSONResponse(azure_api.list_regions(subscriptionId, tenantId))
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to list regions")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/mappings", tags=["Mappings"], summary="Get zone mappings")
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
    return JSONResponse(azure_api.get_mappings(region, sub_ids, tenantId))


@app.get("/api/skus", tags=["SKUs"], summary="Get SKU availability per zone")
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
) -> JSONResponse:
    """Return resource SKUs with zone availability, restrictions and capabilities.

    Use optional filter parameters to reduce the response size.
    When no filters are provided, all SKUs for the resource type are returned.
    """
    if not region or not subscriptionId:
        return JSONResponse(
            {"error": "Both 'region' and 'subscriptionId' query parameters are required"},
            status_code=400,
        )

    try:
        return JSONResponse(
            azure_api.get_skus(
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
        )
    except Exception as exc:
        logger.exception("Failed to fetch SKUs")
        return JSONResponse({"error": str(exc)}, status_code=500)


if __name__ == "__main__":
    from az_mapping.cli import cli

    cli()
