"""Shared Azure ARM API helpers.

Provides pure-data functions that both the Flask web UI and the MCP server
can call.  Every public function returns plain Python objects (dicts / lists)
– no Flask ``Response`` wrappers.
"""

import base64
import json
import logging
import os
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import requests
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

AZURE_API_VERSION = "2022-12-01"
AZURE_MGMT_URL = "https://management.azure.com"

credential = DefaultAzureCredential()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@contextmanager
def _suppress_stderr() -> Generator[None]:
    """Temporarily redirect OS-level stderr to ``/dev/null``.

    This silences subprocess output (e.g. from ``AzureCliCredential``)
    that bypasses Python's logging system.
    """
    original_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(original_fd, 2)
        os.close(original_fd)


def _get_headers(
    tenant_id: str | None = None,
    cred: DefaultAzureCredential | None = None,
) -> dict[str, str]:
    """Return authorization headers using the given or default credential.

    When *tenant_id* is provided the token is scoped to that tenant.
    When *cred* is provided it is used instead of the module-level credential
    (e.g. an ``OnBehalfOfCredential`` from the OBO flow).
    """
    effective = cred or credential
    kwargs: dict[str, str] = {}
    if tenant_id:
        kwargs["tenant_id"] = tenant_id
    token = effective.get_token(f"{AZURE_MGMT_URL}/.default", **kwargs)
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


def _get_default_tenant_id() -> str | None:
    """Extract the tenant ID from the current credential's token."""
    try:
        token = credential.get_token(f"{AZURE_MGMT_URL}/.default")
        payload = token.token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        tid: str | None = claims.get("tid") or claims.get("tenant_id")
        return tid
    except Exception:
        return None


def _check_tenant_auth(tenant_id: str) -> bool:
    """Return *True* if the credential can obtain a token for *tenant_id*."""
    azure_logger = logging.getLogger("azure")
    previous_level = azure_logger.level
    azure_logger.setLevel(logging.CRITICAL)
    try:
        credential.get_token(f"{AZURE_MGMT_URL}/.default", tenant_id=tenant_id)
        return True
    except Exception:
        logger.warning("Authentication failed for tenant %s", tenant_id)
        return False
    finally:
        azure_logger.setLevel(previous_level)


def _paginate(url: str, headers: dict[str, str], timeout: int = 30) -> list[dict]:
    """Fetch all pages from an ARM list endpoint and return the merged values."""
    items: list[dict] = []
    while url:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", []))
        url = data.get("nextLink")
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Discovery cache – short TTL to avoid stale data but fast enough for
# page loads that hit the same endpoints in quick succession.
_DISCOVERY_CACHE_TTL = 300  # 5 minutes
_discovery_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: int = _DISCOVERY_CACHE_TTL) -> object | None:
    """Return cached value if still valid, else ``None``."""
    entry = _discovery_cache.get(key)
    if entry is not None:
        ts, data = entry
        if time.monotonic() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data: object) -> None:
    """Store a value in the discovery cache."""
    _discovery_cache[key] = (time.monotonic(), data)


def list_tenants(tenant_id: str | None = None) -> dict:
    """Return tenants with auth status and the default tenant ID.

    Returns ``{"tenants": [...], "defaultTenantId": ...}``.
    Results are cached for ``_DISCOVERY_CACHE_TTL`` seconds.
    """
    cache_key = f"tenants:{tenant_id or ''}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    headers = _get_headers(tenant_id)
    url = f"{AZURE_MGMT_URL}/tenants?api-version={AZURE_API_VERSION}"
    all_tenants = _paginate(url, headers)

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


def list_subscriptions(tenant_id: str | None = None) -> list[dict]:
    """Return enabled subscriptions as ``[{"id": ..., "name": ...}, ...]``."""
    headers = _get_headers(tenant_id)
    url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
    all_subs = _paginate(url, headers)

    subs = [
        {"id": s["subscriptionId"], "name": s["displayName"]}
        for s in all_subs
        if s.get("state") == "Enabled"
    ]
    return sorted(subs, key=lambda x: x["name"].lower())


def list_regions(
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    """Return AZ-enabled regions as ``[{"name": ..., "displayName": ...}, ...]``.

    When *subscription_id* is ``None`` the first enabled subscription is used.
    """
    headers = _get_headers(tenant_id)

    sub_id = subscription_id
    if not sub_id:
        subs_url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
        subs_resp = requests.get(subs_url, headers=headers, timeout=30)
        subs_resp.raise_for_status()
        enabled = [
            s["subscriptionId"]
            for s in subs_resp.json().get("value", [])
            if s.get("state") == "Enabled"
        ]
        if not enabled:
            raise LookupError("No enabled subscriptions found")
        sub_id = enabled[0]

    url = f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/locations?api-version={AZURE_API_VERSION}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    locations = resp.json().get("value", [])
    regions = [
        {"name": loc["name"], "displayName": loc["displayName"]}
        for loc in locations
        if loc.get("availabilityZoneMappings")
        and loc.get("metadata", {}).get("regionType") == "Physical"
    ]
    result = sorted(regions, key=lambda x: x["displayName"])
    return result


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


# ---------------------------------------------------------------------------
# Compute usages (vCPU quotas) – with TTL cache
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Spot Placement Scores – Azure Compute RP
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Retail Prices – Azure Retail Prices API (unauthenticated)
# ---------------------------------------------------------------------------

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
RETAIL_PRICES_API_VERSION = "2023-01-01-preview"
_PRICE_CACHE_TTL = 3600  # 1 hour
_price_cache: dict[str, tuple[float, dict[str, dict]]] = {}


def _fetch_retail_prices(
    region: str,
    currency_code: str = "USD",
) -> list[dict]:
    """Fetch all VM retail prices for a region from the Azure Retail Prices API.

    This API is unauthenticated.  Handles pagination via ``NextPageLink``
    and retries on HTTP 429 with back-off.
    """
    odata_filter = (
        f"armRegionName eq '{region}' "
        f"and serviceName eq 'Virtual Machines' "
        f"and priceType eq 'Consumption'"
    )
    items: list[dict] = []
    url: str | None = RETAIL_PRICES_URL
    params: dict[str, str] | None = {
        "api-version": RETAIL_PRICES_API_VERSION,
        "$filter": odata_filter,
        "currencyCode": currency_code,
    }

    while url:
        resp = None
        for attempt in range(3):
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                try:
                    retry_after = int(resp.headers.get("Retry-After", str(2**attempt)))
                except (TypeError, ValueError):
                    retry_after = 2**attempt
                logger.warning(
                    "Retail Prices 429, retrying in %ss (attempt %s/3)",
                    retry_after,
                    attempt + 1,
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            break

        if resp is None or resp.status_code >= 400:
            if resp is not None:
                resp.raise_for_status()
            break

        data = resp.json()
        items.extend(data.get("Items", []))
        url = data.get("NextPageLink")
        params = None  # NextPageLink already includes query parameters

    return items


def _select_price_line(lines: list[dict]) -> dict | None:
    """Pick the best price line from a list of retail-price items.

    Prefers non-Windows (Linux) lines.  Among candidates picks the
    cheapest ``retailPrice``.
    """
    if not lines:
        return None

    non_windows = [
        item for item in lines if "windows" not in (item.get("productName") or "").lower()
    ]
    candidates = non_windows if non_windows else lines
    return min(candidates, key=lambda item: item.get("retailPrice", float("inf")))


def get_retail_prices(
    region: str,
    currency_code: str = "USD",
) -> dict[str, dict]:
    """Return retail prices for all VM SKUs in *region*.

    Returns ``{armSkuName: {"paygo": float|None, "spot": float|None,
    "currency": str}}``.

    Results are cached for ``_PRICE_CACHE_TTL`` seconds.
    """
    cache_key = f"{region}:{currency_code}"
    now = time.monotonic()
    cached = _price_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _PRICE_CACHE_TTL:
            return data

    try:
        items = _fetch_retail_prices(region, currency_code)
    except Exception:
        logger.warning("Failed to fetch retail prices for %s", region)
        return {}

    paygo_by_sku: dict[str, list[dict]] = {}
    spot_by_sku: dict[str, list[dict]] = {}

    for item in items:
        sku_name = item.get("armSkuName", "")
        if not sku_name:
            continue
        sku_display = (item.get("skuName") or "").lower()
        if "low priority" in sku_display:
            continue  # skip legacy Low Priority pricing
        if "spot" in sku_display:
            spot_by_sku.setdefault(sku_name, []).append(item)
        else:
            paygo_by_sku.setdefault(sku_name, []).append(item)

    all_skus = set(paygo_by_sku) | set(spot_by_sku)
    result: dict[str, dict] = {}
    for sku_name in all_skus:
        paygo_line = _select_price_line(paygo_by_sku.get(sku_name, []))
        spot_line = _select_price_line(spot_by_sku.get(sku_name, []))
        result[sku_name] = {
            "paygo": paygo_line["retailPrice"] if paygo_line else None,
            "spot": spot_line["retailPrice"] if spot_line else None,
            "currency": currency_code,
        }

    _price_cache[cache_key] = (time.monotonic(), result)
    return result


def enrich_skus_with_prices(
    skus: list[dict],
    region: str,
    currency_code: str = "USD",
) -> list[dict]:
    """Add per-SKU pricing to each dict **in-place**.

    Each SKU gets a ``"pricing"`` key with ``paygo``, ``spot`` and
    ``currency``.  Values are ``None`` when no matching price was found.
    """
    prices = get_retail_prices(region, currency_code)
    for sku in skus:
        name = sku.get("name", "")
        price_info = prices.get(name)
        if price_info:
            sku["pricing"] = price_info
        else:
            sku["pricing"] = {"paygo": None, "spot": None, "currency": currency_code}
    return skus


# ---------------------------------------------------------------------------
# SKU Profile – full capabilities & restrictions from ARM
# ---------------------------------------------------------------------------

_SKU_PROFILE_CACHE_TTL = 600  # 10 minutes
_sku_profile_cache: dict[str, tuple[float, dict | None]] = {}


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


# ---------------------------------------------------------------------------
# Detailed SKU pricing – PayGo, Spot, RI 1Y/3Y, Savings Plan 1Y/3Y
# ---------------------------------------------------------------------------

_DETAIL_PRICE_CACHE_TTL = 3600  # 1 hour
_detail_price_cache: dict[str, tuple[float, dict]] = {}


def _fetch_all_retail_prices(
    region: str,
    sku_name: str,
    currency_code: str = "USD",
) -> list[dict]:
    """Fetch all retail price items for a single SKU (all price types)."""
    odata_filter = (
        f"armRegionName eq '{region}' "
        f"and serviceName eq 'Virtual Machines' "
        f"and armSkuName eq '{sku_name}'"
    )
    items = _fetch_retail_prices_with_filter(odata_filter, currency_code)

    # If exact match returned nothing, try a 'contains' query as fallback.
    # This handles cases where the caller has a slightly wrong ARM name
    # (e.g. "Standard_FX48_v2" instead of "Standard_FX48mds_v2").
    if not items:
        parts = sku_name.replace("-", "_").split("_")
        # Use the most distinctive part (skip "Standard" prefix)
        search_parts = [p for p in parts if p.lower() != "standard" and p]
        if search_parts:
            contains_filter = (
                f"armRegionName eq '{region}' "
                f"and serviceName eq 'Virtual Machines' "
                f"and contains(armSkuName, '{search_parts[0]}')"
            )
            items = _fetch_retail_prices_with_filter(contains_filter, currency_code)

    return items


def _fetch_retail_prices_with_filter(
    odata_filter: str,
    currency_code: str = "USD",
) -> list[dict]:
    """Fetch retail price items matching an OData filter."""
    items: list[dict] = []
    url: str | None = RETAIL_PRICES_URL
    params: dict[str, str] | None = {
        "api-version": RETAIL_PRICES_API_VERSION,
        "$filter": odata_filter,
        "currencyCode": currency_code,
    }

    while url:
        resp = None
        for attempt in range(3):
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                try:
                    retry_after = int(resp.headers.get("Retry-After", str(2**attempt)))
                except (TypeError, ValueError):
                    retry_after = 2**attempt
                logger.warning(
                    "Retail Prices 429, retrying in %ss (attempt %s/3)",
                    retry_after,
                    attempt + 1,
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            break

        if resp is None or resp.status_code >= 400:
            if resp is not None:
                resp.raise_for_status()
            break

        data = resp.json()
        items.extend(data.get("Items", []))
        url = data.get("NextPageLink")
        params = None

    return items


def _is_linux(item: dict) -> bool:
    """Return True if the price item is for Linux (non-Windows)."""
    product = (item.get("productName") or "").lower()
    sku = (item.get("skuName") or "").lower()
    return "windows" not in product and "windows" not in sku


def get_sku_pricing_detail(
    region: str,
    sku_name: str,
    currency_code: str = "USD",
) -> dict:
    """Return detailed pricing for a single SKU: PayGo, Spot, RI, SP.

    Returns::

        {
            "skuName": str,
            "region": str,
            "currency": str,
            "paygo": float | None,
            "spot": float | None,
            "ri_1y": float | None,
            "ri_3y": float | None,
            "sp_1y": float | None,
            "sp_3y": float | None,
        }

    All prices are per-hour, Linux only.
    """
    cache_key = f"detail:{region}:{sku_name}:{currency_code}"
    now = time.monotonic()
    cached = _detail_price_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _DETAIL_PRICE_CACHE_TTL:
            return data

    result: dict = {
        "skuName": sku_name,
        "region": region,
        "currency": currency_code,
        "paygo": None,
        "spot": None,
        "ri_1y": None,
        "ri_3y": None,
        "sp_1y": None,
        "sp_3y": None,
    }

    try:
        items = _fetch_all_retail_prices(region, sku_name, currency_code)
    except Exception:
        logger.warning("Failed to fetch detailed prices for %s in %s", sku_name, region)
        return result

    # If the fuzzy fallback found items for a *different* armSkuName,
    # update the result to reflect the actual matched SKU name.
    if items:
        actual_arm_name = items[0].get("armSkuName", sku_name)
        if actual_arm_name != sku_name:
            result["skuName"] = actual_arm_name
            result["matchedFrom"] = sku_name

    for item in items:
        if not _is_linux(item):
            continue

        sku_display = (item.get("skuName") or "").lower()
        if "low priority" in sku_display:
            continue

        price_type = item.get("type", "")
        retail_price = item.get("retailPrice")

        if price_type == "Consumption":
            if "spot" in sku_display:
                if result["spot"] is None or (
                    retail_price is not None and retail_price < result["spot"]
                ):
                    result["spot"] = retail_price
            else:
                if result["paygo"] is None or (
                    retail_price is not None and retail_price < result["paygo"]
                ):
                    result["paygo"] = retail_price

                # Extract Savings Plan data from savingsPlan array
                for sp in item.get("savingsPlan", []):
                    term = sp.get("term", "")
                    sp_price = sp.get("retailPrice")
                    if sp_price is not None:
                        if "1 Year" in term:
                            result["sp_1y"] = sp_price
                        elif "3 Years" in term:
                            result["sp_3y"] = sp_price

        elif price_type == "Reservation":
            reservation_term = item.get("reservationTerm", "")
            if retail_price is not None:
                # RI retailPrice is the total upfront cost for the full term;
                # convert to per-hour: divide by total hours in the term.
                if "1 Year" in reservation_term:
                    result["ri_1y"] = retail_price / 8760  # 365 * 24
                elif "3 Years" in reservation_term:
                    result["ri_3y"] = retail_price / 26280  # 3 * 365 * 24

    _detail_price_cache[cache_key] = (time.monotonic(), result)
    return result


# ---------------------------------------------------------------------------
# Startup preload – warm the discovery cache
# ---------------------------------------------------------------------------


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
