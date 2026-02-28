"""Tests for the az-scout FastAPI routes."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestIndex:
    """Tests for the index route."""

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Azure Scout" in resp.content


# ---------------------------------------------------------------------------
# GET /api/tenants
# ---------------------------------------------------------------------------


class TestListTenants:
    """Tests for the /api/tenants endpoint."""

    def test_returns_tenants_sorted_with_default_and_auth(self, client):
        azure_response = {
            "value": [
                {"tenantId": "tid-2", "displayName": "Zulu Tenant"},
                {"tenantId": "tid-1", "displayName": "Alpha Tenant"},
            ],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with (
            patch("az_scout.azure_api.requests.get", return_value=mock_resp),
            patch("az_scout.azure_api.discovery._get_default_tenant_id", return_value="tid-1"),
            patch("az_scout.azure_api.discovery._check_tenant_auth", return_value=True),
        ):
            resp = client.get("/api/tenants")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tenants"]) == 2
        assert data["tenants"][0]["name"] == "Alpha Tenant"
        assert data["tenants"][0]["authenticated"] is True
        assert data["tenants"][1]["id"] == "tid-2"
        assert data["defaultTenantId"] == "tid-1"

    def test_marks_unauthenticated_tenants(self, client):
        azure_response = {
            "value": [
                {"tenantId": "tid-ok", "displayName": "Good Tenant"},
                {"tenantId": "tid-fail", "displayName": "Bad Tenant"},
            ],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        def _auth_side_effect(tid):
            return tid == "tid-ok"

        with (
            patch("az_scout.azure_api.requests.get", return_value=mock_resp),
            patch("az_scout.azure_api.discovery._get_default_tenant_id", return_value="tid-ok"),
            patch("az_scout.azure_api.discovery._check_tenant_auth", side_effect=_auth_side_effect),
        ):
            resp = client.get("/api/tenants")

        assert resp.status_code == 200
        data = resp.json()
        by_id = {t["id"]: t for t in data["tenants"]}
        assert by_id["tid-ok"]["authenticated"] is True
        assert by_id["tid-fail"]["authenticated"] is False

    def test_uses_tenant_id_as_fallback_name(self, client):
        azure_response = {
            "value": [{"tenantId": "tid-no-name"}],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with (
            patch("az_scout.azure_api.requests.get", return_value=mock_resp),
            patch("az_scout.azure_api.discovery._get_default_tenant_id", return_value=None),
            patch("az_scout.azure_api.discovery._check_tenant_auth", return_value=True),
        ):
            resp = client.get("/api/tenants")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tenants"][0]["name"] == "tid-no-name"

    def test_returns_500_on_error(self, client):
        with patch("az_scout.azure_api.requests.get", side_effect=Exception("Azure down")):
            resp = client.get("/api/tenants")

        assert resp.status_code == 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/subscriptions
# ---------------------------------------------------------------------------


class TestListSubscriptions:
    """Tests for the /api/subscriptions endpoint."""

    def test_returns_enabled_subscriptions_sorted(self, client):
        azure_response = {
            "value": [
                {"subscriptionId": "aaa", "displayName": "Zeta Sub", "state": "Enabled"},
                {"subscriptionId": "bbb", "displayName": "Alpha Sub", "state": "Enabled"},
                {"subscriptionId": "ccc", "displayName": "Disabled Sub", "state": "Disabled"},
            ],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/subscriptions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Sorted alphabetically – Alpha before Zeta
        assert data[0]["name"] == "Alpha Sub"
        assert data[1]["name"] == "Zeta Sub"
        # Disabled sub excluded
        assert all(s["id"] != "ccc" for s in data)

    def test_handles_pagination(self, client):
        page1 = {
            "value": [{"subscriptionId": "s1", "displayName": "Sub 1", "state": "Enabled"}],
            "nextLink": "https://management.azure.com/subscriptions?next=2",
        }
        page2 = {
            "value": [{"subscriptionId": "s2", "displayName": "Sub 2", "state": "Enabled"}],
            "nextLink": None,
        }
        mock_resp1 = MagicMock()
        mock_resp1.ok = True
        mock_resp1.json.return_value = page1
        mock_resp1.raise_for_status.return_value = None

        mock_resp2 = MagicMock()
        mock_resp2.ok = True
        mock_resp2.json.return_value = page2
        mock_resp2.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", side_effect=[mock_resp1, mock_resp2]):
            resp = client.get("/api/subscriptions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_returns_500_on_azure_error(self, client):
        with patch("az_scout.azure_api.requests.get", side_effect=Exception("Azure down")):
            resp = client.get("/api/subscriptions")

        assert resp.status_code == 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/regions
# ---------------------------------------------------------------------------


class TestListRegions:
    """Tests for the /api/regions endpoint."""

    def _make_locations_response(self):
        return {
            "value": [
                {
                    "name": "eastus",
                    "displayName": "East US",
                    "availabilityZoneMappings": [
                        {"logicalZone": "1", "physicalZone": "eastus-az1"},
                    ],
                    "metadata": {"regionType": "Physical"},
                },
                {
                    "name": "westus",
                    "displayName": "West US",
                    "metadata": {"regionType": "Physical"},
                    # No AZ mappings → should be excluded
                },
                {
                    "name": "eastus2euap",
                    "displayName": "East US 2 EUAP",
                    "availabilityZoneMappings": [
                        {"logicalZone": "1", "physicalZone": "eastus2euap-az1"},
                    ],
                    "metadata": {"regionType": "Logical"},
                    # Logical region → should be excluded
                },
            ]
        }

    def test_returns_az_regions_with_explicit_sub(self, client):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = self._make_locations_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/regions?subscriptionId=sub-123")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "eastus"

    def test_auto_discovers_subscription(self, client):
        subs_resp = MagicMock()
        subs_resp.ok = True
        subs_resp.json.return_value = {
            "value": [{"subscriptionId": "auto-sub", "state": "Enabled"}]
        }
        subs_resp.raise_for_status.return_value = None

        locations_resp = MagicMock()
        locations_resp.ok = True
        locations_resp.json.return_value = self._make_locations_response()
        locations_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", side_effect=[subs_resp, locations_resp]):
            resp = client.get("/api/regions")

        assert resp.status_code == 200

    def test_returns_404_when_no_enabled_subs(self, client):
        subs_resp = MagicMock()
        subs_resp.ok = True
        subs_resp.json.return_value = {"value": [{"subscriptionId": "x", "state": "Disabled"}]}
        subs_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=subs_resp):
            resp = client.get("/api/regions")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/locations
# ---------------------------------------------------------------------------


class TestListLocations:
    """Tests for the /api/locations endpoint."""

    def _make_locations_response(self):
        return {
            "value": [
                {
                    "name": "eastus",
                    "displayName": "East US",
                    "availabilityZoneMappings": [
                        {"logicalZone": "1", "physicalZone": "eastus-az1"},
                    ],
                    "metadata": {"regionType": "Physical"},
                },
                {
                    "name": "westus",
                    "displayName": "West US",
                    "metadata": {"regionType": "Physical"},
                },
                {
                    "name": "staging",
                    "displayName": "Staging",
                    "metadata": {"regionType": "Logical"},
                },
            ]
        }

    def test_returns_locations_with_explicit_sub(self, client):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = self._make_locations_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/locations?subscriptionId=sub-123")

        assert resp.status_code == 200
        data = resp.json()
        # Both Physical regions returned, Logical excluded
        assert len(data) == 2
        names = [d["name"] for d in data]
        assert "eastus" in names
        assert "westus" in names
        assert "staging" not in names

    def test_auto_discovers_subscription_sorted_by_id(self, client):
        subs_resp = MagicMock()
        subs_resp.ok = True
        subs_resp.json.return_value = {
            "value": [
                {"subscriptionId": "zzz-sub", "state": "Enabled"},
                {"subscriptionId": "aaa-sub", "state": "Enabled"},
            ]
        }
        subs_resp.raise_for_status.return_value = None

        locations_resp = MagicMock()
        locations_resp.ok = True
        locations_resp.json.return_value = self._make_locations_response()
        locations_resp.raise_for_status.return_value = None

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=[subs_resp, locations_resp],
        ) as mock_get:
            resp = client.get("/api/locations")

        assert resp.status_code == 200
        # Verify the first sorted subscription was used
        locations_call_url = mock_get.call_args_list[1][0][0]
        assert "aaa-sub" in locations_call_url

    def test_returns_400_when_no_enabled_subs(self, client):
        subs_resp = MagicMock()
        subs_resp.ok = True
        subs_resp.json.return_value = {"value": [{"subscriptionId": "x", "state": "Disabled"}]}
        subs_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=subs_resp):
            resp = client.get("/api/locations")

        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_returns_502_on_arm_error(self, client):
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=Exception("ARM unreachable"),
        ):
            resp = client.get("/api/locations?subscriptionId=sub-123")

        assert resp.status_code == 502
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/mappings
# ---------------------------------------------------------------------------


class TestGetMappings:
    """Tests for the /api/mappings endpoint."""

    def test_returns_400_without_required_params(self, client):
        resp = client.get("/api/mappings")
        assert resp.status_code == 400

        resp = client.get("/api/mappings?region=eastus")
        assert resp.status_code == 400

        resp = client.get("/api/mappings?subscriptions=sub1")
        assert resp.status_code == 400

    def test_returns_mappings_for_region(self, client):
        azure_response = {
            "value": [
                {
                    "name": "eastus",
                    "availabilityZoneMappings": [
                        {"logicalZone": "2", "physicalZone": "eastus-az3"},
                        {"logicalZone": "1", "physicalZone": "eastus-az1"},
                    ],
                },
                {"name": "westus"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/mappings?region=eastus&subscriptions=sub1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["subscriptionId"] == "sub1"
        # Sorted by logicalZone
        assert data[0]["mappings"][0]["logicalZone"] == "1"
        assert data[0]["mappings"][1]["logicalZone"] == "2"

    def test_handles_multiple_subscriptions(self, client):
        azure_response = {
            "value": [
                {
                    "name": "eastus",
                    "availabilityZoneMappings": [
                        {"logicalZone": "1", "physicalZone": "eastus-az1"},
                    ],
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/mappings?region=eastus&subscriptions=sub1,sub2")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_includes_error_for_failing_subscription(self, client):
        ok_response = {
            "value": [
                {
                    "name": "eastus",
                    "availabilityZoneMappings": [
                        {"logicalZone": "1", "physicalZone": "eastus-az1"},
                    ],
                }
            ]
        }
        mock_ok = MagicMock()
        mock_ok.ok = True
        mock_ok.json.return_value = ok_response
        mock_ok.raise_for_status.return_value = None

        mock_fail = MagicMock()
        mock_fail.raise_for_status.side_effect = Exception("Forbidden")

        with patch("az_scout.azure_api.requests.get", side_effect=[mock_ok, mock_fail]):
            resp = client.get("/api/mappings?region=eastus&subscriptions=sub1,sub2")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # First sub succeeded
        assert len(data[0]["mappings"]) == 1
        # Second sub has an error
        assert "error" in data[1]
        assert data[1]["mappings"] == []


# ---------------------------------------------------------------------------
# GET /api/skus
# ---------------------------------------------------------------------------


class TestGetSkus:
    """Tests for the /api/skus endpoint."""

    def test_returns_400_without_required_params(self, client):
        resp = client.get("/api/skus")
        assert resp.status_code == 400

        resp = client.get("/api/skus?region=eastus")
        assert resp.status_code == 400

        resp = client.get("/api/skus?subscriptionId=sub1")
        assert resp.status_code == 400

    def test_returns_filtered_skus_for_region(self, client):
        # With server-side filtering, API only returns SKUs for the requested region
        azure_response = {
            "value": [
                {
                    "name": "Standard_D2s_v3",
                    "resourceType": "virtualMachines",
                    "tier": "Standard",
                    "size": "D2s_v3",
                    "family": "standardDSv3Family",
                    "locations": ["eastus"],
                    "locationInfo": [
                        {
                            "location": "eastus",
                            "zones": ["1", "2", "3"],
                            "zoneDetails": [],
                        }
                    ],
                    "capabilities": [
                        {"name": "vCPUs", "value": "2"},
                        {"name": "MemoryGB", "value": "8"},
                    ],
                    "restrictions": [],
                },
            ],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D2s_v3"
        assert data[0]["zones"] == ["1", "2", "3"]
        assert data[0]["capabilities"]["vCPUs"] == "2"
        assert data[0]["capabilities"]["MemoryGB"] == "8"
        # zoneDetails should not be in response
        assert "zoneDetails" not in data[0]

    def test_filters_by_resource_type(self, client):
        azure_response = {
            "value": [
                {
                    "name": "Standard_D2s_v3",
                    "resourceType": "virtualMachines",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1"]}],
                    "capabilities": [],
                    "restrictions": [],
                },
                {
                    "name": "Premium_LRS",
                    "resourceType": "disks",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1"]}],
                    "capabilities": [],
                    "restrictions": [],
                },
            ],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        # Only virtualMachines (default) should be returned
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D2s_v3"

    def test_includes_zone_restrictions(self, client):
        azure_response = {
            "value": [
                {
                    "name": "Standard_D2s_v3",
                    "resourceType": "virtualMachines",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1", "2", "3"]}],
                    "capabilities": [],
                    "restrictions": [
                        {
                            "type": "Zone",
                            "restrictionInfo": {"zones": ["3"]},
                        }
                    ],
                }
            ],
            "nextLink": None,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = azure_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["restrictions"] == ["3"]

    def test_returns_500_on_error(self, client):
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=Exception("API error"),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        assert resp.status_code == 500
        data = resp.json()
        assert "error" in data

    # ---------- SKU filter tests ----------

    def _make_multi_sku_response(self):
        """ARM-like response with several SKUs for filter testing."""
        return {
            "value": [
                {
                    "name": "Standard_D2s_v3",
                    "resourceType": "virtualMachines",
                    "tier": "Standard",
                    "size": "D2s_v3",
                    "family": "standardDSv3Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1", "2", "3"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "2"},
                        {"name": "MemoryGB", "value": "8"},
                    ],
                    "restrictions": [],
                },
                {
                    "name": "Standard_D4s_v3",
                    "resourceType": "virtualMachines",
                    "tier": "Standard",
                    "size": "D4s_v3",
                    "family": "standardDSv3Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1", "2"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "4"},
                        {"name": "MemoryGB", "value": "16"},
                    ],
                    "restrictions": [],
                },
                {
                    "name": "Standard_E8s_v4",
                    "resourceType": "virtualMachines",
                    "tier": "Standard",
                    "size": "E8s_v4",
                    "family": "standardESv4Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1", "2", "3"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "8"},
                        {"name": "MemoryGB", "value": "64"},
                    ],
                    "restrictions": [],
                },
            ],
            "nextLink": None,
        }

    def test_filters_by_name(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_multi_sku_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&name=D2s")

        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D2s_v3"

    def test_filters_by_family(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_multi_sku_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&family=ESv4")

        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Standard_E8s_v4"

    def test_filters_by_vcpu_range(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_multi_sku_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&minVcpus=2&maxVcpus=4")

        data = resp.json()
        names = [s["name"] for s in data]
        assert "Standard_D2s_v3" in names
        assert "Standard_D4s_v3" in names
        assert "Standard_E8s_v4" not in names

    def test_filters_by_memory_range(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_multi_sku_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get(
                "/api/skus?region=eastus&subscriptionId=sub1&minMemoryGB=10&maxMemoryGB=32"
            )

        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D4s_v3"

    def test_filters_combined(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_multi_sku_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&family=DSv3&minVcpus=4")

        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Standard_D4s_v3"

    def test_no_filters_returns_all(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_multi_sku_response()
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        data = resp.json()
        assert len(data) == 3


# ---------------------------------------------------------------------------
# GET /api/skus – quota enrichment
# ---------------------------------------------------------------------------


class TestGetSkusQuotas:
    """Tests for quota enrichment in the /api/skus endpoint."""

    def _load_fixture(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text())

    def _mock_get_for(self, sku_response: dict, usages_response: dict):
        """Return a side_effect callback that routes by URL."""

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_response
            elif "/usages" in url:
                resp.json.return_value = usages_response
            else:
                resp.json.return_value = {"value": []}
            return resp

        return _dispatch

    def test_includes_matching_quota_info(self, client):
        """SKUs get quota data when family matches a usage entry."""
        sku_resp = self._load_fixture("compute_skus_sample.json")
        usages_resp = self._load_fixture("compute_usages_francecentral.json")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_get_for(sku_resp, usages_resp),
        ):
            resp = client.get("/api/skus?region=francecentral&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        by_name = {s["name"]: s for s in data}

        # standardDSv3Family → limit 50, used 4, remaining 46
        d2s = by_name["Standard_D2s_v3"]
        assert d2s["quota"]["limit"] == 50
        assert d2s["quota"]["used"] == 4
        assert d2s["quota"]["remaining"] == 46

        # standardESv4Family → limit 100, used 8, remaining 92
        e8s = by_name["Standard_E8s_v4"]
        assert e8s["quota"]["limit"] == 100
        assert e8s["quota"]["used"] == 8
        assert e8s["quota"]["remaining"] == 92

    def test_quota_unknown_when_no_family_match(self, client):
        """SKU is returned with null quota when family has no matching usage."""
        sku_resp = self._load_fixture("compute_skus_sample.json")
        usages_resp = self._load_fixture("compute_usages_francecentral.json")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_get_for(sku_resp, usages_resp),
        ):
            resp = client.get("/api/skus?region=francecentral&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        by_name = {s["name"]: s for s in data}

        # standardFSv2Family has no matching usage entry
        f2s = by_name["Standard_F2s_v2"]
        assert f2s["quota"]["limit"] is None
        assert f2s["quota"]["used"] is None
        assert f2s["quota"]["remaining"] is None

    def test_quota_on_403_returns_skus_with_unknown_quotas(self, client):
        """When usages API returns 403, SKUs are still returned with unknown quotas."""
        sku_resp = self._load_fixture("compute_skus_sample.json")

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
                resp.status_code = 200
            elif "/usages" in url:
                resp.status_code = 403
            else:
                resp.json.return_value = {"value": []}
                resp.status_code = 200
            return resp

        with patch("az_scout.azure_api.requests.get", side_effect=_dispatch):
            resp = client.get("/api/skus?region=francecentral&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        for sku in data:
            assert sku["quota"]["limit"] is None
            assert sku["quota"]["used"] is None
            assert sku["quota"]["remaining"] is None

    def test_quota_on_429_retries_and_succeeds(self, client):
        """When usages API returns 429 once then succeeds, quotas are populated."""
        sku_resp = self._load_fixture("compute_skus_sample.json")
        usages_resp = self._load_fixture("compute_usages_francecentral.json")

        call_count = {"usages": 0}

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
                resp.status_code = 200
            elif "/usages" in url:
                call_count["usages"] += 1
                if call_count["usages"] == 1:
                    resp.status_code = 429
                    resp.headers = {"Retry-After": "0"}
                else:
                    resp.status_code = 200
                    resp.json.return_value = usages_resp
            else:
                resp.json.return_value = {"value": []}
                resp.status_code = 200
            return resp

        with (
            patch("az_scout.azure_api.requests.get", side_effect=_dispatch),
            patch("time.sleep"),
        ):
            resp = client.get("/api/skus?region=francecentral&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        by_name = {s["name"]: s for s in data}
        assert by_name["Standard_D2s_v3"]["quota"]["limit"] == 50
        assert call_count["usages"] == 2


# ---------------------------------------------------------------------------
# POST /api/spot-scores
# ---------------------------------------------------------------------------


class TestSpotScores:
    """Tests for the /api/spot-scores endpoint."""

    def test_returns_scores_for_skus(self, client):
        """Basic success: 3 SKUs → single batch POST returns scores."""
        spot_response = {
            "placementScores": [
                {
                    "sku": "Standard_D2s_v3",
                    "score": "High",
                    "region": "eastus",
                    "availabilityZone": "1",
                },
                {
                    "sku": "Standard_D2s_v3",
                    "score": "Medium",
                    "region": "eastus",
                    "availabilityZone": "2",
                },
                {
                    "sku": "Standard_D4s_v3",
                    "score": "Medium",
                    "region": "eastus",
                    "availabilityZone": "1",
                },
                {
                    "sku": "Standard_D4s_v3",
                    "score": "Low",
                    "region": "eastus",
                    "availabilityZone": "2",
                },
                {
                    "sku": "Standard_E8s_v4",
                    "score": "Low",
                    "region": "eastus",
                    "availabilityZone": "1",
                },
                {
                    "sku": "Standard_E8s_v4",
                    "score": "High",
                    "region": "eastus",
                    "availabilityZone": "2",
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = spot_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.post", return_value=mock_resp):
            resp = client.post(
                "/api/spot-scores",
                json={
                    "region": "eastus",
                    "subscriptionId": "sub-1",
                    "skus": ["Standard_D2s_v3", "Standard_D4s_v3", "Standard_E8s_v4"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["scores"]["Standard_D2s_v3"] == {"1": "High", "2": "Medium"}
        assert data["scores"]["Standard_D4s_v3"] == {"1": "Medium", "2": "Low"}
        assert data["scores"]["Standard_E8s_v4"] == {"1": "Low", "2": "High"}
        assert data["errors"] == []

    def test_400_on_missing_required_fields(self, client):
        """Missing required fields return 422 (Pydantic validation)."""
        resp = client.post("/api/spot-scores", json={})
        assert resp.status_code == 422

        resp = client.post("/api/spot-scores", json={"region": "eastus"})
        assert resp.status_code == 422

    def test_chunking_150_skus(self, client):
        """150 SKUs are split into 2 batches of 100+50."""
        sku_names = [f"Standard_D{i}s_v3" for i in range(150)]

        def _mock_post(url, **kwargs):
            payload = kwargs.get("json", {})
            desired_sizes = payload.get("desiredSizes", [])
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "placementScores": [
                    {
                        "sku": s["sku"],
                        "score": "High",
                        "region": "eastus",
                        "availabilityZone": "1",
                    }
                    for s in desired_sizes
                ]
            }
            return resp

        with (
            patch("az_scout.azure_api.requests.post", side_effect=_mock_post),
            patch("az_scout.azure_api.time.sleep"),
        ):
            resp = client.post(
                "/api/spot-scores",
                json={
                    "region": "eastus",
                    "subscriptionId": "sub-1",
                    "skus": sku_names,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scores"]) == 150
        assert data["errors"] == []

    def test_429_retry_succeeds(self, client):
        """429 on first attempt → retry and succeed."""
        call_count = {"n": 0}

        def _mock_post(url, **kwargs):
            call_count["n"] += 1
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if call_count["n"] == 1:
                resp.status_code = 429
                resp.headers = {"Retry-After": "0"}
            else:
                resp.status_code = 200
                resp.json.return_value = {
                    "placementScores": [
                        {
                            "sku": "Standard_D2s_v3",
                            "score": "High",
                            "region": "eastus",
                            "availabilityZone": "1",
                        }
                    ]
                }
            return resp

        with (
            patch("az_scout.azure_api.requests.post", side_effect=_mock_post),
            patch("az_scout.azure_api.time.sleep"),
        ):
            resp = client.post(
                "/api/spot-scores",
                json={
                    "region": "eastus",
                    "subscriptionId": "sub-1",
                    "skus": ["Standard_D2s_v3"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["scores"]["Standard_D2s_v3"] == {"1": "High"}
        assert call_count["n"] == 2

    def test_403_returns_empty_scores(self, client):
        """403 → empty scores with error message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {}
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.post", return_value=mock_resp):
            resp = client.post(
                "/api/spot-scores",
                json={
                    "region": "eastus",
                    "subscriptionId": "sub-1",
                    "skus": ["Standard_D2s_v3"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["scores"] == {}
        assert len(data["errors"]) == 1
        assert "403" in data["errors"][0]

    def test_404_returns_empty_scores(self, client):
        """404 → empty scores (provider not registered), no crash."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.headers = {}
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.post", return_value=mock_resp):
            resp = client.post(
                "/api/spot-scores",
                json={
                    "region": "eastus",
                    "subscriptionId": "sub-1",
                    "skus": ["Standard_D2s_v3"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["scores"] == {}
        assert len(data["errors"]) == 1
        assert "404" in data["errors"][0]

    def test_cache_returns_cached_result(self, client):
        """Second call with same params hits cache."""
        spot_response = {
            "placementScores": [
                {
                    "sku": "Standard_D2s_v3",
                    "score": "High",
                    "region": "eastus",
                    "availabilityZone": "1",
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = spot_response
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.post", return_value=mock_resp) as mock_post:
            payload = {
                "region": "eastus",
                "subscriptionId": "sub-1",
                "skus": ["Standard_D2s_v3"],
            }
            resp1 = client.post("/api/spot-scores", json=payload)
            resp2 = client.post("/api/spot-scores", json=payload)

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Only one actual POST to Azure
        assert mock_post.call_count == 1

    def test_instance_count_forwarded(self, client):
        """instanceCount parameter is forwarded to the Recommender RP."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"placementScores": []}
        mock_resp.raise_for_status.return_value = None

        with patch("az_scout.azure_api.requests.post", return_value=mock_resp) as mock_post:
            client.post(
                "/api/spot-scores",
                json={
                    "region": "eastus",
                    "subscriptionId": "sub-1",
                    "skus": ["Standard_D2s_v3"],
                    "instanceCount": 5,
                },
            )

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"]["desiredCount"] == 5


# ---------------------------------------------------------------------------
# GET /api/skus – pricing enrichment
# ---------------------------------------------------------------------------


class TestGetSkusPricing:
    """Tests for pricing enrichment in the /api/skus endpoint."""

    def _load_fixture(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text())

    def _sku_response(self):
        return {
            "value": [
                {
                    "name": "Standard_D2s_v3",
                    "resourceType": "virtualMachines",
                    "family": "standardDSv3Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1", "2"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "2"},
                        {"name": "MemoryGB", "value": "8"},
                    ],
                    "restrictions": [],
                },
                {
                    "name": "Standard_D4s_v3",
                    "resourceType": "virtualMachines",
                    "family": "standardDSv3Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1", "2"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "4"},
                        {"name": "MemoryGB", "value": "16"},
                    ],
                    "restrictions": [],
                },
            ],
            "nextLink": None,
        }

    def _mock_dispatch(self, sku_resp, retail_resp):
        """Return a side_effect callback routing by URL."""

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
            elif "/usages" in url:
                resp.json.return_value = {"value": []}
            elif "prices.azure.com" in url:
                resp.json.return_value = retail_resp
            else:
                resp.json.return_value = {"value": []}
            return resp

        return _dispatch

    def test_skus_without_prices_by_default(self, client):
        """SKUs returned without pricing key when includePrices is not set."""
        sku_resp = self._sku_response()

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
            else:
                resp.json.return_value = {"value": []}
            return resp

        with patch("az_scout.azure_api.requests.get", side_effect=_dispatch):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        for sku in data:
            assert "pricing" not in sku

    def test_includes_pricing_when_requested(self, client):
        """includePrices=true adds pricing data to each SKU."""
        sku_resp = self._sku_response()
        retail_resp = self._load_fixture("retail_prices_sample.json")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp, retail_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")

        assert resp.status_code == 200
        data = resp.json()
        by_name = {s["name"]: s for s in data}

        d2s = by_name["Standard_D2s_v3"]
        assert d2s["pricing"]["paygo"] == 0.096
        assert d2s["pricing"]["spot"] == 0.019
        assert d2s["pricing"]["currency"] == "USD"

        d4s = by_name["Standard_D4s_v3"]
        assert d4s["pricing"]["paygo"] == 0.192
        assert d4s["pricing"]["spot"] == 0.038

    def test_pricing_prefers_non_windows_line(self, client):
        """Line selection picks non-Windows (Linux) over Windows price."""
        sku_resp = self._sku_response()
        retail_resp = self._load_fixture("retail_prices_sample.json")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp, retail_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")

        data = resp.json()
        by_name = {s["name"]: s for s in data}
        # Linux (0.096) not Windows (0.144)
        assert by_name["Standard_D2s_v3"]["pricing"]["paygo"] == 0.096

    def test_pricing_with_custom_currency(self, client):
        """currencyCode parameter is forwarded to the retail API."""
        sku_resp = self._sku_response()
        retail_resp = self._load_fixture("retail_prices_sample.json")

        call_urls = []

        def _dispatch(url, **kwargs):
            call_urls.append(url)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            params = kwargs.get("params")
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
            elif "/usages" in url:
                resp.json.return_value = {"value": []}
            elif "prices.azure.com" in url:
                assert params is not None
                assert params["currencyCode"] == "EUR"
                resp.json.return_value = retail_resp
            else:
                resp.json.return_value = {"value": []}
            return resp

        with patch("az_scout.azure_api.requests.get", side_effect=_dispatch):
            resp = client.get(
                "/api/skus?region=eastus&subscriptionId=sub1&includePrices=true&currencyCode=EUR"
            )

        assert resp.status_code == 200
        prices_calls = [u for u in call_urls if "prices.azure.com" in u]
        assert len(prices_calls) >= 1

    def test_pricing_cache_reused(self, client):
        """Second request with same region+currency uses cached prices."""
        sku_resp = self._sku_response()
        retail_resp = self._load_fixture("retail_prices_sample.json")

        call_count = {"prices": 0}

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
            elif "/usages" in url:
                resp.json.return_value = {"value": []}
            elif "prices.azure.com" in url:
                call_count["prices"] += 1
                resp.json.return_value = retail_resp
            else:
                resp.json.return_value = {"value": []}
            return resp

        with patch("az_scout.azure_api.requests.get", side_effect=_dispatch):
            resp1 = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")
            resp2 = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert call_count["prices"] == 1

    def test_pricing_null_when_sku_not_in_retail_api(self, client):
        """SKU with no matching retail price gets null paygo/spot."""
        sku_resp = {
            "value": [
                {
                    "name": "Standard_Exotic_v99",
                    "resourceType": "virtualMachines",
                    "family": "exoticFamily",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "2"},
                        {"name": "MemoryGB", "value": "8"},
                    ],
                    "restrictions": [],
                },
            ],
            "nextLink": None,
        }
        retail_resp = self._load_fixture("retail_prices_sample.json")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp, retail_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["pricing"]["paygo"] is None
        assert data[0]["pricing"]["spot"] is None
        assert data[0]["pricing"]["currency"] == "USD"

    def test_pricing_excludes_low_priority_items(self, client):
        """Low Priority pricing lines are excluded from paygo/spot."""
        sku_resp = {
            "value": [
                {
                    "name": "Standard_HB176rs_v4",
                    "resourceType": "virtualMachines",
                    "family": "standardHBv4Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": ["1"]}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "176"},
                        {"name": "MemoryGB", "value": "768"},
                    ],
                    "restrictions": [],
                },
            ],
            "nextLink": None,
        }
        retail_resp = {
            "Items": [
                {
                    "armSkuName": "Standard_HB176rs_v4",
                    "skuName": "HB176rs v4",
                    "meterName": "HB176rs v4",
                    "retailPrice": 7.2,
                    "currencyCode": "USD",
                    "productName": "Virtual Machines HBv4 Series",
                    "serviceName": "Virtual Machines",
                    "type": "Consumption",
                },
                {
                    "armSkuName": "Standard_HB176rs_v4",
                    "skuName": "HB176rs v4 Low Priority",
                    "meterName": "HB176rs v4 Low Priority",
                    "retailPrice": 1.44,
                    "currencyCode": "USD",
                    "productName": "Virtual Machines HBv4 Series",
                    "serviceName": "Virtual Machines",
                    "type": "Consumption",
                },
                {
                    "armSkuName": "Standard_HB176rs_v4",
                    "skuName": "HB176rs v4 Spot",
                    "meterName": "HB176rs v4 Spot",
                    "retailPrice": 1.44,
                    "currencyCode": "USD",
                    "productName": "Virtual Machines HBv4 Series",
                    "serviceName": "Virtual Machines",
                    "type": "Consumption",
                },
            ],
            "NextPageLink": None,
            "Count": 3,
        }

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp, retail_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")

        assert resp.status_code == 200
        data = resp.json()
        pricing = data[0]["pricing"]
        # PAYGO should be the regular price, not the Low Priority one
        assert pricing["paygo"] == 7.2
        assert pricing["spot"] == 1.44


class TestGetSkuPricingDetail:
    """Tests for GET /api/sku-pricing endpoint."""

    @staticmethod
    def _retail_response(items):
        """Create a mock retail prices response."""
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"Items": items, "NextPageLink": None, "Count": len(items)}
        return mock

    def test_basic_pricing_detail(self, client):
        """Return PayGo, Spot, RI and SP prices for a single SKU."""
        items = [
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.096,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
                "savingsPlan": [
                    {"term": "1 Year", "retailPrice": 0.062},
                    {"term": "3 Years", "retailPrice": 0.039},
                ],
            },
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3 Spot",
                "retailPrice": 0.019,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.055,
                "type": "Reservation",
                "reservationTerm": "1 Year",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.035,
                "type": "Reservation",
                "reservationTerm": "3 Years",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
        ]
        with patch(
            "az_scout.azure_api.requests.get",
            return_value=self._retail_response(items),
        ):
            resp = client.get("/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3")

        assert resp.status_code == 200
        data = resp.json()
        assert data["skuName"] == "Standard_D2s_v3"
        assert data["currency"] == "USD"
        assert data["paygo"] == 0.096
        assert data["spot"] == 0.019
        assert data["ri_1y"] == pytest.approx(0.055 / 8760)
        assert data["ri_3y"] == pytest.approx(0.035 / 26280)
        assert data["sp_1y"] == 0.062
        assert data["sp_3y"] == 0.039

    def test_filters_windows(self, client):
        """Windows items should be filtered out, only Linux returned."""
        items = [
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.096,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.188,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series Windows",
                "serviceName": "Virtual Machines",
            },
        ]
        with patch(
            "az_scout.azure_api.requests.get",
            return_value=self._retail_response(items),
        ):
            resp = client.get("/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3")

        data = resp.json()
        assert data["paygo"] == 0.096  # Linux price, not Windows

    def test_excludes_low_priority(self, client):
        """Low Priority items should be excluded entirely."""
        items = [
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.096,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3 Low Priority",
                "retailPrice": 0.019,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
        ]
        with patch(
            "az_scout.azure_api.requests.get",
            return_value=self._retail_response(items),
        ):
            resp = client.get("/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3")

        data = resp.json()
        assert data["paygo"] == 0.096
        assert data["spot"] is None  # Low Priority is not Spot

    def test_currency_forwarded(self, client):
        """The currencyCode parameter should be passed through."""
        items = [
            {
                "armSkuName": "Standard_D2s_v3",
                "skuName": "D2s v3",
                "retailPrice": 0.088,
                "type": "Consumption",
                "productName": "Virtual Machines DSv3 Series",
                "serviceName": "Virtual Machines",
            },
        ]
        with patch(
            "az_scout.azure_api.requests.get",
            return_value=self._retail_response(items),
        ):
            resp = client.get(
                "/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3&currencyCode=EUR"
            )

        data = resp.json()
        assert data["currency"] == "EUR"
        assert data["paygo"] == 0.088

    def test_missing_sku_returns_nulls(self, client):
        """When SKU has no price items, all prices are null."""
        with patch(
            "az_scout.azure_api.requests.get",
            return_value=self._retail_response([]),
        ):
            resp = client.get("/api/sku-pricing?region=eastus&skuName=Standard_NONEXIST")

        assert resp.status_code == 200
        data = resp.json()
        assert data["paygo"] is None
        assert data["spot"] is None
        assert data["ri_1y"] is None
        assert data["ri_3y"] is None
        assert data["sp_1y"] is None
        assert data["sp_3y"] is None

    def test_missing_required_params(self, client):
        """Missing region or skuName should return 422."""
        resp = client.get("/api/sku-pricing?region=eastus")
        assert resp.status_code == 422

        resp = client.get("/api/sku-pricing?skuName=Standard_D2s_v3")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# VM profile via GET /api/sku-pricing?subscriptionId=...
# ---------------------------------------------------------------------------


class TestSkuProfile:
    """Tests for VM profile enrichment on /api/sku-pricing."""

    _SAMPLE_SKU = {
        "name": "Standard_D2s_v3",
        "resourceType": "virtualMachines",
        "locationInfo": [
            {
                "location": "eastus",
                "zones": ["3", "1", "2"],
            }
        ],
        "capabilities": [
            {"name": "vCPUs", "value": "2"},
            {"name": "MemoryGB", "value": "8"},
            {"name": "PremiumIO", "value": "True"},
            {"name": "AcceleratedNetworkingEnabled", "value": "True"},
            {"name": "MaxDataDiskCount", "value": "4"},
            {"name": "UncachedDiskIOPS", "value": "3200"},
            {"name": "UncachedDiskBytesPerSecond", "value": "48000000"},
            {"name": "EphemeralOSDiskSupported", "value": "False"},
            {"name": "HyperVGenerations", "value": "V1,V2"},
            {"name": "CpuArchitectureType", "value": "x64"},
        ],
        "restrictions": [
            {
                "type": "Zone",
                "reasonCode": "NotAvailableForSubscription",
                "restrictionInfo": {
                    "zones": ["3"],
                    "locations": ["eastus"],
                },
            }
        ],
    }

    _RETAIL_ITEM = {
        "armSkuName": "Standard_D2s_v3",
        "skuName": "D2s v3",
        "retailPrice": 0.096,
        "type": "Consumption",
        "productName": "Virtual Machines DSv3 Series",
        "serviceName": "Virtual Machines",
    }

    @staticmethod
    def _mock_dispatch(arm_sku_value, retail_items):
        """Return a side_effect dispatching ARM SKU vs retail URLs."""

        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = {
                    "value": arm_sku_value,
                    "nextLink": None,
                }
            elif "prices.azure.com" in url:
                resp.json.return_value = {
                    "Items": retail_items,
                    "NextPageLink": None,
                    "Count": len(retail_items),
                }
            else:
                resp.json.return_value = {"value": []}
            return resp

        return _dispatch

    def test_profile_returned_with_subscription_id(self, client):
        """When subscriptionId is provided, the response includes profile."""
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch([self._SAMPLE_SKU], [self._RETAIL_ITEM]),
        ):
            resp = client.get(
                "/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3&subscriptionId=sub-1"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "profile" in data
        profile = data["profile"]
        assert profile["zones"] == ["1", "2", "3"]
        assert profile["capabilities"]["vCPUs"] == 2
        assert profile["capabilities"]["MemoryGB"] == 8
        assert profile["capabilities"]["PremiumIO"] is True
        assert profile["capabilities"]["EphemeralOSDiskSupported"] is False
        assert profile["capabilities"]["HyperVGenerations"] == "V1,V2"
        assert len(profile["restrictions"]) == 1
        assert profile["restrictions"][0]["reasonCode"] == "NotAvailableForSubscription"
        assert profile["restrictions"][0]["zones"] == ["3"]

    def test_no_profile_without_subscription_id(self, client):
        """Without subscriptionId, profile key is absent."""
        retail_resp = MagicMock()
        retail_resp.status_code = 200
        retail_resp.json.return_value = {
            "Items": [self._RETAIL_ITEM],
            "NextPageLink": None,
            "Count": 1,
        }

        with patch(
            "az_scout.azure_api.requests.get",
            return_value=retail_resp,
        ):
            resp = client.get("/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3")

        assert resp.status_code == 200
        data = resp.json()
        assert "profile" not in data

    def test_profile_none_when_sku_not_found(self, client):
        """When SKU doesn't exist, profile key should be absent."""
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch([], [self._RETAIL_ITEM]),
        ):
            resp = client.get(
                "/api/sku-pricing?region=eastus&skuName=Standard_NONEXIST&subscriptionId=sub-1"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "profile" not in data

    def test_profile_no_restrictions(self, client):
        """SKU with empty restrictions list returns empty list."""
        sku = {**self._SAMPLE_SKU, "restrictions": []}
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch([sku], [self._RETAIL_ITEM]),
        ):
            resp = client.get(
                "/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3&subscriptionId=sub-1"
            )

        data = resp.json()
        assert data["profile"]["restrictions"] == []

    def test_profile_tenant_id_forwarded(self, client):
        """tenantId parameter should be forwarded to get_sku_profile."""
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch([self._SAMPLE_SKU], [self._RETAIL_ITEM]),
        ):
            resp = client.get(
                "/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3"
                "&subscriptionId=sub-1&tenantId=tid-1"
            )

        assert resp.status_code == 200
        assert "profile" in resp.json()

    def test_capability_type_parsing(self, client):
        """Capabilities are parsed to correct types (bool, int, float, str)."""
        sku = {
            "name": "Standard_D2s_v3",
            "resourceType": "virtualMachines",
            "locationInfo": [{"location": "eastus", "zones": []}],
            "capabilities": [
                {"name": "BoolTrue", "value": "True"},
                {"name": "BoolFalse", "value": "False"},
                {"name": "IntVal", "value": "42"},
                {"name": "FloatVal", "value": "3.14"},
                {"name": "StrVal", "value": "hello"},
            ],
            "restrictions": [],
        }
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch([sku], [self._RETAIL_ITEM]),
        ):
            resp = client.get(
                "/api/sku-pricing?region=eastus&skuName=Standard_D2s_v3&subscriptionId=sub-1"
            )

        caps = resp.json()["profile"]["capabilities"]
        assert caps["BoolTrue"] is True
        assert caps["BoolFalse"] is False
        assert caps["IntVal"] == 42
        assert isinstance(caps["IntVal"], int)
        assert caps["FloatVal"] == pytest.approx(3.14)
        assert caps["StrVal"] == "hello"


# ---------------------------------------------------------------------------
# Deployment Confidence Score integration tests
# ---------------------------------------------------------------------------


class TestDeploymentConfidence:
    """Tests for confidence score integration in the /api/skus endpoint."""

    def _sku_response(self, *, zones=("1", "2", "3"), restrictions=()):
        return {
            "value": [
                {
                    "name": "Standard_D2s_v3",
                    "resourceType": "virtualMachines",
                    "family": "standardDSv3Family",
                    "locations": ["eastus"],
                    "locationInfo": [{"location": "eastus", "zones": list(zones)}],
                    "capabilities": [
                        {"name": "vCPUs", "value": "2"},
                        {"name": "MemoryGB", "value": "8"},
                    ],
                    "restrictions": list(restrictions),
                },
            ],
            "nextLink": None,
        }

    def _usage_response(self, *, limit=100, current=10):
        return {
            "value": [
                {
                    "name": {"value": "standardDSv3Family"},
                    "limit": limit,
                    "currentValue": current,
                }
            ]
        }

    def _mock_dispatch(self, sku_resp, usage_resp=None, retail_resp=None):
        def _dispatch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/Microsoft.Compute/skus" in url:
                resp.json.return_value = sku_resp
            elif "/usages" in url:
                resp.json.return_value = usage_resp or {"value": []}
            elif "prices.azure.com" in url:
                resp.json.return_value = retail_resp or {"Items": [], "NextPageLink": None}
            else:
                resp.json.return_value = {"value": []}
            return resp

        return _dispatch

    def test_skus_include_confidence_key(self, client):
        """Every SKU in the response must have a confidence dict."""
        sku_resp = self._sku_response()
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        conf = data[0]["confidence"]
        assert "score" in conf
        assert "label" in conf
        assert "breakdown" in conf
        assert "missingSignals" in conf

    def test_confidence_without_prices(self, client):
        """Without pricing, pricePressure should be in the missing list."""
        sku_resp = self._sku_response()
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        conf = resp.json()[0]["confidence"]
        assert "pricePressure" in conf["missingSignals"]
        # Spot is also missing (server-side has no spot scores)
        assert "spot" in conf["missingSignals"]

    def test_confidence_with_prices(self, client):
        """With includePrices=true and pricing data, pricePressure is present."""
        sku_resp = self._sku_response()
        retail_resp = {
            "Items": [
                {
                    "armSkuName": "Standard_D2s_v3",
                    "skuName": "D2s v3",
                    "meterName": "D2s v3",
                    "retailPrice": 0.096,
                    "type": "Consumption",
                    "productName": "Virtual Machines DSv3 Series",
                },
                {
                    "armSkuName": "Standard_D2s_v3",
                    "skuName": "D2s v3 Spot",
                    "meterName": "D2s v3 Spot",
                    "retailPrice": 0.019,
                    "type": "Consumption",
                    "productName": "Virtual Machines DSv3 Series",
                },
            ],
            "NextPageLink": None,
        }
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp, retail_resp=retail_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1&includePrices=true")

        conf = resp.json()[0]["confidence"]
        assert "pricePressure" not in conf["missingSignals"]
        signal_names = [b["name"] for b in conf["breakdown"]["components"]]
        assert "pricePressure" in signal_names

    def test_confidence_with_quota(self, client):
        """Quota enrichment feeds into the confidence score."""
        sku_resp = self._sku_response()
        usage_resp = self._usage_response(limit=100, current=10)
        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(sku_resp, usage_resp=usage_resp),
        ):
            resp = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        conf = resp.json()[0]["confidence"]
        assert "quota" not in conf["missingSignals"]
        signal_names = [b["name"] for b in conf["breakdown"]["components"]]
        assert "quota" in signal_names

    def test_confidence_with_restrictions(self, client):
        """Restrictions lower the confidence score."""
        restricted_resp = self._sku_response(
            restrictions=[{"type": "Zone", "restrictionInfo": {"zones": ["3"]}}]
        )
        clean_resp = self._sku_response()

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(restricted_resp),
        ):
            resp_r = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(clean_resp),
        ):
            resp_c = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        conf_r = resp_r.json()[0]["confidence"]
        conf_c = resp_c.json()[0]["confidence"]
        assert conf_r["score"] < conf_c["score"]

    def test_confidence_zones_reflected(self, client):
        """More zones produce a higher zone breadth signal."""
        one_zone = self._sku_response(zones=("1",))
        three_zones = self._sku_response(zones=("1", "2", "3"))

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(one_zone),
        ):
            resp1 = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        with patch(
            "az_scout.azure_api.requests.get",
            side_effect=self._mock_dispatch(three_zones),
        ):
            resp3 = client.get("/api/skus?region=eastus&subscriptionId=sub1")

        conf1 = resp1.json()[0]["confidence"]
        conf3 = resp3.json()[0]["confidence"]
        assert conf3["score"] >= conf1["score"]


# ---------------------------------------------------------------------------
# GET /api/sku-admission
# ---------------------------------------------------------------------------


class TestSkuAdmission:
    """Tests for the /api/sku-admission endpoint."""

    @staticmethod
    def _mock_sku_response():
        """Return a minimal SKU list response for mocking get_skus."""
        return [
            {
                "name": "Standard_D2s_v3",
                "zones": ["1", "2", "3"],
                "restrictions": [],
                "capabilities": {"vCPUs": "2", "MemoryGB": "8"},
                "quota": {"limit": 100, "used": 10, "remaining": 90},
            }
        ]

    def test_returns_admission_intelligence(self, client):
        """Happy path: all sub-services return data."""
        with (
            patch(
                "az_scout.app.azure_api.get_skus",
                return_value=self._mock_sku_response(),
            ),
            patch(
                "az_scout.app.azure_api.get_spot_placement_scores",
                return_value={"scores": {"Standard_D2s_v3": {"1": "High", "2": "High"}}},
            ),
            patch(
                "az_scout.app.azure_api.get_retail_prices",
                return_value={"Standard_D2s_v3": {"paygo": 0.10, "spot": 0.03}},
            ),
            patch(
                "az_scout.app.compute_volatility",
                return_value={
                    "label": "low",
                    "sampleCount": 10,
                    "timeInLowPercent": 0.0,
                    "spotScoreChangeRatePerDay": 0.0,
                    "priceVolatilityPercent": 0.0,
                },
            ),
            patch(
                "az_scout.app.get_spot_eviction_rate",
                return_value={
                    "evictionRate": "0-5%",
                    "normalizedScore": 0.95,
                    "status": "Lowest band",
                    "disclaimer": "Heuristic",
                },
            ),
        ):
            resp = client.get(
                "/api/sku-admission",
                params={
                    "region": "eastus",
                    "sku": "Standard_D2s_v3",
                    "subscriptionId": "sub-1",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "admissionConfidence" in data
        assert "fragmentationRisk" in data
        assert "volatility24h" in data
        assert "volatility7d" in data
        assert "evictionRate" in data
        ac = data["admissionConfidence"]
        assert "score" in ac
        assert "label" in ac
        assert "breakdown" in ac
        assert "disclaimers" in ac

    def test_returns_400_without_required_params(self, client):
        resp = client.get("/api/sku-admission")
        assert resp.status_code == 422  # FastAPI validation error

    def test_handles_all_services_failing_gracefully(self, client):
        """When SKU lookup, spot, and pricing all fail, route still returns a response."""
        with (
            patch(
                "az_scout.app.azure_api.get_skus",
                side_effect=Exception("SKU lookup failed"),
            ),
            patch(
                "az_scout.app.azure_api.get_spot_placement_scores",
                side_effect=Exception("Spot failed"),
            ),
            patch(
                "az_scout.app.azure_api.get_retail_prices",
                side_effect=Exception("Pricing failed"),
            ),
            patch(
                "az_scout.app.compute_volatility",
                return_value={
                    "label": "unknown",
                    "sampleCount": 0,
                    "timeInLowPercent": None,
                    "spotScoreChangeRatePerDay": None,
                    "priceVolatilityPercent": None,
                },
            ),
            patch(
                "az_scout.app.get_spot_eviction_rate",
                return_value={
                    "evictionRate": None,
                    "normalizedScore": None,
                    "status": "unavailable",
                    "disclaimer": "No data",
                },
            ),
        ):
            resp = client.get(
                "/api/sku-admission",
                params={
                    "region": "eastus",
                    "sku": "Standard_D2s_v3",
                    "subscriptionId": "sub-1",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        # Should still return all keys even with degraded signals
        assert "admissionConfidence" in data
        assert "fragmentationRisk" in data

    def test_tenant_id_forwarded(self, client):
        """tenantId query parameter is forwarded to Azure API calls."""
        with (
            patch(
                "az_scout.app.azure_api.get_skus",
                return_value=[],
            ) as mock_skus,
            patch(
                "az_scout.app.azure_api.get_spot_placement_scores",
                return_value={"scores": {}},
            ),
            patch(
                "az_scout.app.azure_api.get_retail_prices",
                return_value={},
            ),
            patch(
                "az_scout.app.compute_volatility",
                return_value={
                    "label": "unknown",
                    "sampleCount": 0,
                    "timeInLowPercent": None,
                    "spotScoreChangeRatePerDay": None,
                    "priceVolatilityPercent": None,
                },
            ),
            patch(
                "az_scout.app.get_spot_eviction_rate",
                return_value={
                    "evictionRate": None,
                    "normalizedScore": None,
                    "status": "unavailable",
                    "disclaimer": "No data",
                },
            ),
        ):
            resp = client.get(
                "/api/sku-admission",
                params={
                    "region": "westeurope",
                    "sku": "Standard_D2s_v3",
                    "subscriptionId": "sub-1",
                    "tenantId": "tenant-42",
                },
            )

        assert resp.status_code == 200
        mock_skus.assert_called_once_with(
            "westeurope",
            "sub-1",
            "tenant-42",
            "virtualMachines",
            name="Standard_D2s_v3",
        )
