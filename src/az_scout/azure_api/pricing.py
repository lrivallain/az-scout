"""Azure Retail Prices API – PayGo, Spot, RI, and Savings Plan pricing."""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
RETAIL_PRICES_API_VERSION = "2023-01-01-preview"
_PRICE_CACHE_TTL = 3600  # 1 hour
_price_cache: dict[str, tuple[float, dict[str, dict]]] = {}
_DETAIL_PRICE_CACHE_TTL = 3600  # 1 hour
_detail_price_cache: dict[str, tuple[float, dict]] = {}


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
# Detailed SKU pricing – PayGo, Spot, RI 1Y/3Y, Savings Plan 1Y/3Y
# ---------------------------------------------------------------------------


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
