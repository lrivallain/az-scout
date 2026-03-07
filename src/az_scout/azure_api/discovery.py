"""Tenant, subscription, and region discovery."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from az_scout.azure_api._arm import arm_get, arm_paginate
from az_scout.azure_api._auth import (
    AZURE_API_VERSION,
    AZURE_MGMT_URL,
    _check_tenant_auth,
    _get_default_tenant_id,
    _suppress_stderr,
)
from az_scout.azure_api._cache import _cache_set, _cached

logger = logging.getLogger(__name__)


def list_tenants(tenant_id: str | None = None) -> dict[str, Any]:
    """Return tenants with auth status and the default tenant ID.

    Returns ``{"tenants": [...], "defaultTenantId": ...}``.
    Results are cached for ``_DISCOVERY_CACHE_TTL`` seconds.
    """
    cache_key = f"tenants:{tenant_id or ''}"
    cached = _cached(
        cache_key, ttl=3600
    )  # Tenants don't change often, so a long TTL is reasonable.
    if cached is not None:
        return cached  # type: ignore[return-value]

    url = f"{AZURE_MGMT_URL}/tenants?api-version={AZURE_API_VERSION}"
    all_tenants = arm_paginate(url, tenant_id=tenant_id)

    tenant_ids = [t["tenantId"] for t in all_tenants]

    # Suppress AzureCliCredential subprocess stderr noise across all threads.
    with _suppress_stderr(), ThreadPoolExecutor(max_workers=min(len(tenant_ids), 8)) as pool:
        auth_results = dict(zip(tenant_ids, pool.map(_check_tenant_auth, tenant_ids), strict=True))

    tenants = [
        {
            "id": t["tenantId"],
            "name": t.get("displayName") or t["tenantId"],
            "authenticated": auth_results.get(t["tenantId"], False),
        }
        for t in all_tenants
    ]
    result = {
        "tenants": sorted(tenants, key=lambda x: x["name"].lower()),
        "defaultTenantId": _get_default_tenant_id(),
    }
    _cache_set(cache_key, result)
    return result


def list_subscriptions(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Return enabled subscriptions as ``[{"id": ..., "name": ...}, ...]``."""
    url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
    all_subs = arm_paginate(url, tenant_id=tenant_id)
    logger.debug("list_subscriptions: %d total, tenant=%s", len(all_subs), tenant_id or "default")

    subs = [
        {"id": s["subscriptionId"], "name": s["displayName"]}
        for s in all_subs
        if s.get("state") == "Enabled"
    ]
    logger.info("list_subscriptions: %d enabled (of %d total)", len(subs), len(all_subs))
    return sorted(subs, key=lambda x: x["name"].lower())


def list_regions(
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return AZ-enabled regions as ``[{"name": ..., "displayName": ...}, ...]``.

    When *subscription_id* is ``None`` the first enabled subscription is used.
    """
    sub_id = subscription_id
    if not sub_id:
        subs_url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
        subs_data = arm_get(subs_url, tenant_id=tenant_id)
        enabled = [
            s["subscriptionId"] for s in subs_data.get("value", []) if s.get("state") == "Enabled"
        ]
        if not enabled:
            raise LookupError("No enabled subscriptions found")
        sub_id = enabled[0]

    url = f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/locations?api-version={AZURE_API_VERSION}"
    loc_data = arm_get(url, tenant_id=tenant_id)

    locations = loc_data.get("value", [])
    regions = [
        {"name": loc["name"], "displayName": loc["displayName"]}
        for loc in locations
        if loc.get("availabilityZoneMappings")
        and loc.get("metadata", {}).get("regionType") == "Physical"
    ]
    result = sorted(regions, key=lambda x: x["displayName"])
    logger.info(
        "list_regions: %d AZ-enabled regions (of %d locations), sub=%s",
        len(result),
        len(locations),
        sub_id[:8] + "…" if sub_id else "auto",
    )
    return result


def list_locations(
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, str]]:
    """Return all ARM locations as ``[{"name": ..., "displayName": ...}, ...]``.

    Unlike :func:`list_regions` this includes regions **without** Availability
    Zones.  When *subscription_id* is ``None`` the first enabled subscription
    (sorted by ID) is used.
    """
    sub_id = subscription_id
    if not sub_id:
        subs_url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
        subs_data = arm_get(subs_url, tenant_id=tenant_id)
        enabled = sorted(
            s["subscriptionId"] for s in subs_data.get("value", []) if s.get("state") == "Enabled"
        )
        if not enabled:
            raise LookupError("No enabled subscriptions found")
        sub_id = enabled[0]

    url = f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/locations?api-version={AZURE_API_VERSION}"
    loc_data = arm_get(url, tenant_id=tenant_id)

    locations = loc_data.get("value", [])
    result = sorted(
        [
            {"name": loc["name"], "displayName": loc["displayName"]}
            for loc in locations
            if loc.get("metadata", {}).get("regionType") == "Physical"
        ],
        key=lambda x: x["displayName"],
    )
    logger.info(
        "list_locations: %d physical regions (of %d locations), sub=%s",
        len(result),
        len(locations),
        sub_id[:8] + "…" if sub_id else "auto",
    )
    return result


def preload_discovery() -> None:
    """Fetch tenants to warm the cache.

    Intended to be called in a background thread at server startup so that
    the first browser request is served from cache.  Errors are logged but
    never propagated – the web UI will retry on demand.
    """
    try:
        logger.info("Preloading tenant list…")
        list_tenants()
        logger.info("Tenant preload complete.")
    except Exception:
        logger.warning("Preload: failed to fetch tenants", exc_info=True)
