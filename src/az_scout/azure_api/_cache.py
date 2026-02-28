"""In-memory TTL caches for Azure API responses."""

from __future__ import annotations

import time

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
