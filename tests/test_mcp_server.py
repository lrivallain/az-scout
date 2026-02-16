"""Tests for the az-mapping MCP server tools."""

import json
from unittest.mock import patch

import pytest

from az_mapping.mcp_server import mcp


@pytest.fixture()
def _mock_credential():
    """Prevent real Azure credential calls."""
    from unittest.mock import MagicMock

    mock_token = MagicMock()
    mock_token.token = "fake-token"
    with patch("az_mapping.azure_api.credential") as cred:
        cred.get_token.return_value = mock_token
        yield cred


# ---------------------------------------------------------------------------
# list_tenants
# ---------------------------------------------------------------------------


class TestMcpListTenants:
    """Tests for the list_tenants MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_tenants_json(self, _mock_credential):
        mock_data = {
            "tenants": [
                {"id": "tid-1", "name": "Alpha", "authenticated": True},
            ],
            "defaultTenantId": "tid-1",
        }
        with patch("az_mapping.azure_api.list_tenants", return_value=mock_data):
            content, _ = await mcp.call_tool("list_tenants", {})

        data = json.loads(content[0].text)
        assert data["defaultTenantId"] == "tid-1"
        assert len(data["tenants"]) == 1


# ---------------------------------------------------------------------------
# list_subscriptions
# ---------------------------------------------------------------------------


class TestMcpListSubscriptions:
    """Tests for the list_subscriptions MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_subscriptions_json(self, _mock_credential):
        mock_data = [{"id": "sub-1", "name": "My Sub"}]
        with patch("az_mapping.azure_api.list_subscriptions", return_value=mock_data):
            content, _ = await mcp.call_tool("list_subscriptions", {})

        data = json.loads(content[0].text)
        assert len(data) == 1
        assert data[0]["id"] == "sub-1"

    @pytest.mark.anyio()
    async def test_passes_tenant_id(self, _mock_credential):
        with patch("az_mapping.azure_api.list_subscriptions", return_value=[]) as mock_fn:
            _, _ = await mcp.call_tool("list_subscriptions", {"tenant_id": "tid-x"})

        mock_fn.assert_called_once_with("tid-x")


# ---------------------------------------------------------------------------
# list_regions
# ---------------------------------------------------------------------------


class TestMcpListRegions:
    """Tests for the list_regions MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_regions_json(self, _mock_credential):
        mock_data = [{"name": "eastus", "displayName": "East US"}]
        with patch("az_mapping.azure_api.list_regions", return_value=mock_data):
            content, _ = await mcp.call_tool("list_regions", {})

        data = json.loads(content[0].text)
        assert data[0]["name"] == "eastus"


# ---------------------------------------------------------------------------
# get_zone_mappings
# ---------------------------------------------------------------------------


class TestMcpGetZoneMappings:
    """Tests for the get_zone_mappings MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_mappings_json(self, _mock_credential):
        mock_data = [
            {
                "subscriptionId": "sub-1",
                "region": "eastus",
                "mappings": [
                    {"logicalZone": "1", "physicalZone": "eastus-az1"},
                ],
            }
        ]
        with patch("az_mapping.azure_api.get_mappings", return_value=mock_data):
            content, _ = await mcp.call_tool(
                "get_zone_mappings",
                {"region": "eastus", "subscription_ids": ["sub-1"]},
            )

        data = json.loads(content[0].text)
        assert len(data) == 1
        assert data[0]["mappings"][0]["physicalZone"] == "eastus-az1"


# ---------------------------------------------------------------------------
# get_sku_availability
# ---------------------------------------------------------------------------


class TestMcpGetSkuAvailability:
    """Tests for the get_sku_availability MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_skus_json(self, _mock_credential):
        mock_data = [
            {
                "name": "Standard_D2s_v3",
                "tier": "Standard",
                "size": "D2s_v3",
                "family": "standardDSv3Family",
                "zones": ["1", "2", "3"],
                "restrictions": [],
                "capabilities": {"vCPUs": "2", "MemoryGB": "8"},
            }
        ]
        with patch("az_mapping.azure_api.get_skus", return_value=mock_data):
            content, _ = await mcp.call_tool(
                "get_sku_availability",
                {"region": "eastus", "subscription_id": "sub-1"},
            )

        data = json.loads(content[0].text)
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D2s_v3"
        assert data[0]["capabilities"]["vCPUs"] == "2"

    @pytest.mark.anyio()
    async def test_passes_resource_type(self, _mock_credential):
        with patch("az_mapping.azure_api.get_skus", return_value=[]) as mock_fn:
            _, _ = await mcp.call_tool(
                "get_sku_availability",
                {
                    "region": "eastus",
                    "subscription_id": "sub-1",
                    "resource_type": "disks",
                },
            )

        mock_fn.assert_called_once_with(
            "eastus",
            "sub-1",
            None,
            "disks",
            name=None,
            family=None,
            min_vcpus=None,
            max_vcpus=None,
            min_memory_gb=None,
            max_memory_gb=None,
        )

    @pytest.mark.anyio()
    async def test_passes_sku_filters(self, _mock_credential):
        with patch("az_mapping.azure_api.get_skus", return_value=[]) as mock_fn:
            _, _ = await mcp.call_tool(
                "get_sku_availability",
                {
                    "region": "eastus",
                    "subscription_id": "sub-1",
                    "name": "D2s",
                    "family": "DSv3",
                    "min_vcpus": 2,
                    "max_vcpus": 8,
                    "min_memory_gb": 4.0,
                    "max_memory_gb": 32.0,
                },
            )

        mock_fn.assert_called_once_with(
            "eastus",
            "sub-1",
            None,
            "virtualMachines",
            name="D2s",
            family="DSv3",
            min_vcpus=2,
            max_vcpus=8,
            min_memory_gb=4.0,
            max_memory_gb=32.0,
        )
