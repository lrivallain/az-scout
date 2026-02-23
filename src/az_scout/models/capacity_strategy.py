"""Pydantic models for the Capacity Strategy Advisor.

Request models describe a workload profile with constraints.
Response models provide a deterministic, multi-region deployment strategy
with business and technical views — no LLM, no invented data.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ScaleSpec(BaseModel):
    sku: str | None = None
    instanceCount: int = 1
    gpuCountTotal: int | None = None


class WorkloadConstraints(BaseModel):
    dataResidency: Literal["FR", "EU", "ANY"] | None = None
    allowRegions: list[str] | None = None
    denyRegions: list[str] | None = None
    requireZonal: bool = False
    maxInterRegionRttMs: int | None = None


class UsageProfile(BaseModel):
    userDistribution: dict[str, float] | None = None
    statefulness: Literal["stateless", "soft-state", "stateful"] = "stateless"
    crossRegionTraffic: Literal["low", "medium", "heavy"] = "low"
    latencySensitivity: Literal["low", "medium", "high"] = "medium"


class DataProfile(BaseModel):
    replicationMode: Literal["none", "async", "sync", "multi-master", "unknown"] = "unknown"
    rpoTargetSeconds: int | None = None
    rtoTargetSeconds: int | None = None


class TimingSpec(BaseModel):
    deploymentUrgency: Literal["now", "today", "this_week"] = "this_week"
    deploymentWindow: Literal["anytime", "night_cet", "weekend"] | None = None


class PricingSpec(BaseModel):
    currencyCode: Literal["USD", "EUR"] = "USD"
    preferSpot: bool = False
    maxHourlyBudget: float | None = None


class WorkloadProfileRequest(BaseModel):
    """Full workload profile for capacity strategy computation."""

    workloadName: str
    subscriptionId: str
    tenantId: str | None = None
    scale: ScaleSpec = Field(default_factory=ScaleSpec)
    constraints: WorkloadConstraints = Field(default_factory=WorkloadConstraints)
    usage: UsageProfile = Field(default_factory=UsageProfile)
    data: DataProfile = Field(default_factory=DataProfile)
    timing: TimingSpec = Field(default_factory=TimingSpec)
    pricing: PricingSpec = Field(default_factory=PricingSpec)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

StrategyType = Literal[
    "single_region",
    "active_active",
    "active_passive",
    "sharded_multi_region",
    "burst_overflow",
    "time_window_deploy",
    "progressive_ramp",
]


class RegionAllocation(BaseModel):
    """One region within the strategy."""

    region: str
    role: Literal["primary", "secondary", "burst", "shard"]
    sku: str
    instanceCount: int
    zones: list[str] = Field(default_factory=list)
    quotaRemaining: int | None = None
    spotScore: str | None = None
    paygoPerHour: float | None = None
    spotPerHour: float | None = None
    confidenceScore: int | None = None
    confidenceLabel: str | None = None
    rttFromPrimaryMs: int | None = None


class StrategySummary(BaseModel):
    workloadName: str
    strategy: StrategyType
    totalInstances: int
    regionCount: int
    estimatedHourlyCost: float | None = None
    currency: str = "USD"
    overallConfidence: int | None = None
    overallConfidenceLabel: str | None = None


class BusinessView(BaseModel):
    keyMessage: str
    justification: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)


class TechnicalView(BaseModel):
    allocations: list[RegionAllocation] = Field(default_factory=list)
    latencyMatrix: dict[str, dict[str, int | None]] = Field(default_factory=dict)
    evaluatedAt: str | None = None


class CapacityStrategyResponse(BaseModel):
    summary: StrategySummary
    businessView: BusinessView
    technicalView: TechnicalView
    warnings: list[str] = Field(default_factory=list)
    missingInputs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "This tool is not affiliated with Microsoft. "
        "All capacity, pricing and latency information are indicative "
        "and not a guarantee of deployment success."
    )
