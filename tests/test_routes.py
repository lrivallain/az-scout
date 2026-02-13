"""Tests for the az-mapping Flask routes."""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestIndex:
    """Tests for the index route."""

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Azure AZ Mapping Viewer" in resp.data


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

        with patch("az_mapping.app.requests.get", return_value=mock_resp):
            resp = client.get("/api/subscriptions")

        assert resp.status_code == 200
        data = resp.get_json()
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

        with patch("az_mapping.app.requests.get", side_effect=[mock_resp1, mock_resp2]):
            resp = client.get("/api/subscriptions")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_returns_500_on_azure_error(self, client):
        with patch("az_mapping.app.requests.get", side_effect=Exception("Azure down")):
            resp = client.get("/api/subscriptions")

        assert resp.status_code == 500
        assert "error" in resp.get_json()


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

        with patch("az_mapping.app.requests.get", return_value=mock_resp):
            resp = client.get("/api/regions?subscriptionId=sub-123")

        assert resp.status_code == 200
        data = resp.get_json()
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

        with patch("az_mapping.app.requests.get", side_effect=[subs_resp, locations_resp]):
            resp = client.get("/api/regions")

        assert resp.status_code == 200

    def test_returns_404_when_no_enabled_subs(self, client):
        subs_resp = MagicMock()
        subs_resp.ok = True
        subs_resp.json.return_value = {"value": [{"subscriptionId": "x", "state": "Disabled"}]}
        subs_resp.raise_for_status.return_value = None

        with patch("az_mapping.app.requests.get", return_value=subs_resp):
            resp = client.get("/api/regions")

        assert resp.status_code == 404


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

        with patch("az_mapping.app.requests.get", return_value=mock_resp):
            resp = client.get("/api/mappings?region=eastus&subscriptions=sub1")

        assert resp.status_code == 200
        data = resp.get_json()
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

        with patch("az_mapping.app.requests.get", return_value=mock_resp):
            resp = client.get("/api/mappings?region=eastus&subscriptions=sub1,sub2")

        assert resp.status_code == 200
        data = resp.get_json()
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

        with patch("az_mapping.app.requests.get", side_effect=[mock_ok, mock_fail]):
            resp = client.get("/api/mappings?region=eastus&subscriptions=sub1,sub2")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        # First sub succeeded
        assert len(data[0]["mappings"]) == 1
        # Second sub has an error
        assert "error" in data[1]
        assert data[1]["mappings"] == []
