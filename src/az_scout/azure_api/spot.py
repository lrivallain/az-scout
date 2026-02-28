"""Spot Placement Scores – Azure Compute RP."""

from __future__ import annotations

import logging
import time

import requests

from az_scout.azure_api._auth import AZURE_MGMT_URL, _get_headers

logger = logging.getLogger(__name__)

SPOT_API_VERSION = "2025-06-05"
_SPOT_CACHE_TTL = 600  # 10 minutes
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
    headers = _get_headers(tenant_id)
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

    max_retries = 3
    resp = None
    for attempt in range(max_retries):
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 429:
            retry_header = resp.headers.get("Retry-After")
            if retry_header:
                try:
                    retry_after = min(int(retry_header), 8)
                except (TypeError, ValueError):
                    retry_after = 2**attempt  # 1, 2, 4
            else:
                retry_after = 2**attempt  # 1, 2, 4
            logger.warning(
                "Spot scores 429, retrying in %ss (attempt %s/%s)",
                retry_after,
                attempt + 1,
                max_retries,
            )
            time.sleep(retry_after)
            continue
        if resp.status_code == 400:
            body = resp.text[:500]
            msg = (
                f"Bad request (400) for spot placement scores on "
                f"subscription {subscription_id} / region {region}: {body}"
            )
            logger.warning(msg)
            raise ValueError(msg)
        if resp.status_code == 403:
            msg = (
                f"Access denied (403) for spot placement scores on subscription "
                f"{subscription_id}. Ensure the identity has the "
                f"'Compute Recommendations Role' RBAC role."
            )
            logger.warning(msg)
            raise PermissionError(msg)
        if resp.status_code == 404:
            msg = (
                f"Spot placement scores endpoint not found (404) for "
                f"subscription {subscription_id} / region {region}. "
                f"Ensure Microsoft.Compute resource provider is registered."
            )
            logger.warning(msg)
            raise FileNotFoundError(msg)
        if resp.status_code >= 500:
            wait_time = 2**attempt  # 1, 2, 4
            logger.warning(
                "Spot scores %s, retrying in %ss (attempt %s/%s)",
                resp.status_code,
                wait_time,
                attempt + 1,
                max_retries,
            )
            time.sleep(wait_time)
            continue
        resp.raise_for_status()
        break

    if resp is None or resp.status_code >= 400:
        if resp is not None:
            resp.raise_for_status()
        return {}

    data = resp.json()
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
) -> dict:
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
