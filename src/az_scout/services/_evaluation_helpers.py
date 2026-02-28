"""Shared helpers used by capacity_strategy_engine and deployment_planner.

Contains constants and pure functions that are duplicated across the two
decision engines.  Centralised here to keep a single source of truth.
"""

from __future__ import annotations

import logging
from typing import Literal

from az_scout import azure_api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GPU_FAMILY_MARKERS: tuple[str, ...] = ("nc", "nd", "nv", "hb", "hc")

SPOT_RANK: dict[str, int] = {"High": 3, "Medium": 2, "Low": 1, "Unknown": 0}

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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_gpu_family(family: str) -> bool:
    """Return True if the SKU family is a GPU/HPC family."""
    normalized = family.lower().replace("standard", "").replace("_", "")
    return any(normalized.startswith(m) for m in GPU_FAMILY_MARKERS)


def best_spot_label(
    zone_scores: dict[str, str],
) -> Literal["High", "Medium", "Low", "Unknown"]:
    """Return the best (most optimistic) spot score across zones."""
    if not zone_scores:
        return "Unknown"
    best = max(zone_scores.values(), key=lambda s: SPOT_RANK.get(s, 0))
    if best in ("High", "Medium", "Low"):
        return best  # type: ignore[return-value]
    return "Unknown"


def resolve_candidate_regions(
    *,
    allow_regions: list[str] | None,
    deny_regions: list[str] | None,
    data_residency: str | None,
    subscription_id: str,
    tenant_id: str | None,
    warnings: list[str],
    errors: list[str],
    max_regions: int | None = None,
) -> list[str]:
    """Resolve candidate regions from constraint parameters.

    The common logic used by both capacity_strategy_engine and
    deployment_planner: allow list → data residency fallback → all regions,
    then deny list filtering, then optional truncation.
    """
    if allow_regions:
        candidates = list(allow_regions)
    elif data_residency and data_residency != "ANY":
        if data_residency in DATA_RESIDENCY_REGIONS:
            candidates = list(DATA_RESIDENCY_REGIONS[data_residency])
        else:
            warnings.append(
                f"No region mapping for data residency '{data_residency}'."
                " Using all available regions."
            )
            candidates = fetch_all_regions(subscription_id, tenant_id, errors)
    else:
        candidates = fetch_all_regions(subscription_id, tenant_id, errors)

    if deny_regions:
        deny_set = {r.lower() for r in deny_regions}
        candidates = [r for r in candidates if r.lower() not in deny_set]

    if max_regions is not None:
        candidates = candidates[:max_regions]

    return candidates


def fetch_all_regions(
    subscription_id: str,
    tenant_id: str | None,
    errors: list[str],
) -> list[str]:
    """Fetch AZ-enabled regions from the Azure API."""
    try:
        regions = azure_api.list_regions(subscription_id, tenant_id)
        return [r["name"] for r in regions]
    except Exception as exc:
        errors.append(f"Failed to list regions: {exc}")
        return []
