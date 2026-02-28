"""Volatility service – derived price/score volatility from historical signals.

Computes volatility metrics over a configurable window (24h or 7d) from
the time-series signal store.

**Important:** These metrics are *heuristic estimates* derived from samples
collected by this tool.  They are NOT Azure internal telemetry.
"""

from __future__ import annotations

import math
from typing import Literal, TypedDict

from az_scout.services.signals.signal_store import get_signals

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class VolatilityResult(TypedDict):
    spotScoreChangeRatePerDay: float | None
    timeInLowPercent: float | None
    priceVolatilityPercent: float | None
    label: Literal["stable", "moderate", "unstable", "unknown"]
    sampleCount: int
    windowHours: int


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MIN_SAMPLES = 3  # below this → "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_RANK: dict[str, int] = {"Low": 1, "Medium": 2, "High": 3}


def _score_changes_per_day(
    scores: list[str | None],
    timestamps: list[str],
    window_hours: int,
) -> float | None:
    """Count distinct score transitions normalised to per-day rate."""
    valid = [(s, t) for s, t in zip(scores, timestamps, strict=False) if s is not None]
    if len(valid) < 2:
        return None
    changes = sum(1 for i in range(1, len(valid)) if valid[i][0] != valid[i - 1][0])
    days = max(window_hours / 24.0, 1.0)
    return round(changes / days, 3)


def _time_in_low_percent(scores: list[str | None]) -> float | None:
    """Percentage of samples where spot_score == 'Low'."""
    valid = [s for s in scores if s is not None]
    if not valid:
        return None
    low_count = sum(1 for s in valid if s == "Low")
    return round(100.0 * low_count / len(valid), 1)


def _price_volatility_percent(prices: list[float | None]) -> float | None:
    """Coefficient of variation (std/mean × 100) for non-null prices."""
    valid = [p for p in prices if p is not None and p > 0]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    if mean == 0:
        return None
    variance = sum((p - mean) ** 2 for p in valid) / len(valid)
    std = math.sqrt(variance)
    return round(100.0 * std / mean, 2)


def _volatility_label(
    change_rate: float | None,
    time_low: float | None,
    price_vol: float | None,
) -> Literal["stable", "moderate", "unstable"]:
    """Assign a qualitative label based on the volatility signals."""
    # Unstable if any strong indicator fires
    if (
        (change_rate is not None and change_rate >= 2.0)
        or (time_low is not None and time_low >= 50.0)
        or (price_vol is not None and price_vol >= 30.0)
    ):
        return "unstable"

    if (
        (change_rate is not None and change_rate >= 0.5)
        or (time_low is not None and time_low >= 20.0)
        or (price_vol is not None and price_vol >= 10.0)
    ):
        return "moderate"

    return "stable"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_volatility(
    region: str,
    sku: str,
    window: Literal["24h", "7d"] = "24h",
) -> VolatilityResult:
    """Compute volatility metrics for a (region, sku) pair.

    Args:
        region: Azure region name.
        sku: ARM SKU name (e.g. ``Standard_D2s_v3``).
        window: Time window – ``"24h"`` (default) or ``"7d"``.

    Returns a ``VolatilityResult`` dict.  If fewer than ``_MIN_SAMPLES``
    samples are available, the label is ``"unknown"`` and all numeric
    fields are ``None``.
    """
    window_hours = 24 if window == "24h" else 168

    signals = get_signals(region, sku, hours=window_hours)
    sample_count = len(signals)

    if sample_count < _MIN_SAMPLES:
        return VolatilityResult(
            spotScoreChangeRatePerDay=None,
            timeInLowPercent=None,
            priceVolatilityPercent=None,
            label="unknown",
            sampleCount=sample_count,
            windowHours=window_hours,
        )

    spot_scores = [s["spot_score"] for s in signals]
    timestamps = [s["timestamp"] for s in signals]
    spot_prices = [s["spot_price"] for s in signals]

    change_rate = _score_changes_per_day(spot_scores, timestamps, window_hours)
    time_low = _time_in_low_percent(spot_scores)
    price_vol = _price_volatility_percent(spot_prices)
    label = _volatility_label(change_rate, time_low, price_vol)

    return VolatilityResult(
        spotScoreChangeRatePerDay=change_rate,
        timeInLowPercent=time_low,
        priceVolatilityPercent=price_vol,
        label=label,
        sampleCount=sample_count,
        windowHours=window_hours,
    )


def volatility_to_normalized(label: str) -> float | None:
    """Map a volatility label to a 0–1 normalized score.

    stable → 1.0, moderate → 0.65, unstable → 0.3, unknown → None
    """
    mapping: dict[str, float] = {"stable": 1.0, "moderate": 0.65, "unstable": 0.3}
    return mapping.get(label)
