"""Tests for the authentication module."""

from unittest.mock import MagicMock, patch

import pytest


class TestHealthEndpoint:
    """The /health endpoint must be public (no auth required)."""

    def test_health_returns_200_without_token(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestMockMode:
    """/api/* routes should return 200 in AUTH_MODE=mock."""

    def test_api_tenants_returns_200_in_mock_mode(self, client):
        tenants_resp = MagicMock()
        tenants_resp.ok = True
        tenants_resp.json.return_value = {
            "value": [{"tenantId": "tid-1", "displayName": "Test"}],
            "nextLink": None,
        }
        tenants_resp.raise_for_status.return_value = None

        with (
            patch("az_scout.azure_api.requests.get", return_value=tenants_resp),
            patch("az_scout.azure_api._get_default_tenant_id", return_value="tid-1"),
            patch("az_scout.azure_api._check_tenant_auth", return_value=True),
        ):
            resp = client.get("/api/tenants")
        assert resp.status_code == 200


class TestGetCurrentUserMock:
    """get_current_user() returns FakeUser in mock mode."""

    @pytest.mark.anyio
    async def test_returns_fake_user_dict(self):
        from fastapi import Request

        from az_scout.auth.security import get_current_user

        # Build a minimal ASGI scope to create a Request object
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
        request = Request(scope)
        result = await get_current_user(request)
        assert result["oid"] == "local-dev-user"
        assert result["tid"] == "local-dev-tenant"


class TestSettingsValidation:
    """AuthSettings validates required vars for entra mode."""

    def test_entra_mode_requires_tenant_and_client_id(self):
        from az_scout.auth.settings import AuthSettings

        with pytest.raises(Exception, match="AUTH_TENANT_ID"):
            AuthSettings(
                auth_mode="entra",
                auth_tenant_id="",
                auth_client_id="",
                _env_file=None,
            )

    def test_mock_mode_needs_no_azure_vars(self):
        from az_scout.auth.settings import AuthSettings

        s = AuthSettings(
            auth_mode="mock",
            auth_tenant_id="",
            auth_client_id="",
            _env_file=None,
        )
        assert s.auth_mode == "mock"
