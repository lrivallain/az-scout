"""Azure Scout – FastAPI web application.

Interactive web tool to visualize how Azure maps logical availability zones
to physical zones across subscriptions in a given region.
"""

import contextlib
import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from az_scout import __version__, azure_api
from az_scout.models.capacity_strategy import WorkloadProfileRequest
from az_scout.models.deployment_plan import DeploymentIntentRequest
from az_scout.scoring.deployment_confidence import (
    best_spot_label,
    compute_deployment_confidence,
    signals_from_sku,
)
from az_scout.services.admission_confidence import compute_admission_confidence
from az_scout.services.ai_chat import is_chat_enabled
from az_scout.services.capacity_strategy_engine import recommend_capacity_strategy
from az_scout.services.deployment_planner import plan_deployment
from az_scout.services.eviction_rate import get_spot_eviction_rate
from az_scout.services.fragmentation import (
    estimate_fragmentation_risk,
    fragmentation_to_normalized,
)
from az_scout.services.volatility import compute_volatility, volatility_to_normalized

_PKG_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Lifespan – preload discovery caches on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Warm the tenant cache and start the MCP session manager."""
    t = threading.Thread(target=azure_api.preload_discovery, daemon=True)
    t.start()
    # The StreamableHTTP session manager needs a running task group;
    # sub-app lifespans are not invoked by FastAPI, so we start it here.
    # Re-create the session manager if a previous instance was already used
    # (e.g. across multiple TestClient contexts in tests).
    _ensure_fresh_session_manager()
    async with _mcp_server.session_manager.run():
        yield


app = FastAPI(
    title="az-scout API",
    version=__version__,
    description=(
        "REST API for the Azure Scout. "
        "Provides endpoints to discover Azure tenants, subscriptions, "
        "AZ-enabled regions, logical-to-physical zone mappings, and "
        "resource SKU availability with optional filtering.\n\n"
        "An **MCP server** (Streamable HTTP transport) is also available at "
        "`/mcp` for AI agent integration."
    ),
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# ---------------------------------------------------------------------------
# MCP – mount the MCP server as an ASGI sub-app under /mcp
# ---------------------------------------------------------------------------

from az_scout.mcp_server import mcp as _mcp_server  # noqa: E402

# Override the internal path so that mounting at "/mcp" gives a clean
# "/mcp" endpoint (instead of the default "/mcp/mcp").
_mcp_server.settings.streamable_http_path = "/"
_mcp_starlette = _mcp_server.streamable_http_app()
app.mount("/mcp", _mcp_starlette)


def _ensure_fresh_session_manager() -> None:
    """Re-create the StreamableHTTP session manager if already used.

    ``StreamableHTTPSessionManager.run()`` can only be called once per
    instance.  When the FastAPI lifespan is re-entered (e.g. across
    multiple ``TestClient`` contexts in tests) we need a fresh manager.
    """
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    mgr = _mcp_server.session_manager
    if mgr._has_started:  # type: ignore[attr-defined]
        new_mgr = StreamableHTTPSessionManager(
            app=_mcp_server._mcp_server,
            event_store=_mcp_server._event_store,
            json_response=_mcp_server.settings.json_response,
            stateless=_mcp_server.settings.stateless_http,
            security_settings=_mcp_server.settings.transport_security,
        )
        _mcp_server._session_manager = new_mgr
        # Also patch the ASGI handler used by the mounted Starlette app
        for route in _mcp_starlette.routes:
            if hasattr(route, "app") and hasattr(route.app, "session_manager"):
                route.app.session_manager = new_mgr


# ---------------------------------------------------------------------------
# Colored logging (reuse uvicorn's formatter)
# ---------------------------------------------------------------------------


def _setup_logging(level: int = logging.WARNING) -> None:
    """Configure the root ``az_scout`` logger with uvicorn-style colours."""
    from uvicorn.logging import DefaultFormatter

    handler = logging.StreamHandler()
    handler.setFormatter(
        DefaultFormatter(fmt="%(levelprefix)s %(name)s - %(message)s", use_colors=True)
    )
    app_logger = logging.getLogger("az_scout")
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
    # EasyAuth injects the authenticated user's display name via this header.
    auth_user = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "")
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "auth_user": auth_user,
            "chat_enabled": is_chat_enabled(),
        },
    )


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
    includePrices: bool = Query(  # noqa: N803
        False, description="Fetch retail prices from the Azure Retail Prices API."
    ),
    currencyCode: str = Query(  # noqa: N803
        "USD", description="ISO 4217 currency code for prices."
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
        skus = azure_api.get_skus(
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
        azure_api.enrich_skus_with_quotas(skus, region, subscriptionId, tenantId)
        if includePrices:
            azure_api.enrich_skus_with_prices(skus, region, currencyCode)

        # Compute Deployment Confidence Score for each SKU (canonical module)
        for sku in skus:
            sig = signals_from_sku(sku)
            sku["confidence"] = compute_deployment_confidence(sig).model_dump()

        return JSONResponse(skus)
    except Exception as exc:
        logger.exception("Failed to fetch SKUs")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# POST /api/deployment-confidence  (canonical bulk scoring)
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


@app.post(
    "/api/deployment-confidence",
    tags=["SKUs"],
    summary="Compute Deployment Confidence Scores (bulk)",
)
async def deployment_confidence(body: DeploymentConfidenceRequest) -> JSONResponse:
    """Compute the canonical Deployment Confidence Score for a set of SKUs.

    Fetches all required signals (quotas, zones, restrictions, pricing,
    spot scores) server-side and returns a deterministic score for each
    SKU.  This is the **single source of truth** – the same module is
    used by the web UI, the MCP server, and this endpoint.
    """
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
        # Fetch SKU data (zones, restrictions, capabilities, quota)
        all_skus = azure_api.get_skus(
            body.region,
            body.subscriptionId,
            body.tenantId,
            "virtualMachines",
        )
        azure_api.enrich_skus_with_quotas(all_skus, body.region, body.subscriptionId, body.tenantId)
        azure_api.enrich_skus_with_prices(all_skus, body.region, body.currencyCode)

        sku_map = {s["name"]: s for s in all_skus}

        # Optionally fetch spot placement scores
        spot_scores: dict[str, dict[str, str]] = {}
        if body.preferSpot:
            try:
                spot_result = azure_api.get_spot_placement_scores(
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

            spot_label = best_spot_label(spot_scores.get(sku_name, {}))
            sig = signals_from_sku(sku_data, spot_score_label=spot_label)
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


@app.post("/api/spot-scores", tags=["SKUs"], summary="Get Spot Placement Scores")
async def get_spot_scores(body: SpotScoresRequest) -> JSONResponse:
    """Return Spot Placement Scores for a list of VM sizes.

    Scores indicate the likelihood of successful Spot VM allocation
    (High / Medium / Low) – this is **not** a measure of datacenter
    capacity.
    """
    if not body.region or not body.subscriptionId or not body.skus:
        return JSONResponse(
            {"error": "'region', 'subscriptionId' and 'skus' are required"},
            status_code=400,
        )
    try:
        result = azure_api.get_spot_placement_scores(
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


@app.get("/api/sku-pricing", tags=["SKUs"], summary="Get detailed pricing for a SKU")
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
    """Return detailed Linux pricing for a single VM SKU.

    Includes pay-as-you-go, Spot, Reserved Instance (1Y/3Y) and
    Savings Plan (1Y/3Y) prices per hour.  When *subscriptionId* is
    provided, also returns the full VM profile (capabilities,
    restrictions, zones).
    """
    try:
        result = azure_api.get_sku_pricing_detail(region, skuName, currencyCode)
        if subscriptionId:
            profile = azure_api.get_sku_profile(region, subscriptionId, skuName, tenantId)
            if profile is not None:
                result["profile"] = profile
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("Failed to fetch SKU pricing detail")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# POST /api/deployment-plan
# ---------------------------------------------------------------------------


@app.post(
    "/api/deployment-plan",
    tags=["Deployment"],
    summary="Generate a deployment plan",
)
async def deployment_plan(body: DeploymentIntentRequest) -> JSONResponse:
    """Generate a deterministic deployment plan from a deployment intent.

    Evaluates candidate (region, SKU) combinations against zones, quotas,
    spot scores, pricing, and restrictions.  Returns a ranked recommendation
    with business and technical views.
    """
    try:
        result = plan_deployment(body)
        return JSONResponse(result.model_dump())
    except Exception as exc:
        logger.exception("Failed to generate deployment plan")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# POST /api/capacity-strategy
# ---------------------------------------------------------------------------


@app.post(
    "/api/capacity-strategy",
    tags=["Strategy"],
    summary="Compute a capacity deployment strategy",
)
async def capacity_strategy(body: WorkloadProfileRequest) -> JSONResponse:
    """Compute a deterministic Azure deployment strategy.

    Evaluates candidate regions and SKUs against capacity signals
    (zones, quotas, restrictions, spot scores, prices, confidence)
    and inter-region latency statistics to recommend a multi-region
    deployment strategy.

    A single call is sufficient for an agent to obtain a complete
    deployment recommendation.
    """
    try:
        result = recommend_capacity_strategy(body)
        return JSONResponse(result.model_dump())
    except Exception as exc:
        logger.exception("Failed to compute capacity strategy")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# GET /api/sku-admission – Admission Intelligence for a single SKU
# ---------------------------------------------------------------------------


@app.get(
    "/api/sku-admission",
    tags=["SKUs"],
    summary="Get Admission Intelligence for a SKU",
)
async def get_sku_admission(
    region: str = Query(..., description="Azure region name."),
    sku: str = Query(..., description="ARM SKU name (e.g. Standard_D2s_v3)."),
    subscriptionId: str = Query(  # noqa: N803
        ..., description="Subscription ID for quota/spot queries."
    ),
    tenantId: str | None = Query(  # noqa: N803
        None, description="Optional tenant ID."
    ),
) -> JSONResponse:
    """Return Admission Intelligence signals for a single VM SKU.

    Combines fragmentation risk, price/score volatility, spot eviction rate,
    and a composite Admission Confidence Score with detailed breakdown.

    **All metrics are heuristic estimates** derived from publicly observable
    Azure signals – they do NOT represent internal Azure capacity data.
    """
    try:
        # --- SKU profile ---
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
                region,
                subscriptionId,
                tenantId,
                "virtualMachines",
                name=sku,
            )
            for s in skus:
                if s.get("name") == sku:
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

        # --- Spot score ---
        try:
            spot_result = azure_api.get_spot_placement_scores(
                region,
                subscriptionId,
                [sku],
                1,
                tenantId,
            )
            sku_scores = spot_result.get("scores", {}).get(sku, {})
            if sku_scores:
                rank = {"High": 3, "Medium": 2, "Low": 1}
                best = max(sku_scores.values(), key=lambda s: rank.get(s, 0))
                spot_score_label = best
        except Exception:
            pass

        # --- Pricing ---
        try:
            all_prices = azure_api.get_retail_prices(region)
            sku_pricing = all_prices.get(sku)
            if sku_pricing:
                paygo_price = sku_pricing.get("paygo")
                spot_price = sku_pricing.get("spot")
        except Exception:
            pass

        # --- Individual signals ---
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

        vol24 = compute_volatility(region, sku, window="24h")
        vol7d = compute_volatility(region, sku, window="7d")

        eviction = get_spot_eviction_rate(
            region, sku, subscription_id=subscriptionId, tenant_id=tenantId
        )

        # --- Admission Confidence ---

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

        return JSONResponse(
            {
                "admissionConfidence": admission,
                "fragmentationRisk": frag,
                "volatility24h": vol24,
                "volatility7d": vol7d,
                "evictionRate": eviction,
            }
        )
    except Exception as exc:
        logger.exception("Failed to compute admission intelligence")
        return JSONResponse({"error": str(exc)}, status_code=500)


if __name__ == "__main__":
    from az_scout.cli import cli

    cli()


# ---------------------------------------------------------------------------
# POST /api/chat – AI chat with tool calling (streaming SSE)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    messages: list[ChatMessage]
    mode: str = "discussion"
    tenant_id: str | None = None
    region: str | None = None
    subscription_id: str | None = None


@app.post(
    "/api/chat",
    tags=["AI Chat"],
    summary="AI chat with Azure Scout tools",
    responses={503: {"description": "AI chat not configured"}},
)
async def chat(body: ChatRequest) -> StreamingResponse:
    """Stream AI chat completions with tool-calling support.

    Requires ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY``, and
    ``AZURE_OPENAI_DEPLOYMENT`` environment variables.
    """
    if not is_chat_enabled():
        return JSONResponse(  # type: ignore[return-value]
            {"error": "AI chat is not configured. Set AZURE_OPENAI_* environment variables."},
            status_code=503,
        )

    from az_scout.services.ai_chat import chat_stream

    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    return StreamingResponse(
        chat_stream(
            messages,
            tenant_id=body.tenant_id,
            region=body.region,
            subscription_id=body.subscription_id,
            mode=body.mode,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
