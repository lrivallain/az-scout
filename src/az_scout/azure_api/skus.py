"""SKU listing, filtering, and profile queries."""

from __future__ import annotations

import logging
import time

import requests

from az_scout.azure_api._auth import (
    AZURE_API_VERSION,
    AZURE_MGMT_URL,
    _get_headers,
)
from az_scout.azure_api._pagination import _paginate

logger = logging.getLogger(__name__)

# SKU profile cache
_SKU_PROFILE_CACHE_TTL = 600  # 10 minutes
_sku_profile_cache: dict[str, tuple[float, dict | None]] = {}


def _sku_name_matches(filter_val: str, sku_name: str) -> bool:
    """Check if *filter_val* matches *sku_name* with fuzzy multi-part logic.

    First tries a direct substring match.  If that fails and the filter
    contains hyphens or underscores, it splits into parts and checks that all
    parts appear in the SKU name in order.  This lets user-friendly names like
    ``"FX48-v2"`` match ARM names like ``Standard_FX48mds_v2``.
    """
    if filter_val in sku_name:
        return True
    # Normalise separators and try again
    normalised = filter_val.replace("-", "_")
    if normalised in sku_name:
        return True
    # Multi-part: split on separators and check all parts appear in order
    parts = [p for p in normalised.split("_") if p]
    if len(parts) <= 1:
        return False
    pos = 0
    for part in parts:
        idx = sku_name.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    return True


def get_skus(
    region: str,
    subscription_id: str,
    tenant_id: str | None = None,
    resource_type: str = "virtualMachines",
    *,
    name: str | None = None,
    family: str | None = None,
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    min_memory_gb: float | None = None,
    max_memory_gb: float | None = None,
) -> list[dict]:
    """Return resource SKUs with zone/restriction info for *region*.

    Optional filters (all case-insensitive substring matches unless noted):

    * *name* – filter by SKU name (e.g. ``"D2s"`` matches ``Standard_D2s_v3``).
    * *family* – filter by SKU family (e.g. ``"DSv3"``).
    * *min_vcpus* / *max_vcpus* – vCPU count range (inclusive).
    * *min_memory_gb* / *max_memory_gb* – memory in GB range (inclusive).

    When no filters are provided all SKUs for the requested resource type are
    returned (current behaviour).
    """
    headers = _get_headers(tenant_id)
    url = (
        f"{AZURE_MGMT_URL}/subscriptions/{subscription_id}/providers/"
        f"Microsoft.Compute/skus?api-version={AZURE_API_VERSION}"
        f"&$filter=location eq '{region}'"
    )

    all_skus: list[dict] = []

    # Simple retry with exponential backoff for transient timeouts
    for attempt in range(3):
        try:
            all_skus = _paginate(url, headers, timeout=60)
            break
        except requests.ReadTimeout:
            if attempt < 2:
                wait_time = 2**attempt
                logger.warning(
                    "SKU API timeout, retrying in %ss (attempt %s/3)", wait_time, attempt + 1
                )
                time.sleep(wait_time)
            else:
                raise

    name_lower = name.lower() if name else None
    family_lower = family.lower() if family else None

    filtered: list[dict] = []
    for sku in all_skus:
        if sku.get("resourceType") != resource_type:
            continue

        # Name / family substring filters (fuzzy multi-part matching)
        if name_lower and not _sku_name_matches(name_lower, (sku.get("name") or "").lower()):
            continue
        if family_lower and family_lower not in (sku.get("family") or "").lower():
            continue

        location_info = sku.get("locationInfo", [])
        zones_for_region: list[str] = []
        for loc_info in location_info:
            if loc_info.get("location", "").lower() == region.lower():
                zones_for_region = loc_info.get("zones", [])
                break

        restrictions: list[str] = []
        for restriction in sku.get("restrictions", []):
            if restriction.get("type") == "Zone":
                restrictions.extend(restriction.get("restrictionInfo", {}).get("zones", []))

        capabilities: dict[str, str] = {}
        for cap in sku.get("capabilities", []):
            cap_name = cap.get("name", "")
            cap_value = cap.get("value", "")
            if cap_name in ("vCPUs", "MemoryGB", "MaxDataDiskCount", "PremiumIO"):
                capabilities[cap_name] = cap_value

        # vCPU / memory range filters
        if min_vcpus is not None or max_vcpus is not None:
            try:
                vcpus = int(capabilities.get("vCPUs", "0"))
            except ValueError:
                continue
            if min_vcpus is not None and vcpus < min_vcpus:
                continue
            if max_vcpus is not None and vcpus > max_vcpus:
                continue

        if min_memory_gb is not None or max_memory_gb is not None:
            try:
                mem = float(capabilities.get("MemoryGB", "0"))
            except ValueError:
                continue
            if min_memory_gb is not None and mem < min_memory_gb:
                continue
            if max_memory_gb is not None and mem > max_memory_gb:
                continue

        filtered.append(
            {
                "name": sku.get("name"),
                "tier": sku.get("tier"),
                "size": sku.get("size"),
                "family": sku.get("family"),
                "zones": zones_for_region,
                "restrictions": restrictions,
                "capabilities": capabilities,
            }
        )

    return sorted(filtered, key=lambda x: x.get("name", ""))


def get_mappings(
    region: str,
    subscription_ids: list[str],
    tenant_id: str | None = None,
) -> list[dict]:
    """Return logical→physical zone mappings per subscription."""
    headers = _get_headers(tenant_id)
    results: list[dict] = []

    for sub_id in subscription_ids:
        url = f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/locations?api-version={AZURE_API_VERSION}"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            locations = resp.json().get("value", [])

            mappings: list[dict] = []
            for loc in locations:
                if loc["name"] == region:
                    for m in loc.get("availabilityZoneMappings", []):
                        mappings.append(
                            {
                                "logicalZone": m["logicalZone"],
                                "physicalZone": m["physicalZone"],
                            }
                        )
                    break

            results.append(
                {
                    "subscriptionId": sub_id,
                    "region": region,
                    "mappings": sorted(mappings, key=lambda m: m["logicalZone"]),
                }
            )
        except Exception as exc:
            logger.warning("Error fetching mappings for subscription %s: %s", sub_id, exc)
            results.append(
                {
                    "subscriptionId": sub_id,
                    "region": region,
                    "mappings": [],
                    "error": str(exc),
                }
            )

    return results


def _parse_capability_value(value: str) -> str | bool | int | float:
    """Convert an ARM capability string to an appropriate Python type."""
    if value in ("True", "False"):
        return value == "True"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def get_sku_profile(
    region: str,
    subscription_id: str,
    sku_name: str,
    tenant_id: str | None = None,
) -> dict | None:
    """Return full capabilities, restrictions and zones for a single VM SKU.

    Calls the ARM ``Microsoft.Compute/skus`` endpoint and returns::

        {
            "zones": ["1", "2", "3"],
            "capabilities": { "vCPUs": 2, "MemoryGB": 8, ... },
            "restrictions": [ { "type": ..., "reasonCode": ..., "zones": [...] } ],
        }

    Returns ``None`` when the SKU is not found in the region.
    Results are cached for ``_SKU_PROFILE_CACHE_TTL`` seconds.
    """
    cache_key = f"profile:{subscription_id}:{region}:{sku_name}:{tenant_id or ''}"
    now = time.monotonic()
    cached = _sku_profile_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _SKU_PROFILE_CACHE_TTL:
            return data

    headers = _get_headers(tenant_id)
    url = (
        f"{AZURE_MGMT_URL}/subscriptions/{subscription_id}/providers/"
        f"Microsoft.Compute/skus?api-version={AZURE_API_VERSION}"
        f"&$filter=location eq '{region}'"
    )

    try:
        all_skus = _paginate(url, headers, timeout=60)
    except Exception:
        logger.warning("Failed to fetch SKU profile for %s in %s", sku_name, region)
        return None

    for sku in all_skus:
        if sku.get("name") == sku_name and sku.get("resourceType") == "virtualMachines":
            # Zones
            zones: list[str] = []
            for loc_info in sku.get("locationInfo", []):
                if loc_info.get("location", "").lower() == region.lower():
                    zones = loc_info.get("zones", [])
                    break

            # Capabilities – all of them, parsed
            capabilities: dict[str, str | bool | int | float] = {}
            for cap in sku.get("capabilities", []):
                cap_name = cap.get("name", "")
                cap_value = cap.get("value", "")
                if cap_name:
                    capabilities[cap_name] = _parse_capability_value(cap_value)

            # Restrictions – full details
            restrictions: list[dict] = []
            for restriction in sku.get("restrictions", []):
                restrictions.append(
                    {
                        "type": restriction.get("type"),
                        "reasonCode": restriction.get("reasonCode"),
                        "zones": restriction.get("restrictionInfo", {}).get("zones", []),
                        "locations": restriction.get("restrictionInfo", {}).get("locations", []),
                    }
                )

            result: dict = {
                "zones": sorted(zones),
                "capabilities": capabilities,
                "restrictions": restrictions,
            }
            _sku_profile_cache[cache_key] = (time.monotonic(), result)
            return result

    # SKU not found
    _sku_profile_cache[cache_key] = (time.monotonic(), None)
    return None
