"""Spot eviction rate service – queries Azure Resource Graph.

Queries the ``SpotResources`` table in Azure Resource Graph (ARG) to
obtain the eviction rate for a given (region, SKU) pair, then maps
it to a 0–1 normalized score.

**Important:** The eviction rate is a *probabilistic signal* provided
by Azure.  The normalized score is a *derived heuristic*.
No deployment outcome is guaranteed.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, TypedDict

import requests

from az_scout.azure_api import AZURE_MGMT_URL, _get_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class EvictionRateResult(TypedDict):
    evictionRate: str | None
    normalizedScore: float | None
    status: Literal["available", "missing", "error"]
    disclaimer: str


# ---------------------------------------------------------------------------
# Eviction rate band mapping
# ---------------------------------------------------------------------------

_EVICTION_BANDS: list[tuple[int, float]] = [
    (5, 1.0),  # 0-5%   → 1.0
    (10, 0.8),  # 5-10%  → 0.8
    (15, 0.6),  # 10-15% → 0.6
    (20, 0.4),  # 15-20% → 0.4
]
_EVICTION_HIGH = 0.2  # 20%+ → 0.2

_DISCLAIMER = (
    "Eviction rate is a probabilistic Azure signal. "
    "The normalized score is a derived heuristic estimate. "
    "No deployment outcome is guaranteed."
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_EVICTION_CACHE_TTL = 1200  # 20 minutes
_eviction_cache: dict[str, tuple[float, EvictionRateResult]] = {}


def clear_eviction_cache() -> None:
    """Clear the eviction rate cache (for testing)."""
    _eviction_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_eviction_rate(rate_str: str | None) -> float | None:
    """Map an eviction rate string from ARG to a 0–1 score.

    Expected formats: "0-5", "5-10", "10-15", "15-20", "20+"
    or percentage strings like "0-5%".
    """
    if rate_str is None:
        return None

    cleaned = rate_str.strip().rstrip("%").strip()

    # Try range format "X-Y" or "X+"
    if "+" in cleaned:
        return _EVICTION_HIGH

    if "-" in cleaned:
        parts = cleaned.split("-")
        try:
            upper = int(parts[1])
        except (IndexError, ValueError):
            return None
        for threshold, score in _EVICTION_BANDS:
            if upper <= threshold:
                return score
        return _EVICTION_HIGH

    # Try as a plain number
    try:
        pct = float(cleaned)
    except ValueError:
        return None

    for threshold, score in _EVICTION_BANDS:
        if pct <= threshold:
            return score
    return _EVICTION_HIGH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_ARG_API_VERSION = "2021-03-01"


def get_spot_eviction_rate(
    region: str,
    sku: str,
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> EvictionRateResult:
    """Query ARG for the spot eviction rate of a (region, SKU) pair.

    Falls back gracefully when ARG is unavailable or returns no data.
    Results are cached for ``_EVICTION_CACHE_TTL`` seconds.
    """
    cache_key = f"{region}:{sku}:{tenant_id or ''}"
    now = time.monotonic()
    cached = _eviction_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _EVICTION_CACHE_TTL:
            return data

    query = (
        "SpotResources\n"
        "| where type =~ 'microsoft.compute/skuspotevictionrate/location'\n"
        f"| where sku.name == '{sku}'\n"
        f"| where location == '{region}'\n"
        "| project evictionRate = properties.evictionRate"
    )

    try:
        headers = _get_headers(tenant_id)
        url = (
            f"{AZURE_MGMT_URL}/providers/Microsoft.ResourceGraph"
            f"/resources?api-version={_ARG_API_VERSION}"
        )

        body: dict = {"query": query}
        if subscription_id:
            body["subscriptions"] = [subscription_id]

        resp = None
        for attempt in range(3):
            resp = requests.post(url, headers=headers, json=body, timeout=30)
            if resp.status_code == 429:
                retry_after_raw = resp.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after_raw) if retry_after_raw else 2**attempt
                except (TypeError, ValueError):
                    retry_after = 2**attempt
                logger.warning(
                    "ARG eviction rate 429, retrying in %ds (attempt %d/3)",
                    retry_after,
                    attempt + 1,
                )
                time.sleep(retry_after)
                continue
            if resp.status_code == 403:
                logger.warning("ARG eviction rate: access denied (403)")
                result = EvictionRateResult(
                    evictionRate=None,
                    normalizedScore=None,
                    status="missing",
                    disclaimer=_DISCLAIMER,
                )
                _eviction_cache[cache_key] = (time.monotonic(), result)
                return result
            resp.raise_for_status()
            break

        if resp is None or resp.status_code >= 400:
            result = EvictionRateResult(
                evictionRate=None,
                normalizedScore=None,
                status="error",
                disclaimer=_DISCLAIMER,
            )
            _eviction_cache[cache_key] = (time.monotonic(), result)
            return result

        resp_data: dict[str, Any] = resp.json()
        rows = resp_data.get("data", {}).get("rows", [])
        if not rows:
            result = EvictionRateResult(
                evictionRate=None,
                normalizedScore=None,
                status="missing",
                disclaimer=_DISCLAIMER,
            )
            _eviction_cache[cache_key] = (time.monotonic(), result)
            return result

        rate_str = str(rows[0][0]) if rows[0] else None
        normalized = _map_eviction_rate(rate_str)

        result = EvictionRateResult(
            evictionRate=rate_str,
            normalizedScore=normalized,
            status="available" if normalized is not None else "missing",
            disclaimer=_DISCLAIMER,
        )
        _eviction_cache[cache_key] = (time.monotonic(), result)
        return result

    except Exception as exc:
        logger.warning("ARG eviction rate query failed: %s", exc)
        result = EvictionRateResult(
            evictionRate=None,
            normalizedScore=None,
            status="error",
            disclaimer=_DISCLAIMER,
        )
        _eviction_cache[cache_key] = (time.monotonic(), result)
        return result
