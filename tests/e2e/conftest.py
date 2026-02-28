"""Shared fixtures for end-to-end (Playwright) tests.

Starts a real FastAPI server in a background thread with mocked Azure APIs,
providing deterministic fixture data for UI testing.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import uvicorn

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

TENANTS_RESPONSE = {
    "tenants": [
        {"id": "tid-1", "name": "Test Tenant Alpha", "authenticated": True},
        {"id": "tid-2", "name": "Test Tenant Beta", "authenticated": True},
    ],
    "defaultTenantId": "tid-1",
}

SUBSCRIPTIONS_RESPONSE = [
    {"id": "sub-aaa", "name": "Dev Subscription"},
    {"id": "sub-bbb", "name": "Prod Subscription"},
]

REGIONS_RESPONSE = [
    {"name": "francecentral", "displayName": "France Central"},
    {"name": "westeurope", "displayName": "West Europe"},
    {"name": "eastus", "displayName": "East US"},
]

MAPPINGS_RESPONSE = [
    {
        "subscriptionId": "sub-aaa",
        "region": "francecentral",
        "mappings": [
            {"logicalZone": "1", "physicalZone": "francecentral-az1"},
            {"logicalZone": "2", "physicalZone": "francecentral-az2"},
            {"logicalZone": "3", "physicalZone": "francecentral-az3"},
        ],
    },
    {
        "subscriptionId": "sub-bbb",
        "region": "francecentral",
        "mappings": [
            {"logicalZone": "1", "physicalZone": "francecentral-az2"},
            {"logicalZone": "2", "physicalZone": "francecentral-az3"},
            {"logicalZone": "3", "physicalZone": "francecentral-az1"},
        ],
    },
]


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


COMPUTE_SKUS_RAW = _load_fixture("compute_skus_sample.json")

# Processed SKU format (as returned by azure_api.get_skus)
PROCESSED_SKUS: list[dict] = []
for _raw in COMPUTE_SKUS_RAW["value"]:
    _caps: dict[str, str] = {}
    for _c in _raw.get("capabilities", []):
        if _c["name"] in ("vCPUs", "MemoryGB", "MaxDataDiskCount", "PremiumIO"):
            _caps[_c["name"]] = _c["value"]
    _zones: list[str] = []
    for _li in _raw.get("locationInfo", []):
        _zones = _li.get("zones", [])
        break
    PROCESSED_SKUS.append(
        {
            "name": _raw["name"],
            "tier": _raw.get("tier"),
            "size": _raw.get("size"),
            "family": _raw.get("family"),
            "zones": _zones,
            "restrictions": [],
            "capabilities": _caps,
        }
    )


STRATEGY_RESPONSE = {
    "summary": {
        "workloadName": "e2e-test-workload",
        "strategy": "single_region",
        "totalInstances": 2,
        "regionCount": 1,
        "estimatedHourlyCost": 0.192,
        "currency": "USD",
        "overallConfidence": 78,
        "overallConfidenceLabel": "Medium",
    },
    "businessView": {
        "keyMessage": "Deploy 2 Standard_D2s_v3 instances in francecentral.",
        "justification": [
            "Region francecentral has sufficient capacity.",
            "SKU Standard_D2s_v3 available in zones 1, 2, 3.",
        ],
        "risks": ["Single-region deployment has no geographic redundancy."],
        "mitigations": ["Add a secondary region for DR."],
    },
    "technicalView": {
        "allocations": [
            {
                "region": "francecentral",
                "role": "primary",
                "sku": "Standard_D2s_v3",
                "instanceCount": 2,
                "zones": ["1", "2"],
                "quotaRemaining": 48,
                "spotScore": None,
                "paygoPerHour": 0.096,
                "spotPerHour": 0.019,
                "confidenceScore": 78,
                "confidenceLabel": "Medium",
                "rttFromPrimaryMs": None,
            }
        ],
        "latencyMatrix": {},
        "evaluatedAt": "2025-01-15T12:00:00+00:00",
    },
    "warnings": [],
    "missingInputs": [],
    "errors": [],
    "disclaimer": (
        "This tool is not affiliated with Microsoft. "
        "All capacity, pricing and latency information are indicative "
        "and not a guarantee of deployment success."
    ),
}


# ---------------------------------------------------------------------------
# Server fixture
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def _server_port() -> int:
    return _find_free_port()


@pytest.fixture(scope="session")
def _patched_server(
    _server_port: int,
) -> Generator[str, None, None]:
    """Start a real FastAPI server with mocked Azure API calls."""
    from az_scout import azure_api

    # Patch azure_api module-level functions to return fixture data
    patches = [
        patch.object(azure_api, "preload_discovery"),
        patch.object(azure_api, "list_tenants", return_value=TENANTS_RESPONSE),
        patch.object(azure_api, "list_subscriptions", return_value=SUBSCRIPTIONS_RESPONSE),
        patch.object(azure_api, "list_regions", return_value=REGIONS_RESPONSE),
        patch.object(azure_api, "get_mappings", return_value=MAPPINGS_RESPONSE),
        patch.object(azure_api, "get_skus", return_value=[dict(s) for s in PROCESSED_SKUS]),
        patch.object(azure_api, "enrich_skus_with_quotas"),
        patch.object(azure_api, "enrich_skus_with_prices"),
        patch.object(
            azure_api,
            "get_spot_placement_scores",
            return_value={"scores": {}, "errors": []},
        ),
        patch.object(
            azure_api,
            "get_retail_prices",
            return_value=[],
        ),
        patch.object(
            azure_api,
            "get_sku_pricing_detail",
            return_value={"prices": {}},
        ),
        patch.object(
            azure_api,
            "get_sku_profile",
            return_value=None,
        ),
        patch(
            "az_scout.app.recommend_capacity_strategy",
            side_effect=lambda body: _make_strategy_model(),
        ),
        # Prevent real Azure credential usage
        patch("az_scout.azure_api._auth.credential", new_callable=lambda: MagicMock),
    ]

    for p in patches:
        p.start()

    # We need to import app AFTER patching
    from az_scout.app import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=_server_port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    base_url = f"http://127.0.0.1:{_server_port}"
    for _ in range(50):
        try:
            import httpx

            r = httpx.get(f"{base_url}/api/tenants", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("E2E server did not start in time")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)

    for p in patches:
        p.stop()


def _make_strategy_model():
    """Build a CapacityStrategyResponse from fixture data."""
    from az_scout.models.capacity_strategy import CapacityStrategyResponse

    return CapacityStrategyResponse(**STRATEGY_RESPONSE)


@pytest.fixture(scope="session")
def base_url(_patched_server: str) -> str:
    """Return the base URL for the E2E test server."""
    return _patched_server
