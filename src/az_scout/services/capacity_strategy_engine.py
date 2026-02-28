"""Capacity Strategy Engine – deterministic multi-region deployment advisor.

Evaluates (region, SKU) combinations using capacity signals already collected
by az-scout (zones, quotas, restrictions, spot scores, prices, confidence)
plus inter-region latency statistics, to recommend a deployment strategy.

No LLM. No invented data. Missing information is flagged explicitly.
"""

import logging
from datetime import UTC, datetime

from az_scout import azure_api
from az_scout.models.capacity_strategy import (
    BusinessView,
    CapacityStrategyResponse,
    RegionAllocation,
    StrategySummary,
    StrategyType,
    TechnicalView,
    WorkloadProfileRequest,
)
from az_scout.scoring.deployment_confidence import (
    DeploymentSignals,
    compute_deployment_confidence,
)
from az_scout.services._evaluation_helpers import (
    SPOT_RANK,
    best_spot_label,
    is_gpu_family,
    resolve_candidate_regions,
)
from az_scout.services.region_latency import get_rtt_ms

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_REGIONS = 10
_MAX_SKUS_PER_REGION = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_candidate_regions(
    profile: WorkloadProfileRequest,
    warnings: list[str],
    errors: list[str],
) -> list[str]:
    """Resolve candidate regions from workload constraints."""
    c = profile.constraints
    return resolve_candidate_regions(
        allow_regions=c.allowRegions,
        deny_regions=c.denyRegions,
        data_residency=c.dataResidency,
        subscription_id=profile.subscriptionId,
        tenant_id=profile.tenantId,
        warnings=warnings,
        errors=errors,
        max_regions=_MAX_REGIONS,
    )


# ---------------------------------------------------------------------------
# Region evaluation
# ---------------------------------------------------------------------------


class _RegionEval:
    """Intermediate evaluation of a (region, SKU) pair."""

    def __init__(
        self,
        region: str,
        sku_name: str,
        zones: list[str],
        restrictions: list[str],
        vcpus: int | None,
        quota_remaining: int | None,
        spot_label: str,
        paygo: float | None,
        spot_price: float | None,
        confidence_score: int | None,
        confidence_label: str | None,
        family: str,
    ):
        self.region = region
        self.sku_name = sku_name
        self.zones = zones
        self.restrictions = restrictions
        self.vcpus = vcpus
        self.quota_remaining = quota_remaining
        self.spot_label = spot_label
        self.paygo = paygo
        self.spot_price = spot_price
        self.confidence_score = confidence_score
        self.confidence_label = confidence_label
        self.family = family

    @property
    def is_restricted(self) -> bool:
        return len(self.restrictions) > 0

    @property
    def available_zone_count(self) -> int:
        return len([z for z in self.zones if z not in self.restrictions])

    def max_instances_from_quota(self) -> int | None:
        if self.quota_remaining is None or self.vcpus is None or self.vcpus <= 0:
            return None
        return self.quota_remaining // self.vcpus


def _evaluate_region_skus(
    region: str,
    profile: WorkloadProfileRequest,
    warnings: list[str],
    errors: list[str],
) -> list[_RegionEval]:
    """Fetch and evaluate SKUs in a single region."""
    sub_id = profile.subscriptionId
    tenant_id = profile.tenantId
    target_sku = profile.scale.sku
    currency = profile.pricing.currencyCode

    # Fetch SKUs
    try:
        if target_sku:
            skus = azure_api.get_skus(region, sub_id, tenant_id, "virtualMachines", name=target_sku)
        else:
            skus = azure_api.get_skus(region, sub_id, tenant_id, "virtualMachines")
    except Exception as exc:
        errors.append(f"Failed to fetch SKUs for {region}: {exc}")
        return []

    # Filter GPU if needed
    if profile.scale.gpuCountTotal and profile.scale.gpuCountTotal > 0:
        skus = [s for s in skus if is_gpu_family(s.get("family", ""))]

    # Limit
    skus = skus[:_MAX_SKUS_PER_REGION]

    if not skus:
        return []

    # Enrich quotas
    try:
        azure_api.enrich_skus_with_quotas(skus, region, sub_id, tenant_id)
    except Exception as exc:
        errors.append(f"Failed to fetch quotas for {region}: {exc}")

    # Enrich prices
    try:
        azure_api.enrich_skus_with_prices(skus, region, currency)
    except Exception as exc:
        errors.append(f"Failed to fetch prices for {region}: {exc}")

    # Spot scores
    sku_names = [s["name"] for s in skus]
    spot_scores: dict[str, dict[str, str]] = {}
    try:
        spot_result = azure_api.get_spot_placement_scores(
            region, sub_id, sku_names, profile.scale.instanceCount, tenant_id
        )
        spot_scores = spot_result.get("scores", {})
        for err in spot_result.get("errors", []):
            errors.append(f"Spot score error in {region}: {err}")
    except Exception as exc:
        errors.append(f"Failed to fetch spot scores for {region}: {exc}")

    evals: list[_RegionEval] = []
    for sku in skus:
        name = sku.get("name", "")
        zones = sku.get("zones", [])
        restrictions = sku.get("restrictions", [])
        caps = sku.get("capabilities", {})
        quota = sku.get("quota", {})
        pricing = sku.get("pricing", {})

        try:
            vcpus = int(caps.get("vCPUs", "0"))
        except (TypeError, ValueError):
            vcpus = None

        remaining = quota.get("remaining")

        sku_spot = spot_scores.get(name, {})
        spot_label = best_spot_label(sku_spot)

        conf = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=vcpus,
                zones_available_count=len(zones),
                restrictions_present=len(restrictions) > 0,
                quota_remaining_vcpu=remaining,
                spot_score_label=spot_label if spot_label != "Unknown" else None,
                paygo_price=pricing.get("paygo"),
                spot_price=pricing.get("spot"),
            )
        )

        evals.append(
            _RegionEval(
                region=region,
                sku_name=name,
                zones=zones,
                restrictions=restrictions,
                vcpus=vcpus,
                quota_remaining=remaining,
                spot_label=spot_label,
                paygo=pricing.get("paygo"),
                spot_price=pricing.get("spot"),
                confidence_score=conf.score,
                confidence_label=conf.label,
                family=sku.get("family", ""),
            )
        )

    return evals


def _pick_best_sku(
    evals: list[_RegionEval],
    prefer_spot: bool,
) -> _RegionEval | None:
    """Pick the best SKU evaluation from a list (highest confidence, not restricted)."""
    eligible = [e for e in evals if not e.is_restricted]
    if not eligible:
        return None

    def sort_key(e: _RegionEval) -> tuple[int, int, float]:
        conf = -(e.confidence_score or 0)
        spot = -SPOT_RANK.get(e.spot_label, 0)
        if prefer_spot and e.spot_price is not None:
            cost = e.spot_price
        elif e.paygo is not None:
            cost = e.paygo
        else:
            cost = float("inf")
        return (conf, spot, cost)

    eligible.sort(key=sort_key)
    return eligible[0]


# ---------------------------------------------------------------------------
# Strategy selection logic
# ---------------------------------------------------------------------------


def _select_strategy(
    profile: WorkloadProfileRequest,
    region_bests: list[_RegionEval],
    primary: _RegionEval | None,
    warnings: list[str],
    missing_inputs: list[str],
) -> StrategyType:
    """Deterministic strategy selection based on signals and constraints."""
    if not primary:
        return "single_region"

    instance_count = profile.scale.instanceCount
    statefulness = profile.usage.statefulness
    prefer_spot = profile.pricing.preferSpot
    latency_sensitivity = profile.usage.latencySensitivity

    # Check if primary has enough quota
    max_from_quota = primary.max_instances_from_quota()
    quota_sufficient = max_from_quota is not None and max_from_quota >= instance_count
    quota_partial = max_from_quota is not None and 0 < max_from_quota < instance_count
    quota_unknown = max_from_quota is None

    if quota_unknown:
        missing_inputs.append("quota")

    # Single region viable?
    single_viable = quota_sufficient or quota_unknown

    # If only one region available, must be single
    if len(region_bests) <= 1:
        if not single_viable and not quota_unknown:
            warnings.append(
                "Only one region available but quota is insufficient. "
                "Consider requesting a quota increase."
            )
        return "single_region"

    # Spot + low score -> time window
    if prefer_spot and primary.spot_label == "Low":
        return "time_window_deploy"

    # Quota blocking -> shard across regions
    if not single_viable and not quota_unknown:
        if quota_partial:
            return "progressive_ramp"
        return "sharded_multi_region"

    # Stateful -> active/passive
    if statefulness == "stateful":
        return "active_passive"

    # Stateless -> check if multi-region beneficial
    if statefulness in ("stateless", "soft-state"):
        # High latency sensitivity + high RTT -> keep single region
        if latency_sensitivity == "high":
            # Check RTT to other regions
            max_rtt = profile.constraints.maxInterRegionRttMs
            if max_rtt is not None and len(region_bests) > 1:
                secondary_rtt = get_rtt_ms(primary.region, region_bests[1].region)
                if secondary_rtt is not None and secondary_rtt > max_rtt:
                    warnings.append(
                        f"Inter-region RTT ({secondary_rtt}ms) exceeds constraint "
                        f"({max_rtt}ms). Limiting to single region."
                    )
                    return "single_region"

        # If enough quota in single region and low cross-region traffic
        if single_viable and profile.usage.crossRegionTraffic == "low":
            return "single_region"

        return "active_active"

    return "single_region"


# ---------------------------------------------------------------------------
# Build allocations
# ---------------------------------------------------------------------------


def _build_allocations(
    strategy: StrategyType,
    profile: WorkloadProfileRequest,
    region_bests: list[_RegionEval],
    primary: _RegionEval,
    warnings: list[str],
) -> list[RegionAllocation]:
    """Build region allocations based on the chosen strategy."""
    instance_count = profile.scale.instanceCount
    allocations: list[RegionAllocation] = []

    def _make_alloc(ev: _RegionEval, role: str, count: int) -> RegionAllocation:
        rtt = None
        if role != "primary" and primary:
            rtt = get_rtt_ms(primary.region, ev.region)
        return RegionAllocation(
            region=ev.region,
            role=role,  # type: ignore[arg-type]
            sku=ev.sku_name,
            instanceCount=count,
            zones=[z for z in ev.zones if z not in ev.restrictions],
            quotaRemaining=ev.quota_remaining,
            spotScore=ev.spot_label if ev.spot_label != "Unknown" else None,
            paygoPerHour=ev.paygo,
            spotPerHour=ev.spot_price,
            confidenceScore=ev.confidence_score,
            confidenceLabel=ev.confidence_label,
            rttFromPrimaryMs=rtt,
        )

    if strategy == "single_region":
        allocations.append(_make_alloc(primary, "primary", instance_count))

    elif strategy == "active_active":
        # Split instances across top 2 regions
        secondaries = [e for e in region_bests if e.region != primary.region]
        if secondaries:
            half = instance_count // 2
            remainder = instance_count - half
            allocations.append(_make_alloc(primary, "primary", half))
            allocations.append(_make_alloc(secondaries[0], "secondary", remainder))
        else:
            allocations.append(_make_alloc(primary, "primary", instance_count))

    elif strategy == "active_passive":
        secondaries = [e for e in region_bests if e.region != primary.region]
        allocations.append(_make_alloc(primary, "primary", instance_count))
        if secondaries:
            # Passive region: same count for failover
            allocations.append(_make_alloc(secondaries[0], "secondary", instance_count))

    elif strategy == "sharded_multi_region":
        # Distribute across all available regions based on quota
        remaining_to_place = instance_count
        for ev in region_bests:
            if remaining_to_place <= 0:
                break
            max_here = ev.max_instances_from_quota()
            count = remaining_to_place if max_here is None else min(remaining_to_place, max_here)
            if count <= 0:
                continue
            role = "primary" if ev.region == primary.region else "shard"
            allocations.append(_make_alloc(ev, role, count))
            remaining_to_place -= count

        if remaining_to_place > 0:
            warnings.append(
                f"Could not place {remaining_to_place} instance(s) — "
                "insufficient quota across all evaluated regions."
            )

    elif strategy == "burst_overflow":
        allocations.append(_make_alloc(primary, "primary", instance_count))
        secondaries = [e for e in region_bests if e.region != primary.region]
        for sec in secondaries[:1]:
            allocations.append(_make_alloc(sec, "burst", 0))

    elif strategy == "time_window_deploy":
        allocations.append(_make_alloc(primary, "primary", instance_count))

    elif strategy == "progressive_ramp":
        # Place what quota allows, rest in secondary
        max_primary = primary.max_instances_from_quota() or 0
        placed = min(instance_count, max_primary)
        allocations.append(_make_alloc(primary, "primary", placed))
        overflow = instance_count - placed
        if overflow > 0:
            secondaries = [e for e in region_bests if e.region != primary.region]
            for sec in secondaries:
                if overflow <= 0:
                    break
                max_sec = sec.max_instances_from_quota()
                count = overflow if max_sec is None else min(overflow, max_sec)
                if count > 0:
                    allocations.append(_make_alloc(sec, "shard", count))
                    overflow -= count
            if overflow > 0:
                warnings.append(
                    f"Could not place {overflow} instance(s) in progressive ramp — "
                    "insufficient quota across evaluated regions."
                )

    return allocations


# ---------------------------------------------------------------------------
# Build latency matrix
# ---------------------------------------------------------------------------


def _build_latency_matrix(
    regions: list[str],
    missing_inputs: list[str],
) -> dict[str, dict[str, int | None]]:
    matrix: dict[str, dict[str, int | None]] = {}
    has_unknown = False
    for a in regions:
        matrix[a] = {}
        for b in regions:
            rtt = get_rtt_ms(a, b)
            matrix[a][b] = rtt
            if rtt is None and a != b:
                has_unknown = True
    if has_unknown and "latency" not in missing_inputs:
        missing_inputs.append("latency")
    return matrix


# ---------------------------------------------------------------------------
# Build business view
# ---------------------------------------------------------------------------


def _build_business_view(
    strategy: StrategyType,
    profile: WorkloadProfileRequest,
    allocations: list[RegionAllocation],
    all_evals: list[_RegionEval],
    warnings: list[str],
) -> BusinessView:
    """Generate business-friendly explanation."""
    if not allocations:
        return BusinessView(
            keyMessage=(
                "No eligible deployment option found matching constraints. "
                "Consider relaxing region, SKU, or budget requirements."
            ),
            risks=["All evaluated options are ineligible or restricted."],
            mitigations=["Review warnings and errors for details."],
        )

    primary = allocations[0]
    region_count = len(allocations)

    strategy_names: dict[str, str] = {
        "single_region": "Single-region deployment",
        "active_active": "Active-active multi-region",
        "active_passive": "Active-passive (failover)",
        "sharded_multi_region": "Sharded multi-region",
        "burst_overflow": "Burst overflow",
        "time_window_deploy": "Time-window deployment (wait for spot)",
        "progressive_ramp": "Progressive ramp-up",
    }

    key_message = (
        f"Recommended strategy: {strategy_names.get(strategy, strategy)} "
        f"for '{profile.workloadName}'. "
        f"Primary region: {primary.region} ({primary.sku}, "
        f"{primary.confidenceLabel or 'Unknown'} confidence)."
    )
    if region_count > 1:
        key_message += f" {region_count} region(s) total."

    justification: list[str] = []
    risks: list[str] = []
    mitigations: list[str] = []

    # Justifications
    if strategy == "single_region":
        justification.append("All capacity requirements can be met in a single region.")
    elif strategy == "active_active":
        justification.append("Stateless workload benefits from multi-region active-active.")
    elif strategy == "active_passive":
        justification.append("Stateful workload requires active-passive for data consistency.")
    elif strategy == "sharded_multi_region":
        justification.append("Insufficient quota in any single region; sharding across regions.")
    elif strategy == "time_window_deploy":
        justification.append("Spot placement score is low; wait for a better deployment window.")
    elif strategy == "progressive_ramp":
        justification.append("Partial quota available; ramp up progressively.")

    if primary.confidenceScore is not None:
        justification.append(
            f"Primary region confidence score: {primary.confidenceScore}/100 "
            f"({primary.confidenceLabel})."
        )

    if primary.paygoPerHour is not None:
        justification.append(
            f"Estimated primary cost: "
            f"{primary.paygoPerHour * primary.instanceCount:.2f}/hr (PayGo)."
        )

    # Risks
    restricted_regions = [e for e in all_evals if e.is_restricted]
    if restricted_regions:
        risks.append(
            f"SKU restrictions detected in {len(restricted_regions)} region/SKU combination(s)."
        )
        mitigations.append("Consider alternative SKUs or contact Azure support for exemptions.")

    low_quota = [
        e
        for e in all_evals
        if e.quota_remaining is not None
        and e.vcpus is not None
        and e.vcpus > 0
        and e.quota_remaining < e.vcpus * profile.scale.instanceCount
    ]
    if low_quota:
        risks.append("Quota is insufficient or low in some regions.")
        mitigations.append("Request quota increase via Azure portal.")

    low_spot = [e for e in all_evals if e.spot_label == "Low"]
    if low_spot and profile.pricing.preferSpot:
        risks.append("Spot placement probability is low in some regions.")
        mitigations.append("Consider on-demand (PayGo) pricing as fallback.")

    if strategy in ("active_active", "sharded_multi_region"):
        risks.append("Multi-region deployments increase operational complexity.")
        mitigations.append(
            "Implement health probes and automated failover. "
            "Validate inter-region latency with Connection Monitor."
        )

    return BusinessView(
        keyMessage=key_message,
        justification=justification,
        risks=risks,
        mitigations=mitigations,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def recommend_capacity_strategy(
    profile: WorkloadProfileRequest,
) -> CapacityStrategyResponse:
    """Compute a deterministic Azure deployment strategy.

    Uses capacity signals (zones, quotas, restrictions, spot scores,
    prices, confidence) and inter-region latency statistics.

    Args:
        profile: The workload profile from the agent / user.
    """
    warnings: list[str] = [
        "Spot placement score is probabilistic and not a guarantee.",
        (
            "Latency values are indicative (source: Microsoft published statistics) "
            "and must be validated with in-tenant measurements."
        ),
    ]
    errors: list[str] = []
    missing_inputs: list[str] = []

    # 1. Resolve candidate regions
    candidate_regions = _resolve_candidate_regions(profile, warnings, errors)

    if not candidate_regions:
        errors.append("No candidate regions resolved from the given constraints.")
        return CapacityStrategyResponse(
            summary=StrategySummary(
                workloadName=profile.workloadName,
                strategy="single_region",
                totalInstances=profile.scale.instanceCount,
                regionCount=0,
                currency=profile.pricing.currencyCode,
            ),
            businessView=BusinessView(
                keyMessage="No candidate regions available.",
                risks=["Cannot evaluate any region."],
            ),
            technicalView=TechnicalView(
                evaluatedAt=datetime.now(UTC).isoformat(),
            ),
            warnings=warnings,
            missingInputs=missing_inputs,
            errors=errors,
        )

    # 2. Evaluate each region
    all_evals: list[_RegionEval] = []
    for region in candidate_regions:
        try:
            evals = _evaluate_region_skus(region, profile, warnings, errors)
            all_evals.extend(evals)
        except Exception as exc:
            errors.append(f"Failed to evaluate region {region}: {exc}")

    # 3. Pick best SKU per region
    by_region: dict[str, list[_RegionEval]] = {}
    for ev in all_evals:
        by_region.setdefault(ev.region, []).append(ev)

    prefer_spot = profile.pricing.preferSpot
    region_bests: list[_RegionEval] = []
    for region in candidate_regions:
        region_evals = by_region.get(region, [])
        best = _pick_best_sku(region_evals, prefer_spot)
        if best:
            region_bests.append(best)

    # Sort region_bests by confidence (descending)
    region_bests.sort(key=lambda e: -(e.confidence_score or 0))

    primary = region_bests[0] if region_bests else None

    # 4. Select strategy
    strategy = _select_strategy(profile, region_bests, primary, warnings, missing_inputs)

    # 5. Build allocations
    allocations: list[RegionAllocation] = []
    if primary:
        allocations = _build_allocations(strategy, profile, region_bests, primary, warnings)

    # 6. Latency matrix
    alloc_regions = list({a.region for a in allocations})
    latency_matrix = _build_latency_matrix(alloc_regions, missing_inputs)

    # 7. Business view
    business_view = _build_business_view(strategy, profile, allocations, all_evals, warnings)

    # 8. Compute summary
    total_instances = sum(a.instanceCount for a in allocations)
    estimated_cost: float | None = None
    currency = profile.pricing.currencyCode
    cost_parts: list[float] = []
    for a in allocations:
        if prefer_spot and a.spotPerHour is not None:
            cost_parts.append(a.spotPerHour * a.instanceCount)
        elif a.paygoPerHour is not None:
            cost_parts.append(a.paygoPerHour * a.instanceCount)
    if cost_parts:
        estimated_cost = sum(cost_parts)

    overall_conf_scores = [a.confidenceScore for a in allocations if a.confidenceScore is not None]
    overall_confidence = (
        round(sum(overall_conf_scores) / len(overall_conf_scores)) if overall_conf_scores else None
    )
    overall_label = None
    if overall_confidence is not None:
        if overall_confidence >= 80:
            overall_label = "High"
        elif overall_confidence >= 60:
            overall_label = "Medium"
        elif overall_confidence >= 40:
            overall_label = "Low"
        else:
            overall_label = "Very Low"

    summary = StrategySummary(
        workloadName=profile.workloadName,
        strategy=strategy,
        totalInstances=total_instances,
        regionCount=len(alloc_regions),
        estimatedHourlyCost=round(estimated_cost, 4) if estimated_cost is not None else None,
        currency=currency,
        overallConfidence=overall_confidence,
        overallConfidenceLabel=overall_label,
    )

    # 9. Technical view
    technical_view = TechnicalView(
        allocations=allocations,
        latencyMatrix=latency_matrix,
        evaluatedAt=datetime.now(UTC).isoformat(),
    )

    # Budget check warning
    if (
        profile.pricing.maxHourlyBudget is not None
        and estimated_cost is not None
        and estimated_cost > profile.pricing.maxHourlyBudget
    ):
        warnings.append(
            f"Estimated hourly cost ({estimated_cost:.2f} {currency}) "
            f"exceeds budget ({profile.pricing.maxHourlyBudget:.2f} {currency})."
        )

    return CapacityStrategyResponse(
        summary=summary,
        businessView=business_view,
        technicalView=technical_view,
        warnings=warnings,
        missingInputs=missing_inputs,
        errors=errors,
    )
