"""Admission Confidence Score – composite heuristic estimating VM deployment success.

Inspired by the principles described in:
- **Protean** – VM Allocation Service (Microsoft Research, 2020)
- **Kerveros** – Cloud Admission Control (Microsoft Research, 2023)

The score synthesises up to six independent signals into a single 0–100
value with automatic weight renormalisation when signals are missing.

**IMPORTANT:** This score is a *heuristic estimate*.  Signals are derived
and probabilistic.  No deployment outcome is guaranteed.  This is NOT
internal Azure capacity data.
"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------

ADMISSION_WEIGHTS: dict[str, float] = {
    "SPS": 0.25,
    "ER": 0.20,
    "VOL": 0.15,
    "FRAG": 0.20,
    "QUOTA": 0.10,
    "POLICY": 0.10,
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

_MIN_SIGNALS = 3  # below this → "Unknown"

# ---------------------------------------------------------------------------
# Disclaimers
# ---------------------------------------------------------------------------

DISCLAIMERS: list[str] = [
    "This is a heuristic estimate, not a guarantee of deployment success.",
    "Spot placement score and eviction rate are probabilistic Azure signals.",
    "Historical volatility is based on samples collected by this tool, not Azure internal data.",
    "Fragmentation risk is a derived heuristic based on observable SKU characteristics.",
    "No Microsoft guarantee is expressed or implied.",
]

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SignalBreakdown(TypedDict):
    signal: str
    rawValue: str | float | None
    normalizedScore: float
    weight: float
    contribution: float


class AdmissionConfidenceResult(TypedDict):
    score: int
    label: str
    breakdown: list[SignalBreakdown]
    missingInputs: list[str]
    signalsAvailable: int
    disclaimers: list[str]


# ---------------------------------------------------------------------------
# Signal normalisation helpers
# ---------------------------------------------------------------------------


def _normalize_sps(spot_score_label: str | None) -> float | None:
    """Normalize Spot Placement Score to 0–1."""
    if spot_score_label is None:
        return None
    mapping = {"high": 1.0, "medium": 0.6, "low": 0.25}
    return mapping.get(spot_score_label.lower())


def _normalize_quota(remaining: int | None, vcpus: int | None) -> float | None:
    """Normalize quota headroom to 0–1."""
    if remaining is None or vcpus is None:
        return None
    if remaining <= 0:
        return 0.0
    ratio = remaining / max(vcpus, 1)
    return min(ratio / 10.0, 1.0)


def _normalize_policy(
    zones_count: int | None,
    restrictions_present: bool | None,
) -> float | None:
    """Normalize policy signal (zones + restrictions) to 0–1."""
    if zones_count is None and restrictions_present is None:
        return None
    score = 0.0
    count = 0
    if zones_count is not None:
        score += min(zones_count / 3.0, 1.0)
        count += 1
    if restrictions_present is not None:
        score += 0.0 if restrictions_present else 1.0
        count += 1
    return score / count if count > 0 else None


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------


def compute_admission_confidence(
    *,
    # SPS
    spot_score_label: str | None = None,
    # ER (already normalized 0–1 from eviction_rate service)
    eviction_rate_normalized: float | None = None,
    # VOL (already normalized 0–1 from volatility service)
    volatility_normalized: float | None = None,
    # FRAG (already normalized 0–1 from fragmentation service)
    fragmentation_normalized: float | None = None,
    # QUOTA inputs
    quota_remaining_vcpu: int | None = None,
    vcpus: int | None = None,
    # POLICY inputs
    zones_supported_count: int | None = None,
    restrictions_present: bool | None = None,
) -> AdmissionConfidenceResult:
    """Compute the Admission Confidence Score for a single SKU.

    All parameters are optional.  Missing signals are excluded and
    remaining weights are renormalised.  If fewer than ``_MIN_SIGNALS``
    signals are available the label is ``"Unknown"``.

    Returns an ``AdmissionConfidenceResult`` dict with score, label,
    breakdown, missing inputs, and disclaimers.
    """
    # Build signal map
    signals: dict[str, tuple[float | None, str | float | None]] = {
        "SPS": (_normalize_sps(spot_score_label), spot_score_label),
        "ER": (eviction_rate_normalized, eviction_rate_normalized),
        "VOL": (volatility_normalized, volatility_normalized),
        "FRAG": (fragmentation_normalized, fragmentation_normalized),
        "QUOTA": (_normalize_quota(quota_remaining_vcpu, vcpus), quota_remaining_vcpu),
        "POLICY": (
            _normalize_policy(zones_supported_count, restrictions_present),
            f"zones={zones_supported_count}, restricted={restrictions_present}",
        ),
    }

    breakdown: list[SignalBreakdown] = []
    missing: list[str] = []
    total_weight = 0.0

    for name, (norm, _raw) in signals.items():
        if norm is None:
            missing.append(name)
        else:
            total_weight += ADMISSION_WEIGHTS[name]

    signals_available = len(signals) - len(missing)

    # Insufficient signals → Unknown
    if signals_available < _MIN_SIGNALS:
        return AdmissionConfidenceResult(
            score=0,
            label="Unknown",
            breakdown=[],
            missingInputs=missing,
            signalsAvailable=signals_available,
            disclaimers=DISCLAIMERS,
        )

    # Weighted sum with renormalisation
    weighted_sum = 0.0
    for name, (norm, raw) in signals.items():
        if norm is None:
            continue
        weight = ADMISSION_WEIGHTS[name]
        effective_weight = weight / total_weight if total_weight > 0 else 0.0
        contribution = norm * effective_weight
        weighted_sum += contribution
        breakdown.append(
            SignalBreakdown(
                signal=name,
                rawValue=raw,
                normalizedScore=round(norm, 3),
                weight=round(effective_weight, 3),
                contribution=round(contribution, 4),
            )
        )

    score = round(100 * weighted_sum) if total_weight > 0 else 0

    # Label
    label = "Very Low"
    for threshold, lbl in _LABEL_THRESHOLDS:
        if score >= threshold:
            label = lbl
            break

    return AdmissionConfidenceResult(
        score=score,
        label=label,
        breakdown=breakdown,
        missingInputs=missing,
        signalsAvailable=signals_available,
        disclaimers=DISCLAIMERS,
    )
