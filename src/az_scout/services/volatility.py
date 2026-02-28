"""Backward-compatible re-export – canonical module is ``signals.volatility``."""

from az_scout.services.signals.volatility import (  # noqa: F401
    VolatilityResult,
    _price_volatility_percent,
    _score_changes_per_day,
    _time_in_low_percent,
    compute_volatility,
    volatility_to_normalized,
)
