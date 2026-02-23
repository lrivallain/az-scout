"""Signal collector – periodic background data gathering with throttle protection.

Collects SKU-level signals (spot score, pricing, confidence, zones,
restrictions) and persists them to the time-series store.

Key 429 / throttle-protection features:
- TTL-based cache (20 min) with composite key (tenant + region + SKU + count)
- In-flight request deduplication (concurrent callers share one future)
- Concurrency limiter (max 3 parallel ARM calls)
- Exponential back-off with jitter, Retry-After header respected
- Periodic background collector (default every 30 min)

All data produced by this collector is **derived / heuristic**.
It is NOT internal Azure telemetry.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from az_scout import azure_api
from az_scout.services.capacity_confidence import compute_capacity_confidence
from az_scout.services.signal_store import record_signals_batch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache (TTL 20 min)
# ---------------------------------------------------------------------------

_CACHE_TTL = 1200  # 20 minutes
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cache_key(tenant_id: str | None, region: str, sku: str, instance_count: int) -> str:
    return f"{tenant_id or ''}:{region}:{sku}:{instance_count}"


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > _CACHE_TTL:
            del _cache[key]
            return None
        return data


def _cache_set(key: str, data: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), data)


def clear_cache() -> None:
    """Clear the collector cache (for testing)."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# In-flight deduplication
# ---------------------------------------------------------------------------

_inflight: dict[str, Future] = {}  # type: ignore[type-arg]
_inflight_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Concurrency limiter (max 3 parallel ARM calls)
# ---------------------------------------------------------------------------

_semaphore = threading.Semaphore(3)

# ---------------------------------------------------------------------------
# Exponential back-off with jitter
# ---------------------------------------------------------------------------

_MAX_RETRIES = 4
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


def _backoff_delay(attempt: int, retry_after: int | None = None) -> float:
    """Compute delay with exponential back-off + jitter."""
    if retry_after is not None:
        return min(float(retry_after), _MAX_DELAY) + random.uniform(0, 1)
    delay = min(_BASE_DELAY * (2**attempt), _MAX_DELAY)
    return float(delay + random.uniform(0, delay * 0.3))


# ---------------------------------------------------------------------------
# Core collection function
# ---------------------------------------------------------------------------


def collect_sku_signal(
    region: str,
    sku_name: str,
    subscription_id: str,
    tenant_id: str | None = None,
    instance_count: int = 1,
    currency_code: str = "USD",
) -> dict:
    """Collect all signals for a single (region, SKU) pair.

    Returns a dict with spot_score, paygo_price, spot_price,
    confidence_score, zones_supported_count, restrictions_present.
    Uses caching, deduplication and throttle protection.
    """
    key = _cache_key(tenant_id, region, sku_name, instance_count)

    # 1. Check cache
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # 2. In-flight deduplication
    with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None:
            # Wait for the in-flight request to complete
            pass
        else:
            future: Future = Future()  # type: ignore[type-arg]
            _inflight[key] = future
            existing = None

    if existing is not None:
        return dict(existing.result(timeout=120))

    # 3. Actual collection (with concurrency limit)
    try:
        result: dict = _do_collect(
            region, sku_name, subscription_id, tenant_id, instance_count, currency_code
        )
        _cache_set(key, result)
        future.set_result(result)
        return result
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)


def _do_collect(
    region: str,
    sku_name: str,
    subscription_id: str,
    tenant_id: str | None,
    instance_count: int,
    currency_code: str,
) -> dict:
    """Perform the actual ARM calls behind the semaphore."""
    _semaphore.acquire()
    try:
        return _fetch_signals(
            region, sku_name, subscription_id, tenant_id, instance_count, currency_code
        )
    finally:
        _semaphore.release()


def _fetch_signals(
    region: str,
    sku_name: str,
    subscription_id: str,
    tenant_id: str | None,
    instance_count: int,
    currency_code: str,
) -> dict:
    """Fetch all signal components from Azure APIs.

    Signal fields:
    - spot_score: str | None (High / Medium / Low)
    - paygo_price: float | None
    - spot_price: float | None
    - zones_supported_count: int
    - restrictions_present: bool
    - confidence_score: int (0–100)
    """
    # --- SKU profile ---
    spot_score: str | None = None
    zones_supported_count = 0
    restrictions_present = False
    vcpus: int | None = None

    try:
        skus = azure_api.get_skus(
            region, subscription_id, tenant_id, "virtualMachines", name=sku_name
        )
        for s in skus:
            if s.get("name") == sku_name:
                zones_supported_count = len(s.get("zones", []))
                restrictions_present = len(s.get("restrictions", [])) > 0
                try:
                    vcpus = int(s.get("capabilities", {}).get("vCPUs", 0))
                except (TypeError, ValueError):
                    vcpus = None
                break
    except Exception:
        logger.warning("Collector: failed to fetch SKU profile for %s/%s", region, sku_name)

    # --- Spot placement score (with retries) ---
    for attempt in range(_MAX_RETRIES):
        try:
            spot_result = azure_api.get_spot_placement_scores(
                region,
                subscription_id,
                [sku_name],
                instance_count,
                tenant_id,
            )
            sku_scores = spot_result.get("scores", {}).get(sku_name, {})
            if sku_scores:
                # Best score across zones
                rank = {"High": 3, "Medium": 2, "Low": 1}
                best = max(sku_scores.values(), key=lambda s: rank.get(s, 0))
                spot_score = best if best in ("High", "Medium", "Low") else None
            break
        except Exception as exc:
            retry_after = _parse_retry_after(exc)
            if attempt < _MAX_RETRIES - 1:
                delay = _backoff_delay(attempt, retry_after)
                logger.warning(
                    "Collector: spot score attempt %d/%d failed, retry in %.1fs: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "Collector: spot score exhausted retries for %s/%s", region, sku_name
                )

    # --- Pricing ---
    paygo_price: float | None = None
    spot_price: float | None = None
    try:
        prices = azure_api.get_retail_prices(region, currency_code)
        sku_prices = prices.get(sku_name, {})
        paygo_price = sku_prices.get("paygo")
        spot_price = sku_prices.get("spot")
    except Exception:
        logger.warning("Collector: failed to fetch prices for %s/%s", region, sku_name)

    # --- Quota ---
    quota_remaining: int | None = None
    try:
        skus_with_quota = azure_api.get_skus(
            region, subscription_id, tenant_id, "virtualMachines", name=sku_name
        )
        azure_api.enrich_skus_with_quotas(skus_with_quota, region, subscription_id, tenant_id)
        for s in skus_with_quota:
            if s.get("name") == sku_name:
                quota_remaining = s.get("quota", {}).get("remaining")
                break
    except Exception:
        logger.warning("Collector: failed to fetch quota for %s/%s", region, sku_name)

    # --- Confidence score ---
    conf = compute_capacity_confidence(
        vcpus=vcpus,
        zones_supported_count=zones_supported_count,
        restrictions_present=restrictions_present,
        quota_remaining_vcpu=quota_remaining,
        spot_score_label=spot_score,
        paygo_price=paygo_price,
        spot_price=spot_price,
    )

    return {
        "region": region,
        "sku": sku_name,
        "spot_score": spot_score,
        "paygo_price": paygo_price,
        "spot_price": spot_price,
        "zones_supported_count": zones_supported_count,
        "restrictions_present": restrictions_present,
        "confidence_score": conf["score"],
    }


def _parse_retry_after(exc: Exception) -> int | None:
    """Attempt to extract Retry-After from an exception."""
    # requests.HTTPError carries a response
    resp = getattr(exc, "response", None)
    if resp is not None:
        raw = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Periodic background collector
# ---------------------------------------------------------------------------

_collector_thread: threading.Thread | None = None
_collector_stop = threading.Event()
_COLLECTOR_INTERVAL = 1800  # 30 minutes

# Registry of (region, sku, subscription_id, tenant_id) tuples to collect
_collection_targets: list[tuple[str, str, str, str | None]] = []
_targets_lock = threading.Lock()


def register_collection_target(
    region: str,
    sku: str,
    subscription_id: str,
    tenant_id: str | None = None,
) -> None:
    """Register a (region, sku) pair for periodic background collection."""
    target = (region, sku, subscription_id, tenant_id)
    with _targets_lock:
        if target not in _collection_targets:
            _collection_targets.append(target)


def _collector_loop() -> None:
    """Background loop that collects signals for all registered targets."""
    logger.info("Signal collector started (interval=%ds)", _COLLECTOR_INTERVAL)
    while not _collector_stop.wait(timeout=_COLLECTOR_INTERVAL):
        with _targets_lock:
            targets = list(_collection_targets)

        if not targets:
            continue

        logger.info("Collector: collecting signals for %d targets", len(targets))
        batch: list[dict] = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {}
            for region, sku, sub_id, tenant_id in targets:
                f = pool.submit(collect_sku_signal, region, sku, sub_id, tenant_id)
                futures[f] = (region, sku)

            for f in futures:
                try:
                    result = f.result(timeout=120)
                    batch.append(result)
                except Exception:
                    region, sku = futures[f]
                    logger.warning("Collector: failed to collect %s/%s", region, sku)

        if batch:
            try:
                record_signals_batch(batch)
                logger.info("Collector: persisted %d signal snapshots", len(batch))
            except Exception:
                logger.warning("Collector: failed to persist signals", exc_info=True)


def start_collector() -> None:
    """Start the background signal collector thread."""
    global _collector_thread  # noqa: PLW0603
    if _collector_thread is not None and _collector_thread.is_alive():
        return
    _collector_stop.clear()
    _collector_thread = threading.Thread(
        target=_collector_loop,
        daemon=True,
        name="signal-collector",
    )
    _collector_thread.start()


def stop_collector() -> None:
    """Stop the background signal collector thread."""
    _collector_stop.set()
    if _collector_thread is not None:
        _collector_thread.join(timeout=5)
