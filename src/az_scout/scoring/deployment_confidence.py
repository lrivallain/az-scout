"""Canonical Deployment Confidence Score – single source of truth.

This module provides the **only** authorised computation of the Deployment
Confidence Score.  Both the web UI (via FastAPI endpoints) and the MCP
server **must** use ``compute_deployment_confidence`` – no client-side
recomputation is permitted.

Scoring rule (v1)
-----------------
Five independent signals are normalised to 0–1, weighted, and summed.
Missing signals are excluded and the remaining weights are renormalised so
the score stays meaningful.

Weights (sum = 1.0):
    quota          0.25     Quota headroom relative to vCPU demand
    spot           0.35     Spot Placement Score (Azure API)
    zones          0.15     Available (non-restricted) AZ breadth
    restrictions   0.15     Whether any subscription/zone restrictions exist
    pricePressure  0.10     Spot-to-PAYGO price ratio

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
# Version – bump when weights or normalisation rules change
# ---------------------------------------------------------------------------
SCORING_VERSION = "v1"

# ---------------------------------------------------------------------------
# Weights (must sum to 1.0)
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "quota": 0.25,
    "spot": 0.35,
    "zones": 0.15,
    "restrictions": 0.15,
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

    quota_remaining_vcpu: int | None = None
    vcpus: int | None = None
    spot_score_label: str | None = None
    zones_available_count: int | None = None
    restrictions_present: bool | None = None
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
    scoringVersion: str
    cacheTtlSeconds: int | None = None


class DeploymentConfidenceResult(BaseModel):
    """Canonical result returned by ``compute_deployment_confidence``."""

    score: int
    label: str
    breakdown: BreakdownDetail
    missingSignals: list[str]
    disclaimers: list[str]
    provenance: Provenance
    scoringVersion: str


# ===================================================================
# Signal normalisation helpers (each returns 0..1 or None)
# ===================================================================


def _normalize_quota(remaining: int | None, vcpus: int | None) -> float | None:
    """Quota headroom: remaining_vcpus / vcpus_per_vm, capped at 10 VMs."""
    if remaining is None or vcpus is None:
        return None
    if remaining <= 0:
        return 0.0
    return min(remaining / max(vcpus, 1) / 10.0, 1.0)


def _normalize_spot(label: str | None) -> float | None:
    """Map a Spot Placement Score label to 0–1."""
    if label is None:
        return None
    mapping: dict[str, float] = {"high": 1.0, "medium": 0.6, "low": 0.25}
    return mapping.get(label.lower())


def _normalize_zones(zones_available_count: int | None) -> float | None:
    """AZ breadth: available zones / 3."""
    if zones_available_count is None:
        return None
    return min(zones_available_count / 3.0, 1.0)


def _normalize_restrictions(restrictions_present: bool | None) -> float | None:
    """Binary: no restrictions → 1.0, any restriction → 0.0."""
    if restrictions_present is None:
        return None
    return 0.0 if restrictions_present else 1.0


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


# Mapping: signal name → (normalizer callable, missing-reason text)
_NORMALIZERS: dict[str, tuple[str, str]] = {
    "quota": ("_do_quota", "quota_remaining_vcpu or vcpus not provided"),
    "spot": ("_do_spot", "spot_score_label not provided"),
    "zones": ("_do_zones", "zones_available_count not provided"),
    "restrictions": ("_do_restrictions", "restrictions_present not provided"),
    "pricePressure": ("_do_price", "paygo_price or spot_price not provided"),
}


def _compute_normalized(
    signals: DeploymentSignals,
) -> dict[str, tuple[float | None, str]]:
    """Return ``{name: (normalised_value_or_None, missing_reason)}``."""
    return {
        "quota": (
            _normalize_quota(signals.quota_remaining_vcpu, signals.vcpus),
            "quota_remaining_vcpu or vcpus not provided",
        ),
        "spot": (
            _normalize_spot(signals.spot_score_label),
            "spot_score_label not provided",
        ),
        "zones": (
            _normalize_zones(signals.zones_available_count),
            "zones_available_count not provided",
        ),
        "restrictions": (
            _normalize_restrictions(signals.restrictions_present),
            "restrictions_present not provided",
        ),
        "pricePressure": (
            _normalize_price_pressure(signals.paygo_price, signals.spot_price),
            "paygo_price or spot_price not provided",
        ),
    }


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

    # ----- too few signals → Unknown ---------------------------------
    if signals_available < MIN_SIGNALS:
        all_components = _build_all_missing_components(normalized)
        return _make_result(
            score=0,
            label="Unknown",
            components=all_components,
            weights_used_sum=0.0,
            renormalized=False,
            missing_signals=missing_signals,
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

    # ----- label mapping ----------------------------------------------
    label = "Very Low"
    for threshold, lbl in LABEL_THRESHOLDS:
        if score >= threshold:
            label = lbl
            break

    return _make_result(
        score=score,
        label=label,
        components=components,
        weights_used_sum=round(used_weights_sum, 4),
        renormalized=renormalized,
        missing_signals=missing_signals,
    )


# ===================================================================
# Helpers for building SKU signals from raw API data
# ===================================================================


def best_spot_label(zone_scores: dict[str, str]) -> str | None:
    """Pick the best Spot Placement Score label from per-zone data.

    Returns ``None`` if *zone_scores* is empty.
    """
    if not zone_scores:
        return None
    rank = {"high": 3, "medium": 2, "low": 1}
    best: str | None = None
    for label in zone_scores.values():
        if rank.get(label.lower(), 0) > rank.get((best or "").lower(), 0):
            best = label
    return best


def signals_from_sku(
    sku: dict[str, Any],
    *,
    spot_score_label: str | None = None,
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
        quota_remaining_vcpu=quota.get("remaining"),
        vcpus=vcpus,
        spot_score_label=spot_score_label,
        zones_available_count=len(available_zones),
        restrictions_present=len(restrictions) > 0 if restrictions is not None else None,
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
    components: list[ComponentBreakdown],
    weights_used_sum: float,
    renormalized: bool,
    missing_signals: list[str],
) -> DeploymentConfidenceResult:
    return DeploymentConfidenceResult(
        score=score,
        label=label,
        breakdown=BreakdownDetail(
            components=components,
            weightsOriginal=dict(WEIGHTS),
            weightsUsedSum=weights_used_sum,
            renormalized=renormalized,
        ),
        missingSignals=missing_signals,
        disclaimers=list(DISCLAIMERS),
        provenance=Provenance(
            computedAtUtc=datetime.datetime.now(datetime.UTC).isoformat(),
            scoringVersion=SCORING_VERSION,
        ),
        scoringVersion=SCORING_VERSION,
    )
