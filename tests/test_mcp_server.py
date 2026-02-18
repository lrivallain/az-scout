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
        with (
            patch("az_mapping.azure_api.get_skus", return_value=mock_data),
            patch("az_mapping.azure_api.get_compute_usages", return_value=[]),
        ):
            content, _ = await mcp.call_tool(
                "get_sku_availability",
                {"region": "eastus", "subscription_id": "sub-1"},
            )

        data = json.loads(content[0].text)
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D2s_v3"
        assert data[0]["capabilities"]["vCPUs"] == "2"
        assert data[0]["quota"]["limit"] is None

    @pytest.mark.anyio()
    async def test_passes_resource_type(self, _mock_credential):
        with (
            patch("az_mapping.azure_api.get_skus", return_value=[]) as mock_fn,
            patch("az_mapping.azure_api.get_compute_usages", return_value=[]),
        ):
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
        with (
            patch("az_mapping.azure_api.get_skus", return_value=[]) as mock_fn,
            patch("az_mapping.azure_api.get_compute_usages", return_value=[]),
        ):
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

    @pytest.mark.anyio()
    async def test_includes_quota_info(self, _mock_credential):
        """SKUs include quota data when usages match the family."""
        mock_skus = [
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
        mock_usages = [
            {
                "currentValue": 4,
                "limit": 50,
                "name": {
                    "value": "standardDSv3Family",
                    "localizedValue": "Standard DSv3 Family vCPUs",
                },
                "unit": "Count",
            }
        ]
        with (
            patch("az_mapping.azure_api.get_skus", return_value=mock_skus),
            patch("az_mapping.azure_api.get_compute_usages", return_value=mock_usages),
        ):
            content, _ = await mcp.call_tool(
                "get_sku_availability",
                {"region": "eastus", "subscription_id": "sub-1"},
            )

        data = json.loads(content[0].text)
        assert data[0]["quota"]["limit"] == 50
        assert data[0]["quota"]["used"] == 4
        assert data[0]["quota"]["remaining"] == 46

    @pytest.mark.anyio()
    async def test_includes_pricing_when_requested(self, _mock_credential):
        """SKUs include pricing data when include_prices is True."""
        mock_skus = [
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
        with (
            patch("az_mapping.azure_api.get_skus", return_value=mock_skus),
            patch("az_mapping.azure_api.get_compute_usages", return_value=[]),
            patch("az_mapping.azure_api.enrich_skus_with_prices") as mock_enrich,
        ):
            content, _ = await mcp.call_tool(
                "get_sku_availability",
                {
                    "region": "eastus",
                    "subscription_id": "sub-1",
                    "include_prices": True,
                    "currency_code": "EUR",
                },
            )

        mock_enrich.assert_called_once_with(mock_skus, "eastus", "EUR")

    @pytest.mark.anyio()
    async def test_includes_confidence_score(self, _mock_credential):
        """SKUs include a deployment confidence score."""
        mock_skus = [
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
        mock_usages = [
            {
                "currentValue": 4,
                "limit": 50,
                "name": {"value": "standardDSv3Family"},
                "unit": "Count",
            }
        ]
        with (
            patch("az_mapping.azure_api.get_skus", return_value=mock_skus),
            patch("az_mapping.azure_api.get_compute_usages", return_value=mock_usages),
        ):
            content, _ = await mcp.call_tool(
                "get_sku_availability",
                {"region": "eastus", "subscription_id": "sub-1"},
            )

        data = json.loads(content[0].text)
        conf = data[0]["confidence"]
        assert "score" in conf
        assert "label" in conf
        assert 0 <= conf["score"] <= 100

    @pytest.mark.anyio()
    async def test_no_pricing_by_default(self, _mock_credential):
        """Pricing enrichment is not called when include_prices is omitted."""
        with (
            patch("az_mapping.azure_api.get_skus", return_value=[]),
            patch("az_mapping.azure_api.get_compute_usages", return_value=[]),
            patch("az_mapping.azure_api.enrich_skus_with_prices") as mock_enrich,
        ):
            _, _ = await mcp.call_tool(
                "get_sku_availability",
                {"region": "eastus", "subscription_id": "sub-1"},
            )

        mock_enrich.assert_not_called()


# ---------------------------------------------------------------------------
# get_spot_scores
# ---------------------------------------------------------------------------


class TestMcpGetSpotScores:
    """Tests for the get_spot_scores MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_spot_scores_json(self, _mock_credential):
        mock_result = {
            "scores": {
                "Standard_D2s_v3": {"1": "High", "2": "Medium"},
                "Standard_D4s_v3": {"1": "Medium", "2": "Low"},
            },
            "errors": [],
        }
        with patch(
            "az_mapping.azure_api.get_spot_placement_scores",
            return_value=mock_result,
        ):
            content, _ = await mcp.call_tool(
                "get_spot_scores",
                {
                    "region": "eastus",
                    "subscription_id": "sub-1",
                    "vm_sizes": ["Standard_D2s_v3", "Standard_D4s_v3"],
                },
            )

        data = json.loads(content[0].text)
        assert data["scores"]["Standard_D2s_v3"] == {"1": "High", "2": "Medium"}
        assert data["scores"]["Standard_D4s_v3"] == {"1": "Medium", "2": "Low"}
        assert data["errors"] == []

    @pytest.mark.anyio()
    async def test_passes_instance_count_and_tenant(self, _mock_credential):
        with patch(
            "az_mapping.azure_api.get_spot_placement_scores",
            return_value={"scores": {}, "errors": []},
        ) as mock_fn:
            _, _ = await mcp.call_tool(
                "get_spot_scores",
                {
                    "region": "westeurope",
                    "subscription_id": "sub-2",
                    "vm_sizes": ["Standard_E4s_v4"],
                    "instance_count": 10,
                    "tenant_id": "tid-x",
                },
            )

        mock_fn.assert_called_once_with(
            "westeurope",
            "sub-2",
            ["Standard_E4s_v4"],
            10,
            "tid-x",
        )


# ---------------------------------------------------------------------------
# get_sku_pricing_detail
# ---------------------------------------------------------------------------


class TestMcpGetSkuPricingDetail:
    """Tests for the get_sku_pricing_detail MCP tool."""

    @pytest.mark.anyio()
    async def test_returns_pricing_detail_json(self, _mock_credential):
        mock_result = {
            "skuName": "Standard_D2s_v5",
            "region": "swedencentral",
            "currency": "USD",
            "paygo": 0.102,
            "spot": 0.019,
            "ri_1y": 0.0602,
            "ri_3y": 0.0377,
            "sp_1y": 0.077,
            "sp_3y": 0.053,
        }
        with patch(
            "az_mapping.azure_api.get_sku_pricing_detail",
            return_value=mock_result,
        ):
            content, _ = await mcp.call_tool(
                "get_sku_pricing_detail",
                {"region": "swedencentral", "sku_name": "Standard_D2s_v5"},
            )

        data = json.loads(content[0].text)
        assert data["skuName"] == "Standard_D2s_v5"
        assert data["paygo"] == 0.102
        assert data["ri_1y"] == 0.0602
        assert data["sp_3y"] == 0.053

    @pytest.mark.anyio()
    async def test_passes_currency_code(self, _mock_credential):
        with patch(
            "az_mapping.azure_api.get_sku_pricing_detail",
            return_value={},
        ) as mock_fn:
            _, _ = await mcp.call_tool(
                "get_sku_pricing_detail",
                {
                    "region": "eastus",
                    "sku_name": "Standard_D4s_v3",
                    "currency_code": "EUR",
                },
            )

        mock_fn.assert_called_once_with("eastus", "Standard_D4s_v3", "EUR")

    @pytest.mark.anyio()
    async def test_includes_profile_when_subscription_provided(self, _mock_credential):
        """Profile is included when subscription_id is provided."""
        mock_pricing = {"skuName": "Standard_D2s_v5", "paygo": 0.1}
        mock_profile = {
            "compute": {"vCPUs": 2, "memoryGB": 8},
            "zones": ["1", "2", "3"],
        }
        with (
            patch(
                "az_mapping.azure_api.get_sku_pricing_detail",
                return_value=mock_pricing,
            ),
            patch(
                "az_mapping.azure_api.get_sku_profile",
                return_value=mock_profile,
            ) as mock_prof,
        ):
            content, _ = await mcp.call_tool(
                "get_sku_pricing_detail",
                {
                    "region": "eastus",
                    "sku_name": "Standard_D2s_v5",
                    "subscription_id": "sub-1",
                    "tenant_id": "tid-x",
                },
            )

        mock_prof.assert_called_once_with("eastus", "sub-1", "Standard_D2s_v5", "tid-x")
        data = json.loads(content[0].text)
        assert "profile" in data
        assert data["profile"]["compute"]["vCPUs"] == 2

    @pytest.mark.anyio()
    async def test_no_profile_without_subscription(self, _mock_credential):
        """Profile is not fetched when subscription_id is omitted."""
        mock_pricing = {"skuName": "Standard_D2s_v5", "paygo": 0.1}
        with (
            patch(
                "az_mapping.azure_api.get_sku_pricing_detail",
                return_value=mock_pricing,
            ),
            patch("az_mapping.azure_api.get_sku_profile") as mock_prof,
        ):
            content, _ = await mcp.call_tool(
                "get_sku_pricing_detail",
                {"region": "eastus", "sku_name": "Standard_D2s_v5"},
            )

        mock_prof.assert_not_called()
        data = json.loads(content[0].text)
        assert "profile" not in data
