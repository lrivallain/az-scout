"""Public-signals services – pricing, latency (no Azure credential needed).

All data comes from unauthenticated APIs or static datasets embedded in
az-scout.  Results are cached with appropriate TTLs.
"""

import datetime
import logging
from typing import Any

from az_scout import azure_api
from az_scout.services.region_latency import get_rtt_ms, list_known_pairs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Common constants
# ---------------------------------------------------------------------------

_DISCLAIMERS: list[str] = [
    "This tool is not affiliated with Microsoft.",
    "All pricing and capacity information are indicative and not a guarantee.",
    "Retail prices are published list prices and may differ from negotiated rates.",
    "Spot pricing is volatile and allocations are not guaranteed.",
    "Latency values are indicative and based on Microsoft published statistics.",
    "No subscription, quota, or policy signals are available in public mode.",
]

_MISSING_SIGNALS: list[str] = [
    "subscription",
    "quota",
    "policy",
    "spot_placement_score",
    "eviction_rate",
    "fragmentation",
]


# ---------------------------------------------------------------------------
# Pricing (Azure Retail Prices API – unauthenticated)
# ---------------------------------------------------------------------------


def get_public_pricing(
    sku_name: str | None = None,
    region: str | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Return retail VM pricing from the Azure Retail Prices API.

    Optionally filter by *sku_name* (case-insensitive substring match).
    If *region* is provided, results are scoped to that region.
    """
    if not region:
        return {
            "mode": "public",
            "error": "region is required",
            "disclaimers": _DISCLAIMERS,
        }

    prices = azure_api.get_retail_prices(region, currency)

    if sku_name:
        sku_upper = sku_name.upper()
        prices = {k: v for k, v in prices.items() if sku_upper in k.upper()}

    return {
        "mode": "public",
        "region": region,
        "currency": currency,
        "skuCount": len(prices),
        "prices": prices,
        "missingSignals": _MISSING_SIGNALS,
        "disclaimers": _DISCLAIMERS,
        "collectedAtUtc": datetime.datetime.now(datetime.UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Latency matrix
# ---------------------------------------------------------------------------


def get_latency_matrix(regions: list[str]) -> dict[str, Any]:
    """Build an RTT matrix for the given regions.

    Uses the static dataset in :mod:`az_scout.services.region_latency`.
    """
    matrix: dict[str, dict[str, int | None]] = {}
    for a in regions:
        row: dict[str, int | None] = {}
        for b in regions:
            if a == b:
                row[b] = 0
            else:
                row[b] = get_rtt_ms(a, b)
        matrix[a] = row

    return {
        "mode": "public",
        "regions": regions,
        "matrix": matrix,
        "pairCount": len(list_known_pairs()),
        "disclaimers": [
            "Latency values are indicative, based on Microsoft published statistics.",
            "Validate with in-tenant measurements (e.g. Azure Connection Monitor).",
        ],
        "collectedAtUtc": datetime.datetime.now(datetime.UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public capacity strategy (reduced-signal version)
# ---------------------------------------------------------------------------


def public_capacity_strategy(
    sku_name: str,
    instance_count: int = 1,
    regions: list[str] | None = None,
    currency: str = "USD",
    prefer_spot: bool = False,
    require_zones: bool = False,
    max_regions: int = 3,
    latency_sensitive: bool = False,
    target_countries: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic capacity recommendation using only public signals.

    Available signals: retail pricing (PayGo + Spot) and inter-region latency.
    Missing signals: subscription, quota, policy, spot placement scores,
    eviction rate, fragmentation.
    """
    # Resolve candidate regions
    candidate_regions = _resolve_regions(regions, target_countries, max_regions)
    if not candidate_regions:
        return {
            "mode": "public",
            "error": "No candidate regions resolved. Provide at least one region.",
            "missingSignals": _MISSING_SIGNALS,
            "disclaimers": _DISCLAIMERS,
        }

    # Evaluate each region
    evaluations: list[dict[str, Any]] = []
    for region in candidate_regions:
        evaluation = _evaluate_region_public(region, sku_name, currency, prefer_spot, require_zones)
        evaluations.append(evaluation)

    # Sort by score (descending)
    evaluations.sort(key=lambda e: e.get("score", 0), reverse=True)

    # Build recommendations (top N)
    recommendations = evaluations[:max_regions]

    # Build latency notes between recommended regions
    rec_regions = [r["region"] for r in recommendations]
    latency_notes: dict[str, dict[str, int | None]] = {}
    if len(rec_regions) > 1 and latency_sensitive:
        for a in rec_regions:
            row: dict[str, int | None] = {}
            for b in rec_regions:
                if a != b:
                    row[b] = get_rtt_ms(a, b)
            latency_notes[a] = row

    # Determine strategy hint
    strategy_hint = _suggest_strategy(
        recommendations, instance_count, prefer_spot, latency_sensitive
    )

    return {
        "mode": "public",
        "skuName": sku_name,
        "instanceCount": instance_count,
        "strategyHint": strategy_hint,
        "recommendations": recommendations,
        "evidence": {
            "signalsUsed": ["retail_pricing", "inter_region_latency"],
            "latencyNotes": latency_notes if latency_notes else None,
        },
        "missingSignals": _MISSING_SIGNALS,
        "disclaimers": _DISCLAIMERS,
        "collectedAtUtc": datetime.datetime.now(datetime.UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Data-residency → region prefixes (same as capacity_strategy_engine.py)
_COUNTRY_REGIONS: dict[str, list[str]] = {
    "FR": ["francecentral", "francesouth"],
    "DE": ["germanywestcentral", "germanynorth"],
    "CH": ["switzerlandnorth", "switzerlandwest"],
    "UK": ["uksouth", "ukwest"],
    "SE": ["swedencentral"],
    "NO": ["norwayeast", "norwaywest"],
    "NL": ["westeurope"],
    "IE": ["northeurope"],
    "IT": ["italynorth"],
    "ES": ["spaincentral"],
    "PL": ["polandcentral"],
    "US": [
        "eastus",
        "eastus2",
        "centralus",
        "westus",
        "westus2",
        "westus3",
        "northcentralus",
        "southcentralus",
        "westcentralus",
    ],
    "CA": ["canadacentral", "canadaeast"],
    "AU": ["australiaeast", "australiasoutheast", "australiacentral"],
    "JP": ["japaneast", "japanwest"],
    "KR": ["koreacentral", "koreasouth"],
    "SG": ["southeastasia"],
    "IN": ["centralindia", "southindia", "westindia"],
    "BR": ["brazilsouth"],
    "ZA": ["southafricanorth", "southafricawest"],
    "AE": ["uaenorth", "uaecentral"],
}

# EU umbrella
_EU_COUNTRIES = {"FR", "DE", "CH", "NL", "IE", "SE", "NO", "IT", "ES", "PL"}

# Default popular regions when nothing specified
_DEFAULT_REGIONS = [
    "eastus",
    "eastus2",
    "westus2",
    "westeurope",
    "northeurope",
    "francecentral",
    "uksouth",
    "germanywestcentral",
    "southeastasia",
    "australiaeast",
    "japaneast",
    "canadacentral",
]


def _resolve_regions(
    explicit: list[str] | None,
    target_countries: list[str] | None,
    max_regions: int,
) -> list[str]:
    """Build a deduplicated list of candidate regions."""
    if explicit:
        return explicit[:max_regions]

    if target_countries:
        regions: list[str] = []
        for country in target_countries:
            country_upper = country.upper()
            if country_upper == "EU":
                for eu_c in _EU_COUNTRIES:
                    regions.extend(_COUNTRY_REGIONS.get(eu_c, []))
            else:
                regions.extend(_COUNTRY_REGIONS.get(country_upper, []))
        # Deduplicate preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for r in regions:
            if r not in seen:
                seen.add(r)
                deduped.append(r)
        return deduped[:max_regions]

    return _DEFAULT_REGIONS[:max_regions]


def _evaluate_region_public(
    region: str,
    sku_name: str,
    currency: str,
    prefer_spot: bool,
    require_zones: bool,
) -> dict[str, Any]:
    """Score a region using only public signals (pricing)."""
    prices = azure_api.get_retail_prices(region, currency)
    sku_price = prices.get(sku_name)

    score = 50  # base score (neutral – no quota/zone signals)
    paygo: float | None = None
    spot: float | None = None
    available = False
    notes: list[str] = []

    if sku_price:
        available = True
        paygo = sku_price.get("paygo")
        spot = sku_price.get("spot")

        if paygo is not None:
            score += 10  # has PayGo pricing = likely available
        if prefer_spot and spot is not None:
            score += 15  # spot available when preferred
        elif prefer_spot and spot is None:
            score -= 10
            notes.append("Spot pricing not available for this SKU in this region.")
    else:
        score -= 20
        notes.append("No retail pricing found — SKU may not be available in this region.")

    if require_zones:
        notes.append("Zone availability cannot be verified without subscription context.")

    return {
        "region": region,
        "skuName": sku_name,
        "available": available,
        "score": max(0, min(100, score)),
        "pricing": {
            "paygo": paygo,
            "spot": spot,
            "currency": currency,
        },
        "notes": notes,
    }


def _suggest_strategy(
    recommendations: list[dict[str, Any]],
    instance_count: int,
    prefer_spot: bool,
    latency_sensitive: bool,
) -> str:
    """Suggest a strategy type based on available signals."""
    viable = [r for r in recommendations if r.get("available")]

    if not viable:
        return "insufficient_data"

    if len(viable) == 1:
        return "single_region"

    if instance_count <= 2:
        return "single_region"

    if latency_sensitive and len(viable) >= 2:
        return "active_passive"

    if prefer_spot and len(viable) >= 2:
        return "burst_overflow"

    if instance_count >= 10 and len(viable) >= 3:
        return "sharded_multi_region"

    if len(viable) >= 2:
        return "active_active"

    return "single_region"
