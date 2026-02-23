"""Inter-region latency dataset based on Microsoft published statistics.

Source: https://learn.microsoft.com/en-us/azure/networking/azure-network-latency

The dataset is indicative — actual latency depends on network path, time of
day, and workload.  Always validate with in-tenant measurements using tools
like Latte, SockPerf, or Azure Connection Monitor.

Cache TTL: 24 hours (dataset is static, refreshed monthly by Microsoft).
"""

import logging
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static latency matrix (round-trip time in ms, symmetric)
# Source: Azure Network Latency page, approximate median RTT values.
# Pairs not listed return None (unknown).
# ---------------------------------------------------------------------------

_LATENCY_PAIRS: dict[tuple[str, str], int] = {}


def _add(a: str, b: str, rtt: int) -> None:
    """Register a symmetric latency pair."""
    _LATENCY_PAIRS[(a, b)] = rtt
    _LATENCY_PAIRS[(b, a)] = rtt


# --- Europe ---
_add("francecentral", "francesouth", 10)
_add("francecentral", "westeurope", 12)
_add("francecentral", "northeurope", 22)
_add("francecentral", "germanywestcentral", 10)
_add("francecentral", "switzerlandnorth", 9)
_add("francecentral", "swedencentral", 32)
_add("francecentral", "uksouth", 10)
_add("francecentral", "ukwest", 12)
_add("francecentral", "norwayeast", 34)
_add("francecentral", "polandcentral", 24)
_add("francecentral", "italynorth", 12)
_add("francecentral", "spaincentral", 16)

_add("westeurope", "northeurope", 14)
_add("westeurope", "germanywestcentral", 8)
_add("westeurope", "switzerlandnorth", 10)
_add("westeurope", "swedencentral", 30)
_add("westeurope", "uksouth", 8)
_add("westeurope", "ukwest", 10)
_add("westeurope", "norwayeast", 32)
_add("westeurope", "francesouth", 16)
_add("westeurope", "polandcentral", 22)
_add("westeurope", "italynorth", 14)
_add("westeurope", "spaincentral", 20)

_add("northeurope", "uksouth", 12)
_add("northeurope", "ukwest", 14)
_add("northeurope", "swedencentral", 24)
_add("northeurope", "germanywestcentral", 18)
_add("northeurope", "norwayeast", 26)
_add("northeurope", "switzerlandnorth", 22)

_add("germanywestcentral", "switzerlandnorth", 6)
_add("germanywestcentral", "swedencentral", 26)
_add("germanywestcentral", "polandcentral", 14)
_add("germanywestcentral", "italynorth", 12)
_add("germanywestcentral", "norwayeast", 28)

_add("swedencentral", "norwayeast", 12)
_add("swedencentral", "polandcentral", 22)
_add("swedencentral", "switzerlandnorth", 30)

_add("uksouth", "ukwest", 6)
_add("uksouth", "northeurope", 12)

_add("switzerlandnorth", "switzerlandwest", 4)
_add("switzerlandnorth", "italynorth", 8)

_add("norwayeast", "norwaywest", 8)

# --- US ---
_add("eastus", "eastus2", 4)
_add("eastus", "centralus", 20)
_add("eastus", "westus", 62)
_add("eastus", "westus2", 62)
_add("eastus", "westus3", 56)
_add("eastus", "northcentralus", 18)
_add("eastus", "southcentralus", 28)
_add("eastus", "westcentralus", 36)
_add("eastus", "canadacentral", 16)
_add("eastus", "canadaeast", 20)

_add("eastus2", "centralus", 18)
_add("eastus2", "westus", 60)
_add("eastus2", "westus2", 60)
_add("eastus2", "westus3", 54)
_add("eastus2", "northcentralus", 16)
_add("eastus2", "southcentralus", 26)
_add("eastus2", "canadacentral", 18)

_add("centralus", "northcentralus", 6)
_add("centralus", "southcentralus", 16)
_add("centralus", "westcentralus", 14)
_add("centralus", "westus", 44)
_add("centralus", "westus2", 42)
_add("centralus", "westus3", 38)

_add("westus", "westus2", 6)
_add("westus", "westus3", 12)
_add("westus", "northcentralus", 40)
_add("westus", "southcentralus", 36)

_add("westus2", "westus3", 10)
_add("westus2", "northcentralus", 38)
_add("westus2", "southcentralus", 34)

_add("northcentralus", "southcentralus", 22)
_add("northcentralus", "westcentralus", 18)
_add("northcentralus", "canadacentral", 14)

_add("southcentralus", "westcentralus", 20)
_add("southcentralus", "westus3", 30)

_add("canadacentral", "canadaeast", 8)

# --- Asia-Pacific ---
_add("eastasia", "southeastasia", 32)
_add("eastasia", "japaneast", 30)
_add("eastasia", "japanwest", 34)
_add("eastasia", "koreacentral", 28)
_add("eastasia", "koreasouth", 30)
_add("eastasia", "australiaeast", 110)

_add("southeastasia", "australiaeast", 78)
_add("southeastasia", "australiasoutheast", 80)
_add("southeastasia", "japaneast", 60)
_add("southeastasia", "koreacentral", 56)
_add("southeastasia", "centralindia", 50)

_add("japaneast", "japanwest", 8)
_add("japaneast", "koreacentral", 20)

_add("koreacentral", "koreasouth", 6)

_add("australiaeast", "australiasoutheast", 6)
_add("australiaeast", "australiacentral", 4)

_add("centralindia", "southindia", 12)
_add("centralindia", "westindia", 16)

# --- Cross-continental (approximate) ---
_add("eastus", "westeurope", 80)
_add("eastus", "northeurope", 76)
_add("eastus", "uksouth", 74)
_add("eastus", "francecentral", 82)

_add("westus2", "eastasia", 140)
_add("westus2", "japaneast", 100)
_add("westus2", "southeastasia", 158)

_add("westeurope", "eastasia", 180)
_add("westeurope", "southeastasia", 170)
_add("westeurope", "centralindia", 110)

# --- Middle East / Africa ---
_add("uaenorth", "uaecentral", 4)
_add("uaenorth", "westeurope", 100)
_add("uaenorth", "centralindia", 50)

_add("southafricanorth", "southafricawest", 8)
_add("southafricanorth", "westeurope", 140)

# --- South America ---
_add("brazilsouth", "brazilsoutheast", 6)
_add("brazilsouth", "eastus", 120)
_add("brazilsouth", "eastus2", 118)

# Self-latency is always 0
# (handled in get_rtt_ms)

# ---------------------------------------------------------------------------
# Cache for runtime-added pairs (e.g. from future API integration)
# ---------------------------------------------------------------------------
_CACHE_TTL = 86400  # 24 hours
_cache: dict[str, tuple[float, int | None]] = {}


def _cache_key(a: str, b: str) -> str:
    parts = sorted([a.lower(), b.lower()])
    return f"{parts[0]}:{parts[1]}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_rtt_ms(region_a: str, region_b: str) -> int | None:
    """Return indicative round-trip time in milliseconds between two Azure regions.

    Returns ``None`` if the pair is not in the dataset.
    Self-latency (same region) returns 0.

    Source: https://learn.microsoft.com/en-us/azure/networking/azure-network-latency
    """
    a = region_a.lower().strip()
    b = region_b.lower().strip()

    if a == b:
        return 0

    # Check static dataset
    rtt = _LATENCY_PAIRS.get((a, b))
    if rtt is not None:
        return rtt

    # Check runtime cache
    ck = _cache_key(a, b)
    cached = _cache.get(ck)
    if cached is not None:
        ts, val = cached
        if time.monotonic() - ts < _CACHE_TTL:
            return val

    return None


def list_known_pairs() -> list[dict[str, str | int]]:
    """Return all known latency pairs for inspection."""
    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, str | int]] = []
    for (a, b), rtt in _LATENCY_PAIRS.items():
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            pairs.append({"regionA": key[0], "regionB": key[1], "rttMs": rtt})
    return sorted(pairs, key=lambda p: (p["regionA"], p["regionB"]))
