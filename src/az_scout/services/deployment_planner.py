"""Deployment planner – deterministic decision engine.

Evaluates (region, SKU) combinations against the derived requirements and
produces a ranked deployment plan with business and technical views.

No LLM, no probabilistic inference: every decision is traceable to data.
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from az_scout import azure_api
from az_scout.models.deployment_plan import (
    BusinessAlternative,
    BusinessView,
    ConfidenceEvaluation,
    DataProvenance,
    DeploymentIntentRequest,
    DeploymentPlanResponse,
    DerivedRequirements,
    Evaluation,
    PricingEvaluation,
    QuotaEvaluation,
    RegionSkuEvaluation,
    SpotEvaluation,
    Summary,
    TechnicalView,
    VerdictEvaluation,
)
from az_scout.services.capacity_confidence import compute_capacity_confidence
from az_scout.services.intent_parser import derive_requirements

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_REGIONS = 8
DEFAULT_MAX_SKUS = 50

_GPU_FAMILY_MARKERS = ("nc", "nd", "nv", "hb", "hc")

_SPOT_RANK = {"High": 3, "Medium": 2, "Low": 1, "Unknown": 0}

DATA_RESIDENCY_REGIONS: dict[str, list[str]] = {
    "FR": ["francecentral", "francesouth"],
    "EU": [
        "francecentral",
        "francesouth",
        "westeurope",
        "northeurope",
        "germanywestcentral",
        "germanynorth",
        "swedencentral",
        "switzerlandnorth",
        "switzerlandwest",
        "norwayeast",
        "norwaywest",
        "polandcentral",
        "italynorth",
        "spaincentral",
    ],
}

_RISK_MESSAGES: dict[str, str] = {
    "ZoneMissing": "Insufficient availability zones for zonal deployment in some regions.",
    "Restricted": "Some SKUs have deployment restrictions in evaluated regions.",
    "QuotaBlocking": "vCPU quota is insufficient for the requested instance count.",
    "SpotLow": "Spot VM placement probability is low.",
    "BudgetExceeded": "Some options exceed the specified hourly budget.",
}

_MITIGATION_MESSAGES: dict[str, str] = {
    "ZoneMissing": "Consider regional deployment mode or regions with more availability zones.",
    "Restricted": "Use alternative SKUs without restrictions or contact Azure support.",
    "QuotaBlocking": "Request a quota increase via the Azure portal or use a different region.",
    "SpotLow": "Consider on-demand (PayGo) pricing as a fallback.",
    "BudgetExceeded": "Consider smaller SKUs, fewer instances, or Spot pricing.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_gpu_family(family: str) -> bool:
    """Return True if the SKU family is a GPU/HPC family."""
    normalized = family.lower().replace("standard", "").replace("_", "")
    return any(normalized.startswith(m) for m in _GPU_FAMILY_MARKERS)


def _best_spot_score(
    zone_scores: dict[str, str],
) -> Literal["High", "Medium", "Low", "Unknown"]:
    """Return the best (most optimistic) score across zones."""
    if not zone_scores:
        return "Unknown"
    best = max(zone_scores.values(), key=lambda s: _SPOT_RANK.get(s, 0))
    if best in ("High", "Medium", "Low"):
        return best  # type: ignore[return-value]
    return "Unknown"


def _evaluate_quota(
    remaining: int | None,
    vcpu_per_vm: int | None,
    instance_count: int,
) -> QuotaEvaluation:
    """Evaluate quota headroom for a (SKU, region) pair."""
    if remaining is None or vcpu_per_vm is None:
        return QuotaEvaluation(
            remainingVcpu=remaining,
            vcpuPerVm=vcpu_per_vm,
            status="unknown",
        )

    needed = vcpu_per_vm * instance_count
    max_instances = remaining // vcpu_per_vm if vcpu_per_vm > 0 else None

    status: Literal["ok", "low", "blocking", "unknown"]
    if remaining <= 0 or (needed > 0 and remaining < needed):
        status = "blocking"
    elif remaining < needed * 2:
        status = "low"
    else:
        status = "ok"

    return QuotaEvaluation(
        remainingVcpu=remaining,
        vcpuPerVm=vcpu_per_vm,
        maxInstancesFromQuota=max_instances,
        status=status,
    )


def _evaluate_pricing(
    pricing_data: dict,
    instance_count: int,
) -> PricingEvaluation:
    """Build a PricingEvaluation from enriched SKU pricing data."""
    paygo = pricing_data.get("paygo")
    spot = pricing_data.get("spot")
    return PricingEvaluation(
        paygoPerHour=paygo,
        spotPerHour=spot,
        estimatedHourlyCostPaygo=paygo * instance_count if paygo is not None else None,
        estimatedHourlyCostSpot=spot * instance_count if spot is not None else None,
    )


def _resolve_candidate_regions(
    intent: DeploymentIntentRequest,
    warnings: list[str],
    errors: list[str],
) -> list[str]:
    """Resolve candidate regions from intent constraints."""
    rc = intent.regionConstraints

    if rc and rc.allowRegions:
        candidates = list(rc.allowRegions)
    elif rc and rc.dataResidency and rc.dataResidency != "ANY":
        residency = rc.dataResidency
        if residency in DATA_RESIDENCY_REGIONS:
            candidates = list(DATA_RESIDENCY_REGIONS[residency])
        else:
            warnings.append(
                f"No region mapping for data residency '{residency}'. Using all available regions."
            )
            candidates = _fetch_all_regions(intent, errors)
    else:
        candidates = _fetch_all_regions(intent, errors)

    # Apply deny list
    if rc and rc.denyRegions:
        deny_set = {r.lower() for r in rc.denyRegions}
        candidates = [r for r in candidates if r.lower() not in deny_set]

    return candidates


def _fetch_all_regions(
    intent: DeploymentIntentRequest,
    errors: list[str],
) -> list[str]:
    """Fetch AZ-enabled regions from the Azure API."""
    try:
        regions = azure_api.list_regions(intent.subscriptionId, intent.tenantId)
        return [r["name"] for r in regions]
    except Exception as exc:
        errors.append(f"Failed to list regions: {exc}")
        return []


def _evaluate_sku(
    sku: dict,
    region: str,
    intent: DeploymentIntentRequest,
    requirements: DerivedRequirements,
    spot_scores: dict[str, dict[str, str]],
) -> RegionSkuEvaluation:
    """Evaluate a single SKU in a region against the requirements."""
    instance_count = intent.scale.instanceCount
    sku_name = sku.get("name", "")

    # Zones
    zones: list[str] = sku.get("zones", [])
    zones_count = len(zones)

    # Restrictions
    restrictions: list[str] = sku.get("restrictions", [])
    restrictions_present = len(restrictions) > 0

    # Quota
    quota_data = sku.get("quota", {})
    remaining = quota_data.get("remaining")
    try:
        vcpu_per_vm = int(sku.get("capabilities", {}).get("vCPUs", "0"))
    except (TypeError, ValueError):
        vcpu_per_vm = None
    quota_eval = _evaluate_quota(remaining, vcpu_per_vm, instance_count)

    # Spot
    sku_spot_scores = spot_scores.get(sku_name, {})
    spot_label = _best_spot_score(sku_spot_scores)
    spot_eval = SpotEvaluation(score=spot_label)

    # Pricing
    pricing_data = sku.get("pricing", {})
    pricing_eval = _evaluate_pricing(pricing_data, instance_count)

    # Confidence
    conf_spot_label = spot_label if spot_label != "Unknown" else None
    confidence_result = compute_capacity_confidence(
        vcpus=vcpu_per_vm,
        zones_supported_count=zones_count,
        restrictions_present=restrictions_present,
        quota_remaining_vcpu=remaining,
        spot_score_label=conf_spot_label,
        paygo_price=pricing_data.get("paygo"),
        spot_price=pricing_data.get("spot"),
    )
    confidence_eval = ConfidenceEvaluation(
        score=confidence_result["score"],
        label=confidence_result["label"],
        missingInputs=confidence_result["missing"],
    )

    # Verdict
    reason_codes: list[str] = []
    eligible = True

    if zones_count < requirements.minZones:
        reason_codes.append("ZoneMissing")
        eligible = False

    if restrictions_present:
        reason_codes.append("Restricted")
        eligible = False

    if quota_eval.status == "blocking":
        reason_codes.append("QuotaBlocking")
        eligible = False

    if (
        spot_label == "Low"
        and requirements.requiresSpotScore
        and intent.pricing
        and intent.pricing.preferSpot
    ):
        reason_codes.append("SpotLow")

    # Budget check
    if intent.pricing and intent.pricing.maxHourlyBudget is not None:
        budget = intent.pricing.maxHourlyBudget
        cost: float | None
        if intent.pricing.preferSpot and pricing_eval.estimatedHourlyCostSpot is not None:
            cost = pricing_eval.estimatedHourlyCostSpot
        else:
            cost = pricing_eval.estimatedHourlyCostPaygo
        if cost is not None and cost > budget:
            reason_codes.append("BudgetExceeded")
            eligible = False

    verdict = VerdictEvaluation(eligible=eligible, reasonCodes=reason_codes)

    return RegionSkuEvaluation(
        region=region,
        sku=sku_name,
        instanceCount=instance_count,
        zonesSupportedCount=zones_count,
        restrictionsPresent=restrictions_present,
        restrictionReasonCodes=restrictions,
        quota=quota_eval,
        spot=spot_eval,
        pricing=pricing_eval,
        confidence=confidence_eval,
        verdict=verdict,
    )


def _evaluate_region(
    region: str,
    intent: DeploymentIntentRequest,
    requirements: DerivedRequirements,
    max_skus: int,
    warnings: list[str],
    errors: list[str],
) -> list[RegionSkuEvaluation]:
    """Evaluate all candidate SKUs in a region."""
    sub_id = intent.subscriptionId
    tenant_id = intent.tenantId
    pricing_pref = intent.pricing

    # Fetch SKUs
    try:
        skus = azure_api.get_skus(region, sub_id, tenant_id, "virtualMachines")
    except Exception as exc:
        errors.append(f"Failed to fetch SKUs for {region}: {exc}")
        return []

    # Enrich with quotas
    try:
        azure_api.enrich_skus_with_quotas(skus, region, sub_id, tenant_id)
    except Exception as exc:
        errors.append(f"Failed to fetch quotas for {region}: {exc}")

    # Enrich with prices if needed
    if requirements.requiresPriceCheck:
        currency = pricing_pref.currencyCode if pricing_pref else "USD"
        try:
            azure_api.enrich_skus_with_prices(skus, region, currency)
        except Exception as exc:
            errors.append(f"Failed to fetch prices for {region}: {exc}")

    # Filter by preferredSkus
    if intent.skuConstraints and intent.skuConstraints.preferredSkus:
        preferred = {s.lower() for s in intent.skuConstraints.preferredSkus}
        skus = [s for s in skus if s.get("name", "").lower() in preferred]

    # Filter by GPU
    if intent.skuConstraints and intent.skuConstraints.requireGpu:
        gpu_skus = [s for s in skus if _is_gpu_family(s.get("family", ""))]
        if not gpu_skus:
            warnings.append(
                f"No GPU SKUs found in {region}. "
                "GPU filter is based on family prefix (NC/ND/NV/HB/HC)."
            )
        skus = gpu_skus

    # Limit SKUs
    skus = skus[:max_skus]

    # Fetch spot scores if needed
    spot_scores: dict[str, dict[str, str]] = {}
    if requirements.requiresSpotScore and skus:
        sku_names = [s["name"] for s in skus]
        try:
            spot_result = azure_api.get_spot_placement_scores(
                region, sub_id, sku_names, intent.scale.instanceCount, tenant_id
            )
            spot_scores = spot_result.get("scores", {})
            for err in spot_result.get("errors", []):
                errors.append(f"Spot score error in {region}: {err}")
        except Exception as exc:
            errors.append(f"Failed to fetch spot scores for {region}: {exc}")

    # Evaluate each SKU
    evaluations: list[RegionSkuEvaluation] = []
    for sku in skus:
        evaluation = _evaluate_sku(sku, region, intent, requirements, spot_scores)
        evaluations.append(evaluation)

    return evaluations


def _ranking_key(
    evaluation: RegionSkuEvaluation,
    prefer_spot: bool,
) -> tuple[int, int, int, float]:
    """Produce a sort key: lower is better."""
    eligible_rank = 0 if evaluation.verdict.eligible else 1
    confidence = -(evaluation.confidence.score or 0)
    spot_rank = -_SPOT_RANK.get(evaluation.spot.score, 0)

    if prefer_spot and evaluation.pricing.estimatedHourlyCostSpot is not None:
        cost = evaluation.pricing.estimatedHourlyCostSpot
    elif evaluation.pricing.estimatedHourlyCostPaygo is not None:
        cost = evaluation.pricing.estimatedHourlyCostPaygo
    else:
        cost = float("inf")

    return (eligible_rank, confidence, spot_rank, cost)


def _build_business_view(
    recommended: RegionSkuEvaluation | None,
    alternatives: list[RegionSkuEvaluation],
    all_evaluations: list[RegionSkuEvaluation],
) -> BusinessView:
    """Generate a business-friendly view from evaluation results."""
    if recommended is None:
        return BusinessView(
            keyMessage=(
                "No eligible deployment option was found matching your constraints. "
                "Consider relaxing region, SKU, or budget requirements."
            ),
            risks=["All evaluated options are ineligible."],
            mitigations=["Review the technical view for per-option details."],
        )

    # Key message
    mode = "zonal" if recommended.zonesSupportedCount >= 3 else "regional"
    conf_label = recommended.confidence.label or "Unknown"
    key_message = (
        f"{recommended.sku} in {recommended.region} is recommended "
        f"for {mode} deployment ({recommended.zonesSupportedCount} AZs, "
        f"{conf_label} confidence)."
    )

    # Reasons
    reasons: list[str] = []
    reasons.append(f"Available in {recommended.zonesSupportedCount} availability zone(s).")
    if recommended.quota.status == "ok":
        reasons.append(f"Sufficient quota ({recommended.quota.remainingVcpu} vCPUs remaining).")
    if not recommended.restrictionsPresent:
        reasons.append("No deployment restrictions.")
    if recommended.spot.score != "Unknown":
        reasons.append(f"Spot placement score: {recommended.spot.score}.")
    if recommended.pricing.paygoPerHour is not None:
        reasons.append(
            f"Estimated hourly cost: {recommended.pricing.estimatedHourlyCostPaygo:.2f} "
            f"(PayGo) per {recommended.instanceCount} instance(s)."
        )

    # Risks and mitigations (from all evaluations)
    all_reason_codes: set[str] = set()
    for ev in all_evaluations:
        all_reason_codes.update(ev.verdict.reasonCodes)

    risks = [_RISK_MESSAGES[rc] for rc in all_reason_codes if rc in _RISK_MESSAGES]
    mitigations = [
        _MITIGATION_MESSAGES[rc] for rc in all_reason_codes if rc in _MITIGATION_MESSAGES
    ]

    # Alternatives
    biz_alternatives: list[BusinessAlternative] = []
    for alt in alternatives[:3]:
        alt_conf = alt.confidence.label or "Unknown"
        reason = f"{alt_conf} confidence, {alt.zonesSupportedCount} AZs"
        if alt.pricing.estimatedHourlyCostPaygo is not None:
            reason += f", ~{alt.pricing.estimatedHourlyCostPaygo:.2f}/hr"
        biz_alternatives.append(BusinessAlternative(region=alt.region, sku=alt.sku, reason=reason))

    return BusinessView(
        keyMessage=key_message,
        reasons=reasons,
        risks=risks,
        mitigations=mitigations,
        alternatives=biz_alternatives,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def plan_deployment(
    intent: DeploymentIntentRequest,
    *,
    max_regions: int = DEFAULT_MAX_REGIONS,
    max_skus: int = DEFAULT_MAX_SKUS,
) -> DeploymentPlanResponse:
    """Generate a deployment plan from a deployment intent.

    Deterministic: every decision is based on data already collected
    (zones, SKUs, quotas, spot scores, prices, restrictions, confidence).

    Args:
        intent: The deployment intent from the agent / user.
        max_regions: Maximum number of regions to evaluate.
        max_skus: Maximum number of SKUs per region to evaluate.
    """
    requirements = derive_requirements(intent)
    warnings: list[str] = [
        "Spot placement score is probabilistic and not a guarantee.",
    ]
    errors: list[str] = []

    # Data residency warning
    rc = intent.regionConstraints
    if (
        rc
        and rc.dataResidency
        and rc.dataResidency not in ("ANY", None)
        and rc.dataResidency not in DATA_RESIDENCY_REGIONS
    ):
        warnings.append(
            f"Data residency '{rc.dataResidency}' has no region mapping. "
            "Results may include non-compliant regions."
        )

    # 1. Resolve candidate regions
    candidate_regions = _resolve_candidate_regions(intent, warnings, errors)
    candidate_regions = candidate_regions[:max_regions]

    if not candidate_regions:
        errors.append("No candidate regions resolved from the given constraints.")

    # 2. Evaluate each region
    all_evaluations: list[RegionSkuEvaluation] = []
    for region in candidate_regions:
        try:
            region_evals = _evaluate_region(
                region, intent, requirements, max_skus, warnings, errors
            )
            all_evaluations.extend(region_evals)
        except Exception as exc:
            errors.append(f"Failed to evaluate region {region}: {exc}")

    # 3. Rank
    prefer_spot = intent.pricing.preferSpot if intent.pricing else False
    all_evaluations.sort(key=lambda e: _ranking_key(e, prefer_spot))

    eligible = [e for e in all_evaluations if e.verdict.eligible]
    recommended = eligible[0] if eligible else None
    alternatives = eligible[1:4] if len(eligible) > 1 else []

    # 4. Summary
    missing_inputs: list[str] = []
    risk_level: Literal["low", "medium", "high"] = "high"
    mode: Literal["zonal", "regional"] | None = None
    if recommended:
        missing_inputs = list(recommended.confidence.missingInputs)
        conf_score = recommended.confidence.score
        if conf_score is not None and conf_score >= 80:
            risk_level = "low"
        elif conf_score is not None and conf_score >= 60:
            risk_level = "medium"
        else:
            risk_level = "high"
        mode = "zonal" if recommended.zonesSupportedCount >= 3 else "regional"
    else:
        risk_level = "high"
        mode = None
        # Collect missing inputs from all evaluations
        for ev in all_evaluations:
            for mi in ev.confidence.missingInputs:
                if mi not in missing_inputs:
                    missing_inputs.append(mi)

    summary = Summary(
        recommendedRegion=recommended.region if recommended else None,
        recommendedSku=recommended.sku if recommended else None,
        recommendedMode=mode,
        riskLevel=risk_level,
        confidenceScore=recommended.confidence.score if recommended else None,
        missingInputs=missing_inputs,
    )

    # 5. Business view
    business_view = _build_business_view(recommended, alternatives, all_evaluations)

    # 6. Technical view (recommended + alternatives only, to limit size)
    included_evals: list[RegionSkuEvaluation] = []
    if recommended:
        included_evals.append(recommended)
    included_evals.extend(alternatives)

    technical_view = TechnicalView(
        evaluation=Evaluation(
            regionsEvaluated=list(candidate_regions),
            perRegionResults=included_evals,
        ),
        dataProvenance=DataProvenance(
            evaluatedAt=datetime.now(UTC).isoformat(),
            cacheTtl={
                "quotas": "10m",
                "spotScores": "10m",
                "prices": "1h",
            },
            apiVersions={
                "compute": azure_api.COMPUTE_API_VERSION,
                "spot": azure_api.SPOT_API_VERSION,
                "arm": azure_api.AZURE_API_VERSION,
            },
        ),
    )

    # 7. Warnings for missing data
    if not requirements.requiresSpotScore:
        warnings.append("Spot scores were not evaluated (not required by intent).")
    if not requirements.requiresPriceCheck:
        warnings.append("Pricing was not evaluated (not required by intent).")
    if missing_inputs:
        warnings.append(f"Missing data signals: {', '.join(missing_inputs)}.")

    return DeploymentPlanResponse(
        summary=summary,
        businessView=business_view,
        technicalView=technical_view,
        warnings=warnings,
        errors=errors,
    )
