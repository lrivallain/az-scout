"""Tests for OBO authentication flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from az_scout.app import app
from az_scout.azure_api._auth import _get_headers
from az_scout.azure_api._obo import OboTokenError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def obo_client():
    """Test client with OBO enabled (mocked env vars)."""
    with (
        patch("az_scout.azure_api.preload_discovery"),
        patch("az_scout.azure_api._obo.CLIENT_ID", "test-client-id"),
        patch("az_scout.azure_api._obo.CLIENT_SECRET", "test-secret"),
        patch("az_scout.azure_api._obo.TENANT_ID", "test-tenant"),
        TestClient(app, raise_server_exceptions=False) as c,
    ):
        yield c


@pytest.fixture()
def _obo_enabled():
    """Patch OBO env vars for unit tests (no HTTP client needed)."""
    with (
        patch("az_scout.azure_api._obo.CLIENT_ID", "test-client-id"),
        patch("az_scout.azure_api._obo.CLIENT_SECRET", "test-secret"),
        patch("az_scout.azure_api._obo.TENANT_ID", "test-tenant"),
    ):
        yield


# ---------------------------------------------------------------------------
# OBO guard: web requests without token should be blocked
# ---------------------------------------------------------------------------


class TestOboGuard:
    """When OBO is enabled, unauthenticated web requests must return 401."""

    def test_tenants_without_token_returns_401(self, obo_client: TestClient) -> None:
        resp = obo_client.get("/api/tenants")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Authentication required"

    def test_subscriptions_without_token_returns_401(self, obo_client: TestClient) -> None:
        resp = obo_client.get("/api/subscriptions")
        assert resp.status_code == 401

    def test_regions_without_token_returns_401(self, obo_client: TestClient) -> None:
        resp = obo_client.get("/api/regions")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# OBO guard: CLI mode should fall through to DefaultAzureCredential
# ---------------------------------------------------------------------------


class TestCliModeFallback:
    """CLI mode (no middleware context) should use DefaultAzureCredential."""

    @pytest.mark.usefixtures("_obo_enabled")
    def test_get_headers_without_web_context_uses_default_credential(self) -> None:
        """When _in_web_request is False, _get_headers should NOT raise."""
        headers = _get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# OBO exchange success
# ---------------------------------------------------------------------------


class TestOboExchange:
    """Tests for the OBO token exchange via MSAL."""

    @pytest.mark.usefixtures("_obo_enabled")
    def test_successful_exchange(self) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "access_token": "arm-token-123",
            "expires_in": 3600,
        }
        with patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app):
            from az_scout.azure_api._obo import obo_exchange

            headers = obo_exchange("user-token-abc")

        assert headers["Authorization"] == "Bearer arm-token-123"
        mock_app.acquire_token_on_behalf_of.assert_called_once_with(
            user_assertion="user-token-abc",
            scopes=["https://management.azure.com/.default"],
        )

    @pytest.mark.usefixtures("_obo_enabled")
    def test_exchange_failure_raises_obo_error(self) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "Token expired",
        }
        with (
            patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app),
            pytest.raises(OboTokenError, match="invalid_grant"),
        ):
            from az_scout.azure_api._obo import obo_exchange

            obo_exchange("expired-token")

    @pytest.mark.usefixtures("_obo_enabled")
    def test_consent_error_includes_url(self) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS65001: consent required",
        }
        with (
            patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app),
            pytest.raises(OboTokenError, match="adminconsent"),
        ):
            from az_scout.azure_api._obo import obo_exchange

            obo_exchange("user-token", tenant_id="target-tenant")

    @pytest.mark.usefixtures("_obo_enabled")
    def test_mfa_error_returns_claims_challenge(self) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS50076: MFA required",
            "claims": '{"access_token":{"capolids":{"essential":true}}}',
        }
        with patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app):
            from az_scout.azure_api._obo import obo_exchange

            with pytest.raises(OboTokenError) as exc_info:
                obo_exchange("user-token")
            assert exc_info.value.error_code == "claims_challenge"
            assert exc_info.value.claims != ""

    @pytest.mark.usefixtures("_obo_enabled")
    def test_mfa_no_claims_returns_direct_auth(self) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS50076: MFA required",
        }
        with patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app):
            from az_scout.azure_api._obo import obo_exchange

            with pytest.raises(OboTokenError) as exc_info:
                obo_exchange("user-token")
            assert exc_info.value.error_code == "mfa_direct_auth"


# ---------------------------------------------------------------------------
# OBO exception handler (HTTP responses)
# ---------------------------------------------------------------------------


class TestOboExceptionHandler:
    """Tests for the OboTokenError exception handler in app.py."""

    def test_claims_challenge_returns_401_with_claims(self, obo_client: TestClient) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS50076: MFA required",
            "claims": '{"test":"claims"}',
        }
        with patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app):
            resp = obo_client.get(
                "/api/tenants",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"] == "claims_challenge"
        assert data["claims"] == '{"test":"claims"}'

    def test_mfa_direct_auth_returns_401(self, obo_client: TestClient) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS50076: MFA required",
        }
        with patch("az_scout.azure_api._obo._get_msal_app", return_value=mock_app):
            resp = obo_client.get(
                "/api/tenants",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 401
        assert resp.json()["error"] == "mfa_direct_auth"


# ---------------------------------------------------------------------------
# Auth config endpoint
# ---------------------------------------------------------------------------


class TestAuthConfig:
    """Tests for /api/auth/config."""

    def test_obo_disabled_returns_enabled_false(self, client) -> None:
        resp = client.get("/api/auth/config")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_obo_enabled_returns_config(self, obo_client: TestClient) -> None:
        resp = obo_client.get("/api/auth/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
