"""Shared Azure ARM API helpers.

Provides pure-data functions that both the FastAPI web UI and the MCP server
can call.  Every public function returns plain Python objects (dicts / lists)
– no framework ``Response`` wrappers.

This package re-exports all public names so that existing
``from az_scout.azure_api import X`` and ``from az_scout import azure_api``
imports keep working without changes.
"""

import time as time  # noqa: F401  # re-export for mock patching

import requests as requests  # noqa: F401  # re-export for mock patching

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
