"""Spot Placement Scores – Azure Compute RP."""

from __future__ import annotations

import logging
import time
from typing import Any

from az_scout.azure_api._arm import (
    ArmAuthorizationError,
    ArmNotFoundError,
    ArmRequestError,
    arm_post,
)
from az_scout.azure_api._auth import AZURE_MGMT_URL

logger = logging.getLogger(__name__)

SPOT_API_VERSION = "2025-06-05"
_SPOT_CACHE_TTL = 3600  # 1 hour – the API is heavily rate-limited
_spot_cache: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}
_SPOT_BATCH_SIZE = 5  # Azure API limit: max 5 VM sizes per call


def _spot_cache_key(
    subscription_id: str,
    region: str,
    instance_count: int,
    vm_sizes: list[str],
) -> str:
    """Build a deterministic cache key for spot score results."""
    sizes_hash = ",".join(sorted(vm_sizes))
    return f"{subscription_id}:{region}:{instance_count}:{sizes_hash}"


def _fetch_spot_batch(
    region: str,
    subscription_id: str,
    vm_sizes: list[str],
    instance_count: int,
    tenant_id: str | None,
) -> dict[str, dict[str, str]]:
    """POST a single batch of VM sizes to the Compute RP spot endpoint.

    Returns a dict mapping VM size → {zone → score}.
    Handles 429 with retry/back-off and 403/404 gracefully.
    """
    url = (
        f"{AZURE_MGMT_URL}/subscriptions/{subscription_id}/providers/"
        f"Microsoft.Compute/locations/{region}/placementScores/spot/generate"
        f"?api-version={SPOT_API_VERSION}"
    )
    payload = {
        "desiredLocations": [region],
        "desiredSizes": [{"sku": s} for s in vm_sizes],
        "desiredCount": instance_count,
        "availabilityZones": True,
    }

    try:
        data = arm_post(url, json=payload, tenant_id=tenant_id)
    except ArmAuthorizationError:
        msg = (
            f"Access denied (403) for spot placement scores on subscription "
            f"{subscription_id}. Ensure the identity has the "
            f"'Compute Recommendations Role' RBAC role."
        )
        logger.warning(msg)
        raise PermissionError(msg) from None
    except ArmNotFoundError:
        msg = (
            f"Spot placement scores endpoint not found (404) for "
            f"subscription {subscription_id} / region {region}. "
            f"Ensure Microsoft.Compute resource provider is registered."
        )
        logger.warning(msg)
        raise FileNotFoundError(msg) from None
    except ArmRequestError as exc:
        if exc.status_code == 400:
            msg = (
                f"Bad request (400) for spot placement scores on "
                f"subscription {subscription_id} / region {region}: {exc}"
            )
            logger.warning(msg)
            raise ValueError(msg) from None
        raise

    scores: dict[str, dict[str, str]] = {}
    for item in data.get("placementScores", []):
        sku_name = item.get("sku", "")
        score = item.get("score", "Unknown")
        zone = item.get("availabilityZone", "")
        if sku_name:
            if score == "DataNotFoundOrStale":
                score = "Unknown"
            if sku_name not in scores:
                scores[sku_name] = {}
            scores[sku_name][zone] = score
    return scores


def get_spot_placement_scores(
    region: str,
    subscription_id: str,
    vm_sizes: list[str],
    instance_count: int = 1,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Return spot placement scores for a list of VM sizes.

        The Compute RP accepts at most ~100 VM sizes per call; this
        function batches them in chunks of ``_SPOT_BATCH_SIZE`` and runs
        them sequentially to avoid 429 rate-limit storms.

    Returns ``{"scores": {vmSize: {zone: score}}, "errors": [...]}``.

        Scores are cached for ``_SPOT_CACHE_TTL`` seconds.
    """
    if not vm_sizes:
        return {"scores": {}, "errors": []}

    # Check cache
    cache_key = _spot_cache_key(subscription_id, region, instance_count, vm_sizes)
    now = time.monotonic()
    cached = _spot_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _SPOT_CACHE_TTL:
            return {"scores": data, "errors": []}

    # Split into batches
    batches: list[list[str]] = []
    for i in range(0, len(vm_sizes), _SPOT_BATCH_SIZE):
        batches.append(vm_sizes[i : i + _SPOT_BATCH_SIZE])

    merged_scores: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for i, batch in enumerate(batches):
        try:
            batch_scores = _fetch_spot_batch(
                region, subscription_id, batch, instance_count, tenant_id
            )
            for sku, zone_scores in batch_scores.items():
                merged_scores.setdefault(sku, {}).update(zone_scores)
        except Exception as exc:
            errors.append(str(exc))
        # Pace requests: wait between batches to avoid 429 storms
        if i < len(batches) - 1:
            time.sleep(1)

    # Cache the merged result (only if no errors)
    if not errors:
        _spot_cache[cache_key] = (time.monotonic(), merged_scores)

    return {"scores": merged_scores, "errors": errors}
