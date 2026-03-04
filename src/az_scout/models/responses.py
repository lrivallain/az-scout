"""Pydantic response models for API endpoints.

These models document the JSON shapes returned by the API and enable
auto-generated OpenAPI schemas in Swagger UI (``/docs``).  They are
**backward-compatible** with the existing ``JSONResponse`` payloads —
same field names, same nesting.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# GET /api/tenants
# ---------------------------------------------------------------------------


class TenantInfo(BaseModel):
    """A single Azure AD tenant with authentication status."""

    id: str
    name: str
    authenticated: bool


class TenantListResponse(BaseModel):
    """Response for ``GET /api/tenants``."""

    tenants: list[TenantInfo]
    defaultTenantId: str | None


# ---------------------------------------------------------------------------
# GET /api/subscriptions
# ---------------------------------------------------------------------------


class SubscriptionInfo(BaseModel):
    """A single enabled Azure subscription."""

    id: str
    name: str


# ---------------------------------------------------------------------------
# GET /api/regions  &  GET /api/locations
# ---------------------------------------------------------------------------


class RegionInfo(BaseModel):
    """An Azure region / location entry."""

    name: str
    displayName: str


# ---------------------------------------------------------------------------
# GET /api/mappings
# ---------------------------------------------------------------------------


class ZoneMapping(BaseModel):
    """A single logical→physical zone mapping."""

    logicalZone: str
    physicalZone: str


class SubscriptionMappingResult(BaseModel):
    """Zone mapping result for one subscription."""

    subscriptionId: str
    region: str
    mappings: list[ZoneMapping]
    error: str | None = None


# ---------------------------------------------------------------------------
# GET /api/skus
# ---------------------------------------------------------------------------


class SkuQuota(BaseModel):
    """Quota enrichment for a SKU."""

    limit: int | None = None
    used: int | None = None
    remaining: int | None = None


class SkuConfidence(BaseModel):
    """Deployment confidence score embedded in a SKU."""

    score: int | None = None
    label: str | None = None


class SkuInfo(BaseModel):
    """A resource SKU with zone availability, restrictions and capabilities."""

    name: str | None = None
    tier: str | None = None
    size: str | None = None
    family: str | None = None
    zones: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    capabilities: dict[str, str] = Field(default_factory=dict)
    quota: SkuQuota | None = None
    confidence: SkuConfidence | None = None
    prices: dict[str, Any] | None = Field(
        default=None,
        description="Retail pricing data (paygo/spot per-hour costs) when includePrices=true.",
    )


# ---------------------------------------------------------------------------
# POST /api/deployment-confidence
# ---------------------------------------------------------------------------


class DeploymentConfidenceResultEntry(BaseModel):
    """One SKU's deployment confidence evaluation."""

    sku: str
    deploymentConfidence: dict[str, Any] = Field(
        description=(
            "Confidence result with keys: score (int 0–100), label, "
            "signalContributions, and optionally provenance."
        ),
    )
    rawSignals: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw signal values (quotaPressure, zones, restrictionDensity, "
            "pricePressure, spot) when includeSignals=true."
        ),
    )


class DeploymentConfidenceResponse(BaseModel):
    """Response for ``POST /api/deployment-confidence``."""

    region: str
    subscriptionId: str
    evaluatedAtUtc: str
    results: list[DeploymentConfidenceResultEntry]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# POST /api/spot-scores
# ---------------------------------------------------------------------------


class SpotScoresResponse(BaseModel):
    """Response for ``POST /api/spot-scores``."""

    scores: dict[str, dict[str, str]] = Field(
        description="Mapping of SKU name → {zone → score label (High/Medium/Low/Unknown)}.",
    )
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Error response (shared)
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Generic error envelope used by all endpoints on failure."""

    error: str
