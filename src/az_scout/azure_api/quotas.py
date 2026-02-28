"""Compute resource usage (vCPU quota) queries."""

from __future__ import annotations

import logging
import time

import requests

from az_scout.azure_api._auth import AZURE_MGMT_URL, _get_headers

logger = logging.getLogger(__name__)

COMPUTE_API_VERSION = "2024-11-01"
_USAGE_CACHE_TTL = 600  # 10 minutes
_usage_cache: dict[str, tuple[float, list[dict]]] = {}


def _normalize_family(family: str) -> str:
    """Normalize a SKU family string for matching against usage ``name.value``."""
    return family.replace(" ", "").replace("-", "").replace("_", "").lower()


def get_compute_usages(
    region: str,
    subscription_id: str,
    tenant_id: str | None = None,
) -> list[dict]:
    """Return Compute resource usages (vCPU quotas) for *region*.

    Results are cached for ``_USAGE_CACHE_TTL`` seconds to avoid
    redundant ARM calls.  Each entry has the shape::

        {"name": {"value": ..., "localizedValue": ...},
         "currentValue": int, "limit": int, "unit": str}

    Handles HTTP 429 with retry/back-off and HTTP 403 gracefully
    (returns an empty list).
    """
    cache_key = f"{subscription_id}:{region}:{tenant_id or ''}"
    now = time.monotonic()
    cached = _usage_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _USAGE_CACHE_TTL:
            return data

    headers = _get_headers(tenant_id)
    url = (
        f"{AZURE_MGMT_URL}/subscriptions/{subscription_id}/providers/"
        f"Microsoft.Compute/locations/{region}/usages?api-version={COMPUTE_API_VERSION}"
    )

    resp = None
    for attempt in range(3):
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 429:
            try:
                retry_after = int(resp.headers.get("Retry-After", str(2**attempt)))
            except (TypeError, ValueError):
                retry_after = 2**attempt
            logger.warning(
                "Compute usages 429, retrying in %ss (attempt %s/3)",
                retry_after,
                attempt + 1,
            )
            time.sleep(retry_after)
            continue
        if resp.status_code == 403:
            logger.warning(
                "Access denied (403) for compute usages: %s / %s",
                subscription_id,
                region,
            )
            return []
        resp.raise_for_status()
        result: list[dict] = resp.json().get("value", [])
        _usage_cache[cache_key] = (time.monotonic(), result)
        return result

    # Exhausted retries on 429
    if resp is not None:
        resp.raise_for_status()
    return []


def enrich_skus_with_quotas(
    skus: list[dict],
    region: str,
    subscription_id: str,
    tenant_id: str | None = None,
) -> list[dict]:
    """Add per-family quota information to each SKU dict **in-place**.

    Each SKU gets a ``"quota"`` key with ``limit``, ``used`` and
    ``remaining`` (all ``int | None``).  ``None`` means unknown
    (no matching usage entry or fetch failure).
    """
    usage_map: dict[str, dict] = {}
    try:
        usages = get_compute_usages(region, subscription_id, tenant_id)
        for u in usages:
            name_obj = u.get("name")
            if isinstance(name_obj, dict):
                name_value = name_obj.get("value", "")
                if name_value:
                    usage_map[_normalize_family(name_value)] = u
    except Exception:
        logger.warning("Failed to fetch/parse compute usages, quotas will be unknown")

    for sku in skus:
        family = sku.get("family") or ""
        key = _normalize_family(family)
        usage = usage_map.get(key) if key else None
        if usage:
            limit = usage.get("limit", 0)
            current = usage.get("currentValue", 0)
            sku["quota"] = {
                "limit": limit,
                "used": current,
                "remaining": limit - current,
            }
        else:
            sku["quota"] = {"limit": None, "used": None, "remaining": None}

    return skus
