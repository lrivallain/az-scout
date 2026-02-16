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


def _get_headers(tenant_id: str | None = None) -> dict[str, str]:
    """Return authorization headers using *DefaultAzureCredential*.

    When *tenant_id* is provided the token is scoped to that tenant.
    """
    kwargs: dict[str, str] = {}
    if tenant_id:
        kwargs["tenant_id"] = tenant_id
    token = credential.get_token(f"{AZURE_MGMT_URL}/.default", **kwargs)
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


def list_tenants(tenant_id: str | None = None) -> dict:
    """Return tenants with auth status and the default tenant ID.

    Returns ``{"tenants": [...], "defaultTenantId": ...}``.
    """
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
    return {
        "tenants": sorted(tenants, key=lambda x: x["name"].lower()),
        "defaultTenantId": _get_default_tenant_id(),
    }


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
    return sorted(regions, key=lambda x: x["displayName"])


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

        # Name / family substring filters
        if name_lower and name_lower not in (sku.get("name") or "").lower():
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
