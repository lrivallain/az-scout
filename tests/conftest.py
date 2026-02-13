"""Shared test fixtures for az-mapping tests."""

from unittest.mock import MagicMock, patch

import pytest

from az_mapping.app import app


@pytest.fixture()
def client():
    """Create a Flask test client with mocked Azure credentials."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _mock_credential():
    """Prevent real Azure credential calls in every test."""
    mock_token = MagicMock()
    mock_token.token = "fake-token"
    with patch("az_mapping.app.credential") as cred:
        cred.get_token.return_value = mock_token
        yield cred
