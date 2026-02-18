"""Deployment Confidence Score – estimates the likelihood of successful VM deployment.

This module provides a **pure function** that computes a 0–100 score from
up to five independent signals.  Missing signals are excluded and the
remaining weights are renormalized so the score is always meaningful.

**Important wording constraint:** this score estimates *deployment success
probability*, never "capacity" or "capacity available".
"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "quota": 0.25,
    "spot": 0.35,
    "zones": 0.15,
    "restrictions": 0.15,
    "pricePressure": 0.10,
}

# ---------------------------------------------------------------------------
# Label thresholds
# ---------------------------------------------------------------------------
_LABEL_THRESHOLDS: list[tuple[int, str]] = [
    (80, "High"),
    (60, "Medium"),
    (40, "Low"),
    (0, "Very Low"),
]

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SignalBreakdown(TypedDict):
    signal: str
    score: float
    weight: float
    contribution: float


class ConfidenceResult(TypedDict):
    score: int
    label: str
    breakdown: list[SignalBreakdown]
    missing: list[str]


# ---------------------------------------------------------------------------
# Internal signal normalisations
# ---------------------------------------------------------------------------


def _quota_score(
    remaining: int | None,
    vcpus: int | None,
) -> float | None:
    """Score quota headroom.

    * remaining / vcpus gives the number of VMs that can be deployed.
    * 10+ VMs of headroom → full score (100).
    * 0 remaining → 0.
    """
    if remaining is None or vcpus is None:
        return None
    if remaining <= 0:
        return 0.0
    ratio = remaining / max(vcpus, 1)
    return min(ratio / 10.0, 1.0) * 100


def _spot_score(score_label: str | None) -> float | None:
    """Map a Spot Placement Score label to 0–100."""
    if score_label is None:
        return None
    mapping = {"high": 100.0, "medium": 60.0, "low": 25.0}
    return mapping.get(score_label.lower())


def _zone_score(zones_count: int | None) -> float | None:
    """Score based on AZ breadth (0–3 zones)."""
    if zones_count is None:
        return None
    return min(zones_count / 3.0, 1.0) * 100


def _restriction_score(restrictions_present: bool | None) -> float | None:
    """Binary: no restrictions → 100, any restrictions → 0."""
    if restrictions_present is None:
        return None
    return 0.0 if restrictions_present else 100.0


def _price_pressure_score(
    paygo: float | None,
    spot: float | None,
) -> float | None:
    """Score based on spot-to-paygo price ratio.

    A *low* ratio means cheap spot (low demand → high deployment
    confidence).  A ratio approaching 1.0 means high demand.

    * ratio ≤ 0.2 → 100
    * ratio ≥ 0.8 → 0
    * Linear interpolation in between.
    """
    if paygo is None or spot is None or paygo <= 0:
        return None
    ratio = spot / paygo
    return max(0.0, min(1.0, (0.8 - ratio) / 0.6)) * 100


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------


def compute_capacity_confidence(
    *,
    vcpus: int | None = None,
    zones_supported_count: int | None = None,
    restrictions_present: bool | None = None,
    quota_remaining_vcpu: int | None = None,
    spot_score_label: str | None = None,
    paygo_price: float | None = None,
    spot_price: float | None = None,
) -> ConfidenceResult:
    """Compute the Deployment Confidence Score for a single SKU.

    All parameters are optional.  When a signal cannot be computed
    (``None`` source data), it is excluded and remaining weights are
    renormalized.  If **no** signals are available, the score defaults
    to 0 with label "Very Low".
    """
    signals: dict[str, float | None] = {
        "quota": _quota_score(quota_remaining_vcpu, vcpus),
        "spot": _spot_score(spot_score_label),
        "zones": _zone_score(zones_supported_count),
        "restrictions": _restriction_score(restrictions_present),
        "pricePressure": _price_pressure_score(paygo_price, spot_price),
    }

    breakdown: list[SignalBreakdown] = []
    missing: list[str] = []
    total_weight = 0.0

    for signal_name, signal_value in signals.items():
        if signal_value is None:
            missing.append(signal_name)
        else:
            total_weight += WEIGHTS[signal_name]

    # Renormalize and compute weighted score
    weighted_sum = 0.0
    for signal_name, signal_value in signals.items():
        if signal_value is None:
            continue
        weight = WEIGHTS[signal_name]
        effective_weight = weight / total_weight if total_weight > 0 else 0.0
        contribution = signal_value * effective_weight
        weighted_sum += contribution
        breakdown.append(
            SignalBreakdown(
                signal=signal_name,
                score=round(signal_value, 1),
                weight=round(effective_weight, 3),
                contribution=round(contribution, 1),
            )
        )

    score = round(weighted_sum) if total_weight > 0 else 0
    label = "Very Low"
    for threshold, lbl in _LABEL_THRESHOLDS:
        if score >= threshold:
            label = lbl
            break

    return ConfidenceResult(
        score=score,
        label=label,
        breakdown=breakdown,
        missing=missing,
    )
