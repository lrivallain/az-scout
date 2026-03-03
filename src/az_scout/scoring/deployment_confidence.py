"""Canonical Deployment Confidence Score – single source of truth.

This module provides the **only** authorised computation of the Deployment
Confidence Score.  Both the web UI (via FastAPI endpoints) and the MCP
server **must** use ``compute_deployment_confidence`` – no client-side
recomputation is permitted.

Score types
-----------
**Basic** (default):  Four signals – quotaPressure, zones,
restrictionDensity, pricePressure.  Spot Placement is *excluded*
(``spot_score_label=None``) and the remaining weights are renormalised.
This is what appears in the SKU table listing and the default modal view.

**Basic + Spot**:  All five signals – quotaPressure, spot, zones,
restrictionDensity, pricePressure.  Activated when the caller supplies a
``spot_score_label`` to ``signals_from_sku`` (typically after fetching
Spot Placement Scores with a specific instance count).

Scoring rule
------------
Five independent signals are normalised to 0–1, weighted, and summed.
Missing signals are excluded and the remaining weights are renormalised so
the score stays meaningful.

Weights (sum = 1.0):
    quotaPressure      0.25   Demand-adjusted, non-linear quota pressure (Protean-inspired)
    spot               0.35   Spot Placement Score (Azure API)
    zones              0.15   Available (non-restricted) AZ breadth
    restrictionDensity 0.15   Fraction of zones *not* restricted
    pricePressure      0.10   Spot-to-PAYGO price ratio

Label mapping:
    >=80  High
    >=60  Medium
    >=40  Low
    < 40  Very Low
    (fewer than MIN_SIGNALS available → Unknown, score = 0)

Renormalisation:
    If N signals are missing, weight_effective_i = weight_i / Σ(available weights).

Rounding:
    ``round(score_01 * 100)`` → int 0..100.

**IMPORTANT**: This score is a *heuristic estimate*.  No deployment
outcome is guaranteed.
"""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Weights (must sum to 1.0)
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "quotaPressure": 0.25,
    "spot": 0.35,
    "zones": 0.15,
    "restrictionDensity": 0.15,
    "pricePressure": 0.10,
}

# ---------------------------------------------------------------------------
# Label thresholds (checked top-down, first match wins)
# ---------------------------------------------------------------------------
LABEL_THRESHOLDS: list[tuple[int, str]] = [
    (80, "High"),
    (60, "Medium"),
    (40, "Low"),
    (0, "Very Low"),
]

# Minimum number of available signals before we return a score.
# Below this threshold the result is ``label="Unknown", score=0``.
MIN_SIGNALS = 2

# ---------------------------------------------------------------------------
# Disclaimers (always included in every result)
# ---------------------------------------------------------------------------
DISCLAIMERS: list[str] = [
    "This is a heuristic estimate, not a guarantee of deployment success.",
    "Signals are derived from Azure APIs and may change at any time.",
    "No Microsoft guarantee is expressed or implied.",
]


# ===================================================================
# Pydantic models
# ===================================================================


class DeploymentSignals(BaseModel):
    """All possible input signals for confidence scoring.

    Every field is optional.  When ``None``, the corresponding signal is
    treated as missing and excluded from the score (with renormalisation).
    """

    # Quota pressure (v3) – replaces old linear quota headroom
    quota_used_vcpu: int | None = None
    quota_limit_vcpu: int | None = None
    quota_remaining_vcpu: int | None = None
    vcpus: int | None = None
    instance_count: int = 1

    # Spot
    spot_score_label: str | None = None

    # Zones
    zones_available_count: int | None = None
    zones_total_count: int | None = None

    # Restriction density (v3) – replaces old binary restrictions
    restricted_zones_count: int | None = None

    # Pricing
    paygo_price: float | None = None
    spot_price: float | None = None


class ComponentBreakdown(BaseModel):
    """Per-signal breakdown entry."""

    name: str
    score01: float
    score100: float
    weight: float
    contribution: float
    status: str  # "used" | "missing"
    reasonIfMissing: str | None = None


class BreakdownDetail(BaseModel):
    """Full breakdown payload."""

    components: list[ComponentBreakdown]
    weightsOriginal: dict[str, float]
    weightsUsedSum: float
    renormalized: bool


class Provenance(BaseModel):
    """Traceability metadata."""

    computedAtUtc: str
    cacheTtlSeconds: int | None = None


class DeploymentConfidenceResult(BaseModel):
    """Canonical result returned by ``compute_deployment_confidence``."""

    score: int
    label: str
    scoreType: str  # "basic" | "basic+spot" | "blocked"
    breakdown: BreakdownDetail
    missingSignals: list[str]
    knockoutReasons: list[str]
    disclaimers: list[str]
    provenance: Provenance


# ===================================================================
# Signal normalisation helpers (each returns 0..1 or None)
# ===================================================================


def _normalize_quota_pressure(
    used: int | None,
    limit: int | None,
    remaining: int | None,
    vcpus: int | None,
    instance_count: int = 1,
) -> float | None:
    """Demand-adjusted, non-linear quota usage pressure (Protean-inspired).

    Combines two perspectives:
    1. **Hard headroom** – can the requested fleet fit?
    2. **Projected usage band** – non-linear penalty based on utilisation
       *after* accounting for the requested deployment.

    The ``instance_count`` parameter makes the score demand-aware: deploying
    10×16-vCPU VMs into a 100-vCPU quota is far more critical than deploying 1.
    When ``instance_count`` is 1 (the default), behaviour is identical to pure
    supply-side pressure.

    Bands (projected_usage = (used + vcpus × instance_count) / limit):
        projected < 60%  → 1.0  (healthy)
        projected < 80%  → 0.7  (moderate pressure)
        projected < 95%  → 0.3  (danger zone)
        projected ≥ 95%  → 0.1  (critical)
        remaining < fleet → 0.0  (hard failure, regardless of band)
    """
    if remaining is None or vcpus is None:
        return None
    fleet_vcpus = max(vcpus, 1) * max(instance_count, 1)
    # Hard failure: cannot fit the requested fleet
    if remaining < fleet_vcpus:
        return 0.0
    # Need used & limit for pressure bands
    if used is None or limit is None or limit <= 0:
        # Fall back to simple headroom when usage data is missing
        return min(remaining / fleet_vcpus / 10.0, 1.0)
    projected_usage = (used + fleet_vcpus) / limit
    if projected_usage < 0.60:
        return 1.0
    if projected_usage < 0.80:
        return 0.7
    if projected_usage < 0.95:
        return 0.3
    return 0.1


# Labels from the Azure Spot Placement Scores API that mean
# "spot is definitively unavailable" — score them as 0.0, not missing.
_SPOT_UNAVAILABLE_LABELS: frozenset[str] = frozenset(
    {
        "restrictedskunotavailable",
        "restricted",
    }
)


def _normalize_spot(label: str | None) -> float | None:
    """Map a Spot Placement Score label to 0–1.

    Restricted/unavailable labels are scored as 0.0 (definitively bad)
    rather than ``None`` (missing), so they contribute to the weighted
    sum instead of being silently excluded.
    """
    if label is None:
        return None
    key = label.lower()
    mapping: dict[str, float] = {"high": 1.0, "medium": 0.6, "low": 0.25}
    value = mapping.get(key)
    if value is not None:
        return value
    if key in _SPOT_UNAVAILABLE_LABELS:
        return 0.0
    # Genuinely unknown / no data → treat as missing
    return None


def _normalize_zones(zones_available_count: int | None) -> float | None:
    """AZ breadth: available zones / 3."""
    if zones_available_count is None:
        return None
    return min(zones_available_count / 3.0, 1.0)


def _normalize_restriction_density(
    restricted_count: int | None,
    total_count: int | None,
) -> float | None:
    """Fraction of zones that are *not* restricted.

    Replaces the old binary signal: a SKU restricted in 1 of 3 zones now
    scores 0.67 instead of 0.0.

    Returns:
        1.0 – no restrictions
        0.67 – 1 of 3 zones restricted
        0.33 – 2 of 3 zones restricted
        0.0 – all zones restricted
        None – data unavailable
    """
    if restricted_count is None or total_count is None:
        return None
    if total_count <= 0:
        return 0.0
    return max(0.0, 1.0 - restricted_count / total_count)


def _normalize_price_pressure(
    paygo: float | None,
    spot: float | None,
) -> float | None:
    """Spot-to-PAYGO price ratio.

    Low ratio (cheap spot) → high score.  Ratio ≤ 0.2 → 1.0, ≥ 0.8 → 0.0.
    """
    if paygo is None or spot is None or paygo <= 0:
        return None
    ratio = spot / paygo
    return max(0.0, min(1.0, (0.8 - ratio) / 0.6))


def _compute_normalized(
    signals: DeploymentSignals,
) -> dict[str, tuple[float | None, str]]:
    """Return ``{name: (normalised_value_or_None, missing_reason)}``."""
    return {
        "quotaPressure": (
            _normalize_quota_pressure(
                signals.quota_used_vcpu,
                signals.quota_limit_vcpu,
                signals.quota_remaining_vcpu,
                signals.vcpus,
                signals.instance_count,
            ),
            "quota data or vcpus not provided",
        ),
        "spot": (
            _normalize_spot(signals.spot_score_label),
            "spot_score_label not provided",
        ),
        "zones": (
            _normalize_zones(signals.zones_available_count),
            "zones_available_count not provided",
        ),
        "restrictionDensity": (
            _normalize_restriction_density(
                signals.restricted_zones_count,
                signals.zones_total_count,
            ),
            "restricted_zones_count or zones_total_count not provided",
        ),
        "pricePressure": (
            _normalize_price_pressure(signals.paygo_price, signals.spot_price),
            "paygo_price or spot_price not provided",
        ),
    }


# ===================================================================
# Knockout checks — hard blockers that force score to 0
# ===================================================================


def _check_knockouts(signals: DeploymentSignals) -> list[str]:
    """Return a list of knockout reasons (empty if deployment is feasible).

    Knockout conditions represent **impossible** deployments — situations
    where the Azure ARM API would deterministically reject the request.
    Unlike low signal scores (which reduce confidence), knockouts force
    the overall score to 0 with label ``Blocked``.

    Current knockouts:
    - Quota exhausted: ``remaining < vcpus × instance_count``
    - No zones available: ``zones_available_count == 0``
    """
    reasons: list[str] = []
    # Quota knockout: fleet cannot fit
    if signals.quota_remaining_vcpu is not None and signals.vcpus is not None and signals.vcpus > 0:
        fleet = signals.vcpus * max(signals.instance_count, 1)
        if signals.quota_remaining_vcpu < fleet:
            reasons.append(
                f"Insufficient quota: {signals.quota_remaining_vcpu} vCPUs remaining, "
                f"{fleet} required ({signals.vcpus} × {max(signals.instance_count, 1)})"
            )
    # Zone knockout: no available zone
    if signals.zones_available_count is not None and signals.zones_available_count == 0:
        reasons.append("No availability zones available (all zones restricted or SKU not offered)")
    return reasons


# ===================================================================
# Main function
# ===================================================================


def compute_deployment_confidence(
    signals: DeploymentSignals,
) -> DeploymentConfidenceResult:
    """Compute the canonical Deployment Confidence Score.

    Parameters
    ----------
    signals:
        Input signals.  Missing fields (``None``) are excluded and
        weights are renormalised.

    Returns
    -------
    DeploymentConfidenceResult
        Deterministic result (same inputs → same outputs, except for
        ``provenance.computedAtUtc``).
    """
    normalized = _compute_normalized(signals)

    # ----- knockout gate: hard blockers → score 0, label Blocked ------
    knockout_reasons = _check_knockouts(signals)

    # ----- identify used vs missing -----------------------------------
    missing_signals: list[str] = []
    used_weights_sum = 0.0

    for name, (norm_value, _reason) in normalized.items():
        if norm_value is None:
            missing_signals.append(name)
        else:
            used_weights_sum += WEIGHTS[name]

    signals_available = len(WEIGHTS) - len(missing_signals)
    renormalized = len(missing_signals) > 0 and signals_available > 0

    # ----- determine score type ---------------------------------------
    has_spot = "spot" not in missing_signals
    score_type: str
    if knockout_reasons:
        score_type = "blocked"
    elif has_spot:
        score_type = "basic+spot"
    else:
        score_type = "basic"

    # ----- too few signals → Unknown ---------------------------------
    if signals_available < MIN_SIGNALS:
        all_components = _build_all_missing_components(normalized)
        return _make_result(
            score=0,
            label="Blocked" if knockout_reasons else "Unknown",
            score_type=score_type,
            components=all_components,
            weights_used_sum=0.0,
            renormalized=False,
            missing_signals=missing_signals,
            knockout_reasons=knockout_reasons,
        )

    # ----- weighted sum with renormalisation --------------------------
    weighted_sum = 0.0
    components: list[ComponentBreakdown] = []

    for name, (norm_value, reason) in normalized.items():
        if norm_value is None:
            components.append(
                ComponentBreakdown(
                    name=name,
                    score01=0.0,
                    score100=0.0,
                    weight=0.0,
                    contribution=0.0,
                    status="missing",
                    reasonIfMissing=reason,
                )
            )
            continue

        eff_weight = WEIGHTS[name] / used_weights_sum if used_weights_sum > 0 else 0.0
        contribution = norm_value * eff_weight
        weighted_sum += contribution

        components.append(
            ComponentBreakdown(
                name=name,
                score01=round(norm_value, 4),
                score100=round(norm_value * 100, 1),
                weight=round(eff_weight, 4),
                contribution=round(contribution, 4),
                status="used",
            )
        )

    score = round(weighted_sum * 100)

    # ----- knockout override ------------------------------------------
    if knockout_reasons:
        return _make_result(
            score=0,
            label="Blocked",
            score_type=score_type,
            components=components,
            weights_used_sum=round(used_weights_sum, 4),
            renormalized=renormalized,
            missing_signals=missing_signals,
            knockout_reasons=knockout_reasons,
        )

    # ----- label mapping ----------------------------------------------
    label = "Very Low"
    for threshold, lbl in LABEL_THRESHOLDS:
        if score >= threshold:
            label = lbl
            break

    return _make_result(
        score=score,
        label=label,
        score_type=score_type,
        components=components,
        weights_used_sum=round(used_weights_sum, 4),
        renormalized=renormalized,
        missing_signals=missing_signals,
        knockout_reasons=knockout_reasons,
    )


# ===================================================================
# Helpers for building SKU signals from raw API data
# ===================================================================


def best_spot_label(zone_scores: dict[str, str]) -> str | None:
    """Pick the best Spot Placement Score label from per-zone data.

    Returns ``None`` only if *zone_scores* is empty.  When all zones
    report non-scorable values (e.g. ``RestrictedSkuNotAvailable``),
    the first such value is returned so the caller can still include
    Spot as a 0-score signal rather than silently excluding it.
    """
    if not zone_scores:
        return None
    rank = {"high": 3, "medium": 2, "low": 1}
    best: str | None = None
    for label in zone_scores.values():
        if rank.get(label.lower(), 0) > rank.get((best or "").lower(), 0):
            best = label
    # If no High/Medium/Low found but zones had data, return the first
    # label (e.g. "RestrictedSkuNotAvailable") so _normalize_spot can
    # map it to 0.0 instead of treating spot as entirely missing.
    if best is None and zone_scores:
        best = next(iter(zone_scores.values()))
    return best


def signals_from_sku(
    sku: dict[str, Any],
    *,
    spot_score_label: str | None = None,
    instance_count: int = 1,
) -> DeploymentSignals:
    """Build ``DeploymentSignals`` from a raw SKU dict (as returned by ``azure_api``)."""
    caps = sku.get("capabilities", {})
    quota = sku.get("quota", {})
    pricing = sku.get("pricing", {})
    zones: list[str] = sku.get("zones", [])
    restrictions: list[str] = sku.get("restrictions", [])

    try:
        vcpus: int | None = int(caps.get("vCPUs", 0))
    except (TypeError, ValueError):
        vcpus = None

    available_zones = [z for z in zones if z not in restrictions]

    return DeploymentSignals(
        quota_used_vcpu=quota.get("used"),
        quota_limit_vcpu=quota.get("limit"),
        quota_remaining_vcpu=quota.get("remaining"),
        vcpus=vcpus,
        instance_count=instance_count,
        spot_score_label=spot_score_label,
        zones_available_count=len(available_zones),
        zones_total_count=len(zones),
        restricted_zones_count=len(restrictions),
        paygo_price=pricing.get("paygo") if pricing else None,
        spot_price=pricing.get("spot") if pricing else None,
    )


# ===================================================================
# Private helpers
# ===================================================================


def _build_all_missing_components(
    normalized: dict[str, tuple[float | None, str]],
) -> list[ComponentBreakdown]:
    """Create component entries when all/most signals are missing."""
    components: list[ComponentBreakdown] = []
    for name, (norm_value, reason) in normalized.items():
        if norm_value is None:
            components.append(
                ComponentBreakdown(
                    name=name,
                    score01=0.0,
                    score100=0.0,
                    weight=0.0,
                    contribution=0.0,
                    status="missing",
                    reasonIfMissing=reason,
                )
            )
        else:
            components.append(
                ComponentBreakdown(
                    name=name,
                    score01=round(norm_value, 4),
                    score100=round(norm_value * 100, 1),
                    weight=0.0,
                    contribution=0.0,
                    status="used",
                    reasonIfMissing="insufficient signals for scoring",
                )
            )
    return components


def _make_result(
    *,
    score: int,
    label: str,
    score_type: str,
    components: list[ComponentBreakdown],
    weights_used_sum: float,
    renormalized: bool,
    missing_signals: list[str],
    knockout_reasons: list[str] | None = None,
) -> DeploymentConfidenceResult:
    return DeploymentConfidenceResult(
        score=score,
        label=label,
        scoreType=score_type,
        breakdown=BreakdownDetail(
            components=components,
            weightsOriginal=dict(WEIGHTS),
            weightsUsedSum=weights_used_sum,
            renormalized=renormalized,
        ),
        missingSignals=missing_signals,
        knockoutReasons=knockout_reasons or [],
        disclaimers=list(DISCLAIMERS),
        provenance=Provenance(
            computedAtUtc=datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )
