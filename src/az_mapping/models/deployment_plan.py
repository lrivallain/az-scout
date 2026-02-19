"""Pydantic models for the deployment plan feature.

Request models describe a deployment intent from a Sales / Solution Engineer.
Response models provide a structured, agent-consumable deployment plan with
both business-friendly and technical views.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RegionConstraints(BaseModel):
    allowRegions: list[str] | None = None
    denyRegions: list[str] | None = None
    dataResidency: Literal["EU", "FR", "ANY"] | None = None


class SkuConstraints(BaseModel):
    preferredSkus: list[str] | None = None
    requireGpu: bool | None = None
    requireZonal: bool | None = None


class ScaleRequirement(BaseModel):
    instanceCount: int


class PricingPreference(BaseModel):
    currencyCode: Literal["USD", "EUR"] = "USD"
    preferSpot: bool = False
    maxHourlyBudget: float | None = None


class TimingPreference(BaseModel):
    urgency: Literal["now", "today", "this_week"] = "this_week"


class DeploymentIntentRequest(BaseModel):
    subscriptionId: str
    tenantId: str | None = None
    regionConstraints: RegionConstraints | None = None
    skuConstraints: SkuConstraints | None = None
    scale: ScaleRequirement
    pricing: PricingPreference | None = None
    timing: TimingPreference | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Derived requirements (intermediate, computed from intent)
# ---------------------------------------------------------------------------


class DerivedRequirements(BaseModel):
    minZones: Literal[1, 2, 3]
    requiresSpotScore: bool
    requiresQuotaCheck: bool
    requiresPriceCheck: bool


# ---------------------------------------------------------------------------
# Response sub-models
# ---------------------------------------------------------------------------


class QuotaEvaluation(BaseModel):
    remainingVcpu: int | None = None
    vcpuPerVm: int | None = None
    maxInstancesFromQuota: int | None = None
    status: Literal["ok", "low", "blocking", "unknown"] = "unknown"


class SpotEvaluation(BaseModel):
    score: Literal["High", "Medium", "Low", "Unknown"] = "Unknown"


class PricingEvaluation(BaseModel):
    paygoPerHour: float | None = None
    spotPerHour: float | None = None
    estimatedHourlyCostPaygo: float | None = None
    estimatedHourlyCostSpot: float | None = None


class ConfidenceEvaluation(BaseModel):
    score: int | None = None
    label: str | None = None
    missingInputs: list[str] = Field(default_factory=list)


class VerdictEvaluation(BaseModel):
    eligible: bool
    reasonCodes: list[str] = Field(default_factory=list)


class RegionSkuEvaluation(BaseModel):
    region: str
    sku: str
    instanceCount: int
    zonesSupportedCount: int
    restrictionsPresent: bool
    restrictionReasonCodes: list[str] = Field(default_factory=list)
    quota: QuotaEvaluation = Field(default_factory=QuotaEvaluation)
    spot: SpotEvaluation = Field(default_factory=SpotEvaluation)
    pricing: PricingEvaluation = Field(default_factory=PricingEvaluation)
    confidence: ConfidenceEvaluation = Field(default_factory=ConfidenceEvaluation)
    verdict: VerdictEvaluation


# ---------------------------------------------------------------------------
# Response top-level models
# ---------------------------------------------------------------------------


class BusinessAlternative(BaseModel):
    region: str
    sku: str
    reason: str


class Summary(BaseModel):
    recommendedRegion: str | None = None
    recommendedSku: str | None = None
    recommendedMode: Literal["zonal", "regional"] | None = None
    riskLevel: Literal["low", "medium", "high"] | None = None
    confidenceScore: int | None = None
    missingInputs: list[str] = Field(default_factory=list)


class BusinessView(BaseModel):
    keyMessage: str
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)
    alternatives: list[BusinessAlternative] = Field(default_factory=list)


class DataProvenance(BaseModel):
    evaluatedAt: str | None = None
    cacheTtl: dict[str, str] = Field(default_factory=dict)
    apiVersions: dict[str, str] = Field(default_factory=dict)


class Evaluation(BaseModel):
    regionsEvaluated: list[str] = Field(default_factory=list)
    perRegionResults: list[RegionSkuEvaluation] = Field(default_factory=list)


class TechnicalView(BaseModel):
    evaluation: Evaluation = Field(default_factory=Evaluation)
    dataProvenance: DataProvenance = Field(default_factory=DataProvenance)


class DeploymentPlanResponse(BaseModel):
    summary: Summary
    businessView: BusinessView
    technicalView: TechnicalView
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
