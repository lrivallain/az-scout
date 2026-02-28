"""Tests for the deployment plan feature.

Covers:
- derive_requirements (intent parser, unit tests)
- plan_deployment (decision engine, unit tests with mocked azure_api)
- POST /api/deployment-plan endpoint (integration tests)
"""

from unittest.mock import patch

from az_scout.models.deployment_plan import (
    DeploymentIntentRequest,
    PricingPreference,
    RegionConstraints,
    ScaleRequirement,
    SkuConstraints,
    TimingPreference,
)
from az_scout.services._evaluation_helpers import is_gpu_family
from az_scout.services.deployment_planner import (
    _ranking_key,
    plan_deployment,
)
from az_scout.services.intent_parser import derive_requirements

# ---------------------------------------------------------------------------
# Fixtures — sample SKU dicts as returned by azure_api.get_skus (processed)
# ---------------------------------------------------------------------------


def _make_sku(
    name: str = "Standard_D2s_v3",
    family: str = "standardDSv3Family",
    zones: list[str] | None = None,
    restrictions: list[str] | None = None,
    vcpus: str = "2",
    memory_gb: str = "8",
) -> dict:
    """Build a processed SKU dict matching azure_api.get_skus output."""
    return {
        "name": name,
        "family": family,
        "zones": zones if zones is not None else ["1", "2", "3"],
        "restrictions": restrictions if restrictions is not None else [],
        "capabilities": {"vCPUs": vcpus, "MemoryGB": memory_gb},
    }


def _make_sku_list() -> list[dict]:
    """Three processed SKUs for francecentral."""
    return [
        _make_sku("Standard_D2s_v3", "standardDSv3Family", ["1", "2", "3"], [], "2", "8"),
        _make_sku("Standard_E8s_v4", "standardESv4Family", ["1", "2", "3"], [], "8", "64"),
        _make_sku("Standard_F2s_v2", "standardFSv2Family", ["1", "2"], [], "2", "4"),
    ]


SAMPLE_SPOT_SCORES: dict = {
    "scores": {
        "Standard_D2s_v3": {"1": "High", "2": "High", "3": "Medium"},
        "Standard_E8s_v4": {"1": "Medium", "2": "Medium", "3": "Low"},
        "Standard_F2s_v2": {"1": "High", "2": "High"},
    },
    "errors": [],
}


# ---------------------------------------------------------------------------
# Intent parser tests
# ---------------------------------------------------------------------------


class TestDeriveRequirements:
    """Tests for services.intent_parser.derive_requirements."""

    def test_minimal_intent(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1", scale=ScaleRequirement(instanceCount=4)
        )
        req = derive_requirements(intent)
        assert req.minZones == 1
        assert req.requiresSpotScore is False
        assert req.requiresQuotaCheck is True
        assert req.requiresPriceCheck is False

    def test_zonal_implies_min_zones_3(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            skuConstraints=SkuConstraints(requireZonal=True),
            scale=ScaleRequirement(instanceCount=2),
        )
        req = derive_requirements(intent)
        assert req.minZones == 3

    def test_prefer_spot_implies_spot_and_price(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            scale=ScaleRequirement(instanceCount=1),
            pricing=PricingPreference(preferSpot=True),
        )
        req = derive_requirements(intent)
        assert req.requiresSpotScore is True
        assert req.requiresPriceCheck is True

    def test_urgency_now_implies_spot(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            scale=ScaleRequirement(instanceCount=1),
            timing=TimingPreference(urgency="now"),
        )
        req = derive_requirements(intent)
        assert req.requiresSpotScore is True

    def test_max_budget_implies_price_check(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            scale=ScaleRequirement(instanceCount=1),
            pricing=PricingPreference(maxHourlyBudget=5.0),
        )
        req = derive_requirements(intent)
        assert req.requiresPriceCheck is True

    def test_eur_currency_implies_price_check(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            scale=ScaleRequirement(instanceCount=1),
            pricing=PricingPreference(currencyCode="EUR"),
        )
        req = derive_requirements(intent)
        assert req.requiresPriceCheck is True

    def test_zero_instances_no_quota_check(self) -> None:
        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            scale=ScaleRequirement(instanceCount=0),
        )
        req = derive_requirements(intent)
        assert req.requiresQuotaCheck is False


# ---------------------------------------------------------------------------
# GPU family detection
# ---------------------------------------------------------------------------


class TestIsGpuFamily:
    def test_nc_family(self) -> None:
        assert is_gpu_family("standardNCv3Family") is True

    def test_nd_family(self) -> None:
        assert is_gpu_family("standardNDv2Family") is True

    def test_nv_family(self) -> None:
        assert is_gpu_family("standardNVv4Family") is True

    def test_hb_family(self) -> None:
        assert is_gpu_family("standardHBv3Family") is True

    def test_hc_family(self) -> None:
        assert is_gpu_family("standardHCFamily") is True

    def test_non_gpu(self) -> None:
        assert is_gpu_family("standardDSv3Family") is False
        assert is_gpu_family("standardESv4Family") is False


# ---------------------------------------------------------------------------
# Planner unit tests (mocked azure_api)
# ---------------------------------------------------------------------------


def _enrich_quotas(skus: list[dict], *_args, **_kwargs) -> list[dict]:
    """Mock side-effect: add quota data in-place."""
    for sku in skus:
        vcpu = int(sku.get("capabilities", {}).get("vCPUs", "0"))
        sku["quota"] = {"limit": 100, "used": 10, "remaining": 90 * vcpu}
    return skus


def _enrich_quotas_blocking(skus: list[dict], *_args, **_kwargs) -> list[dict]:
    """Mock side-effect: quota remaining is 0 (blocking)."""
    for sku in skus:
        sku["quota"] = {"limit": 100, "used": 100, "remaining": 0}
    return skus


def _enrich_prices(skus: list[dict], *_args, **_kwargs) -> list[dict]:
    """Mock side-effect: add pricing data in-place."""
    for sku in skus:
        sku["pricing"] = {"paygo": 0.10, "spot": 0.03}
    return skus


class TestPlanDeployment:
    """Integration-level tests for plan_deployment with mocked Azure API."""

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_nominal_case(self, mock_api) -> None:
        """Nominal: quota OK, zones OK, spot High -> coherent recommendation."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = _make_sku_list()
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices
        mock_api.get_spot_placement_scores.return_value = SAMPLE_SPOT_SCORES

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            scale=ScaleRequirement(instanceCount=2),
            pricing=PricingPreference(preferSpot=True),
        )

        result = plan_deployment(intent)

        assert result.summary.recommendedRegion == "francecentral"
        assert result.summary.recommendedSku is not None
        assert result.summary.riskLevel in ("low", "medium", "high")
        assert result.summary.confidenceScore is not None
        assert len(result.technicalView.evaluation.perRegionResults) >= 1
        # The spot warning is always present
        assert any("Spot" in w for w in result.warnings)

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_quota_blocking_excludes_combo(self, mock_api) -> None:
        """When quota=0, combos should be marked ineligible."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas_blocking

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            scale=ScaleRequirement(instanceCount=4),
        )

        result = plan_deployment(intent)

        assert result.summary.recommendedSku is None
        assert result.summary.riskLevel == "high"
        # No eligible combo -> business view should mention no eligible
        assert "No eligible" in result.businessView.keyMessage

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_restrictions_mark_ineligible(self, mock_api) -> None:
        """SKU with restrictions present should be ineligible."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        restricted_sku = _make_sku(restrictions=["3"])
        mock_api.get_skus.return_value = [restricted_sku]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            scale=ScaleRequirement(instanceCount=1),
        )

        result = plan_deployment(intent)

        assert result.summary.recommendedSku is None

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_spot_missing_adds_warning(self, mock_api) -> None:
        """When spot scores are not required, a warning is emitted."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            scale=ScaleRequirement(instanceCount=1),
        )

        result = plan_deployment(intent)

        assert any("Spot scores were not evaluated" in w for w in result.warnings)

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_max_hourly_budget_exceeded(self, mock_api) -> None:
        """Combos exceeding maxHourlyBudget are ineligible."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            scale=ScaleRequirement(instanceCount=10),
            pricing=PricingPreference(maxHourlyBudget=0.01),
        )

        result = plan_deployment(intent)

        assert result.summary.recommendedSku is None
        assert "No eligible" in result.businessView.keyMessage

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_deny_regions_excluded(self, mock_api) -> None:
        """Regions in denyRegions should not be evaluated."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(
                allowRegions=["francecentral", "westeurope"],
                denyRegions=["westeurope"],
            ),
            scale=ScaleRequirement(instanceCount=1),
        )

        result = plan_deployment(intent)

        evaluated = result.technicalView.evaluation.regionsEvaluated
        assert "westeurope" not in evaluated
        assert "francecentral" in evaluated

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_data_residency_fr(self, mock_api) -> None:
        """dataResidency=FR limits candidates to France regions."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(dataResidency="FR"),
            scale=ScaleRequirement(instanceCount=1),
        )

        result = plan_deployment(intent)

        evaluated = result.technicalView.evaluation.regionsEvaluated
        assert set(evaluated).issubset({"francecentral", "francesouth"})

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_ranking_prefers_eligible_then_confidence(self, mock_api) -> None:
        """Eligible combos should rank above ineligible ones."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        good_sku = _make_sku("Standard_D2s_v3", zones=["1", "2", "3"])
        bad_sku = _make_sku("Standard_F2s_v2", zones=["1", "2"])  # only 2 zones
        mock_api.get_skus.return_value = [bad_sku, good_sku]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            skuConstraints=SkuConstraints(requireZonal=True),  # minZones=3
            scale=ScaleRequirement(instanceCount=1),
        )

        result = plan_deployment(intent)

        # Only Standard_D2s_v3 has 3 zones, so it should be recommended
        assert result.summary.recommendedSku == "Standard_D2s_v3"

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_preferred_skus_filter(self, mock_api) -> None:
        """preferredSkus should filter to only those SKUs."""
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = _make_sku_list()
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas

        intent = DeploymentIntentRequest(
            subscriptionId="sub-1",
            regionConstraints=RegionConstraints(allowRegions=["francecentral"]),
            skuConstraints=SkuConstraints(preferredSkus=["Standard_E8s_v4"]),
            scale=ScaleRequirement(instanceCount=1),
        )

        result = plan_deployment(intent)

        # Only the preferred SKU should appear in results
        per_region = result.technicalView.evaluation.perRegionResults
        sku_names = {e.sku for e in per_region}
        assert sku_names == {"Standard_E8s_v4"}


# ---------------------------------------------------------------------------
# Ranking key tests
# ---------------------------------------------------------------------------


class TestRankingKey:
    def test_eligible_before_ineligible(self) -> None:
        from az_scout.models.deployment_plan import (
            ConfidenceEvaluation,
            RegionSkuEvaluation,
            VerdictEvaluation,
        )

        eligible = RegionSkuEvaluation(
            region="eastus",
            sku="A",
            instanceCount=1,
            zonesSupportedCount=3,
            restrictionsPresent=False,
            confidence=ConfidenceEvaluation(score=50),
            verdict=VerdictEvaluation(eligible=True),
        )
        ineligible = RegionSkuEvaluation(
            region="eastus",
            sku="B",
            instanceCount=1,
            zonesSupportedCount=3,
            restrictionsPresent=False,
            confidence=ConfidenceEvaluation(score=90),
            verdict=VerdictEvaluation(eligible=False, reasonCodes=["QuotaBlocking"]),
        )
        assert _ranking_key(eligible, False) < _ranking_key(ineligible, False)

    def test_higher_confidence_wins(self) -> None:
        from az_scout.models.deployment_plan import (
            ConfidenceEvaluation,
            RegionSkuEvaluation,
            VerdictEvaluation,
        )

        hi = RegionSkuEvaluation(
            region="eastus",
            sku="A",
            instanceCount=1,
            zonesSupportedCount=3,
            restrictionsPresent=False,
            confidence=ConfidenceEvaluation(score=90),
            verdict=VerdictEvaluation(eligible=True),
        )
        lo = RegionSkuEvaluation(
            region="eastus",
            sku="B",
            instanceCount=1,
            zonesSupportedCount=3,
            restrictionsPresent=False,
            confidence=ConfidenceEvaluation(score=50),
            verdict=VerdictEvaluation(eligible=True),
        )
        assert _ranking_key(hi, False) < _ranking_key(lo, False)


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestDeploymentPlanEndpoint:
    """Integration tests for POST /api/deployment-plan."""

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_nominal_200(self, mock_api, client) -> None:
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = _make_sku_list()
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices
        mock_api.get_spot_placement_scores.return_value = SAMPLE_SPOT_SCORES

        resp = client.post(
            "/api/deployment-plan",
            json={
                "subscriptionId": "sub-1",
                "regionConstraints": {"allowRegions": ["francecentral"]},
                "scale": {"instanceCount": 2},
                "pricing": {"preferSpot": True},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body
        assert "businessView" in body
        assert "technicalView" in body
        assert body["summary"]["recommendedRegion"] == "francecentral"
        assert body["summary"]["recommendedSku"] is not None

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_quota_blocking_200(self, mock_api, client) -> None:
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas_blocking

        resp = client.post(
            "/api/deployment-plan",
            json={
                "subscriptionId": "sub-1",
                "regionConstraints": {"allowRegions": ["francecentral"]},
                "scale": {"instanceCount": 10},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["recommendedSku"] is None
        assert body["summary"]["riskLevel"] == "high"

    def test_missing_scale_returns_422(self, client) -> None:
        resp = client.post(
            "/api/deployment-plan",
            json={"subscriptionId": "sub-1"},
        )
        assert resp.status_code == 422

    @patch("az_scout.services.deployment_planner.azure_api")
    def test_empty_regions_returns_error(self, mock_api, client) -> None:
        mock_api.COMPUTE_API_VERSION = "2024-11-01"
        mock_api.SPOT_API_VERSION = "2025-06-05"
        mock_api.AZURE_API_VERSION = "2022-12-01"

        resp = client.post(
            "/api/deployment-plan",
            json={
                "subscriptionId": "sub-1",
                "regionConstraints": {
                    "allowRegions": ["francecentral"],
                    "denyRegions": ["francecentral"],
                },
                "scale": {"instanceCount": 1},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert any("No candidate regions" in e for e in body["errors"])
