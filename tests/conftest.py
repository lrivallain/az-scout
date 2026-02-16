"""Shared test fixtures for az-mapping tests."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from az_mapping.app import app


@pytest.fixture()
def client():
    """Create a FastAPI test client with mocked Azure credentials."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _mock_credential():
    """Prevent real Azure credential calls in every test."""
    mock_token = MagicMock()
    mock_token.token = "fake-token"
    with patch("az_mapping.azure_api.credential") as cred:
        cred.get_token.return_value = mock_token
        yield cred
