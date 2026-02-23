"""Shared test fixtures for az-scout tests."""

import os

# Set mock auth mode before any az_scout imports to avoid Entra ID validation
os.environ.setdefault("AUTH_MODE", "mock")

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from az_scout.app import app


@pytest.fixture()
def client():
    """Create a FastAPI test client with mocked Azure credentials."""
    with patch("az_scout.azure_api.preload_discovery"), TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _mock_credential():
    """Prevent real Azure credential calls in every test."""
    mock_token = MagicMock()
    mock_token.token = "fake-token"
    with patch("az_scout.azure_api.credential") as cred:
        cred.get_token.return_value = mock_token
        yield cred


@pytest.fixture(autouse=True)
def _clear_usage_cache():
    """Clear the compute usages cache between tests."""
    from az_scout.azure_api import _usage_cache

    _usage_cache.clear()
    yield
    _usage_cache.clear()


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """Clear the discovery cache between tests."""
    from az_scout.azure_api import _discovery_cache

    _discovery_cache.clear()
    yield
    _discovery_cache.clear()


@pytest.fixture(autouse=True)
def _clear_spot_cache():
    """Clear the spot placement scores cache between tests."""
    from az_scout.azure_api import _spot_cache

    _spot_cache.clear()
    yield
    _spot_cache.clear()


@pytest.fixture(autouse=True)
def _clear_price_cache():
    """Clear the retail prices cache between tests."""
    from az_scout.azure_api import _detail_price_cache, _price_cache

    _price_cache.clear()
    _detail_price_cache.clear()
    yield
    _price_cache.clear()
    _detail_price_cache.clear()


@pytest.fixture(autouse=True)
def _clear_sku_profile_cache():
    """Clear the SKU profile cache between tests."""
    from az_scout.azure_api import _sku_profile_cache

    _sku_profile_cache.clear()
    yield
    _sku_profile_cache.clear()
