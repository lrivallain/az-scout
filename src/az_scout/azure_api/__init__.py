"""Shared Azure ARM API helpers – **stable plugin API surface**.

Provides pure-data functions that both the FastAPI web UI, the MCP server,
and **plugins** can call.  Every public function returns plain Python objects
(dicts / lists) – no framework ``Response`` wrappers.

Stability guarantee
-------------------
Names listed in ``__all__`` form the **public API** and follow semantic
versioning tracked by :data:`PLUGIN_API_VERSION`.  Breaking changes
(signature removals, incompatible return-type changes) bump the major
version; additive changes bump the minor version.

Names prefixed with ``_`` are **internal** – they are re-exported for
backward compatibility (tests, core modules) but plugins **must not**
rely on them.  They can change without notice.

Plugin compatibility check::

    from az_scout.azure_api import PLUGIN_API_VERSION
    major, minor = (int(x) for x in PLUGIN_API_VERSION.split("."))
    assert major == 1, f"Incompatible azure_api version: {PLUGIN_API_VERSION}"
"""

import time as time  # noqa: F401  # re-export for mock patching

import requests as requests  # noqa: F401  # re-export for mock patching

# -- ARM helpers (public API for plugins) ------------------------------------
from az_scout.azure_api._arm import (  # noqa: F401
    ArmAuthorizationError,
    ArmNotFoundError,
    ArmRequestError,
    arm_get,
    arm_paginate,
    arm_post,
    get_headers,
)

# -- Auth & constants -------------------------------------------------------
from az_scout.azure_api._auth import (  # noqa: F401
    AZURE_API_VERSION,
    AZURE_MGMT_URL,
    _check_tenant_auth,
    _get_default_tenant_id,
    _get_headers,
    _suppress_stderr,
    credential,
)

# -- Caches (exposed for test fixtures) -------------------------------------
from az_scout.azure_api._cache import (  # noqa: F401
    _cache_set,
    _cached,
    _discovery_cache,
)

# -- Pagination --------------------------------------------------------------
from az_scout.azure_api._pagination import _paginate  # noqa: F401

# -- Discovery ---------------------------------------------------------------
from az_scout.azure_api.discovery import (  # noqa: F401
    list_locations,
    list_regions,
    list_subscriptions,
    list_tenants,
    preload_discovery,
)

# -- Pricing -----------------------------------------------------------------
from az_scout.azure_api.pricing import (  # noqa: F401
    RETAIL_PRICES_API_VERSION,
    RETAIL_PRICES_URL,
    _detail_price_cache,
    _price_cache,
    enrich_skus_with_prices,
    get_retail_prices,
    get_sku_pricing_detail,
)

# -- Quotas ------------------------------------------------------------------
from az_scout.azure_api.quotas import (  # noqa: F401
    COMPUTE_API_VERSION,
    _normalize_family,
    _usage_cache,
    enrich_skus_with_quotas,
    get_compute_usages,
)

# -- SKUs --------------------------------------------------------------------
from az_scout.azure_api.skus import (  # noqa: F401
    _parse_capability_value,
    _sku_list_cache,
    _sku_name_matches,
    _sku_profile_cache,
    get_mappings,
    get_sku_profile,
    get_skus,
)

# -- Spot --------------------------------------------------------------------
from az_scout.azure_api.spot import (  # noqa: F401
    SPOT_API_VERSION,
    _spot_cache,
    get_spot_placement_scores,
)

# ---------------------------------------------------------------------------
# API version – bump major for breaking changes, minor for additions.
# ---------------------------------------------------------------------------
PLUGIN_API_VERSION = "1.1"
"""Semantic version of the plugin-facing API surface (``__all__``)."""

# ---------------------------------------------------------------------------
# Public API surface – plugins should only use names listed here.
# ---------------------------------------------------------------------------
__all__ = [
    # Meta
    "PLUGIN_API_VERSION",
    # Constants
    "AZURE_API_VERSION",
    "AZURE_MGMT_URL",
    "COMPUTE_API_VERSION",
    "RETAIL_PRICES_API_VERSION",
    "RETAIL_PRICES_URL",
    "SPOT_API_VERSION",
    # ARM helpers (authentication, retry, pagination)
    "get_headers",
    "arm_get",
    "arm_post",
    "arm_paginate",
    "ArmRequestError",
    "ArmAuthorizationError",
    "ArmNotFoundError",
    # Discovery
    "list_tenants",
    "list_subscriptions",
    "list_regions",
    "list_locations",
    "preload_discovery",
    # Zone mappings
    "get_mappings",
    # SKU catalogue
    "get_skus",
    "get_sku_profile",
    # Enrichment (mutate SKU dicts in-place)
    "enrich_skus_with_prices",
    "enrich_skus_with_quotas",
    # Standalone data fetchers
    "get_retail_prices",
    "get_sku_pricing_detail",
    "get_compute_usages",
    "get_spot_placement_scores",
]
