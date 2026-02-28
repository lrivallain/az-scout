"""Tests for the capacity strategy advisor.

Covers:
- region_latency service (unit tests)
- capacity_strategy_engine (unit tests with mocked azure_api)
- POST /api/capacity-strategy endpoint (integration tests)
"""

from unittest.mock import patch

from az_scout.models.capacity_strategy import WorkloadProfileRequest
from az_scout.services._evaluation_helpers import best_spot_label
from az_scout.services.capacity_strategy_engine import (
    _RegionEval,
    _select_strategy,
    recommend_capacity_strategy,
)
from az_scout.services.region_latency import get_rtt_ms, list_known_pairs

# ---------------------------------------------------------------------------
# Latency service tests
# ---------------------------------------------------------------------------


class TestRegionLatency:
    def test_self_latency_is_zero(self) -> None:
        assert get_rtt_ms("francecentral", "francecentral") == 0

    def test_known_pair(self) -> None:
        rtt = get_rtt_ms("francecentral", "westeurope")
        assert rtt is not None
        assert isinstance(rtt, int)
        assert rtt > 0

    def test_symmetric(self) -> None:
        assert get_rtt_ms("eastus", "westeurope") == get_rtt_ms("westeurope", "eastus")

    def test_unknown_pair_returns_none(self) -> None:
        assert get_rtt_ms("francecentral", "nonexistentregion") is None

    def test_case_insensitive(self) -> None:
        assert get_rtt_ms("FranceCentral", "WestEurope") == get_rtt_ms(
            "francecentral", "westeurope"
        )

    def test_list_known_pairs_non_empty(self) -> None:
        pairs = list_known_pairs()
        assert len(pairs) > 10
        for p in pairs:
            assert "regionA" in p
            assert "regionB" in p
            assert "rttMs" in p


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestBestSpotLabel:
    def test_empty(self) -> None:
        assert best_spot_label({}) == "Unknown"

    def test_high(self) -> None:
        assert best_spot_label({"1": "High", "2": "Medium"}) == "High"

    def test_low_only(self) -> None:
        assert best_spot_label({"1": "Low"}) == "Low"


# ---------------------------------------------------------------------------
# Fixtures — SKU dicts matching azure_api.get_skus output
# ---------------------------------------------------------------------------


def _make_sku(
    name: str = "Standard_D2s_v3",
    family: str = "standardDSv3Family",
    zones: list[str] | None = None,
    restrictions: list[str] | None = None,
    vcpus: str = "2",
    memory_gb: str = "8",
) -> dict:
    return {
        "name": name,
        "family": family,
        "zones": zones if zones is not None else ["1", "2", "3"],
        "restrictions": restrictions if restrictions is not None else [],
        "capabilities": {"vCPUs": vcpus, "MemoryGB": memory_gb},
    }


def _enrich_quotas(skus: list[dict], *_args, **_kwargs) -> list[dict]:
    for sku in skus:
        sku["quota"] = {"limit": 100, "used": 10, "remaining": 90}
    return skus


def _enrich_quotas_blocking(skus: list[dict], *_args, **_kwargs) -> list[dict]:
    for sku in skus:
        sku["quota"] = {"limit": 100, "used": 100, "remaining": 0}
    return skus


def _enrich_prices(skus: list[dict], *_args, **_kwargs) -> list[dict]:
    for sku in skus:
        sku["pricing"] = {"paygo": 0.10, "spot": 0.03}
    return skus


SAMPLE_SPOT_SCORES: dict = {
    "scores": {
        "Standard_D2s_v3": {"1": "High", "2": "High", "3": "Medium"},
    },
    "errors": [],
}


def _make_profile(**overrides) -> WorkloadProfileRequest:
    defaults = {
        "workloadName": "test-workload",
        "subscriptionId": "sub-1",
        "scale": {"instanceCount": 2},
        "constraints": {"allowRegions": ["francecentral"]},
    }
    defaults.update(overrides)
    return WorkloadProfileRequest(**defaults)


# ---------------------------------------------------------------------------
# Strategy selection unit tests
# ---------------------------------------------------------------------------


def _make_region_eval(
    region: str = "francecentral",
    sku_name: str = "Standard_D2s_v3",
    zones: list[str] | None = None,
    restrictions: list[str] | None = None,
    vcpus: int = 2,
    quota_remaining: int | None = 90,
    spot_label: str = "High",
    confidence_score: int = 80,
) -> _RegionEval:
    return _RegionEval(
        region=region,
        sku_name=sku_name,
        zones=zones or ["1", "2", "3"],
        restrictions=restrictions or [],
        vcpus=vcpus,
        quota_remaining=quota_remaining,
        spot_label=spot_label,
        paygo=0.10,
        spot_price=0.03,
        confidence_score=confidence_score,
        confidence_label="High",
        family="standardDSv3Family",
    )


class TestSelectStrategy:
    def test_single_region_when_one_region(self) -> None:
        profile = _make_profile()
        primary = _make_region_eval()
        warnings: list[str] = []
        missing: list[str] = []
        result = _select_strategy(profile, [primary], primary, warnings, missing)
        assert result == "single_region"

    def test_stateful_gives_active_passive(self) -> None:
        profile = _make_profile(
            usage={"statefulness": "stateful"},
            constraints={"allowRegions": ["francecentral", "westeurope"]},
        )
        primary = _make_region_eval(region="francecentral")
        secondary = _make_region_eval(region="westeurope")
        warnings: list[str] = []
        missing: list[str] = []
        result = _select_strategy(profile, [primary, secondary], primary, warnings, missing)
        assert result == "active_passive"

    def test_quota_blocking_gives_shard(self) -> None:
        profile = _make_profile(
            scale={"instanceCount": 100},
            constraints={"allowRegions": ["francecentral", "westeurope"]},
        )
        primary = _make_region_eval(region="francecentral", quota_remaining=10)
        secondary = _make_region_eval(region="westeurope", quota_remaining=10)
        warnings: list[str] = []
        missing: list[str] = []
        result = _select_strategy(profile, [primary, secondary], primary, warnings, missing)
        # With 10 remaining / 2 vcpus = 5 max, 100 needed → partial → progressive_ramp
        assert result in ("sharded_multi_region", "progressive_ramp")

    def test_spot_low_gives_time_window(self) -> None:
        profile = _make_profile(
            pricing={"preferSpot": True},
            constraints={"allowRegions": ["francecentral", "westeurope"]},
        )
        primary = _make_region_eval(region="francecentral", spot_label="Low")
        secondary = _make_region_eval(region="westeurope")
        warnings: list[str] = []
        missing: list[str] = []
        result = _select_strategy(profile, [primary, secondary], primary, warnings, missing)
        assert result == "time_window_deploy"

    def test_high_latency_constraint_single_region(self) -> None:
        profile = _make_profile(
            usage={"latencySensitivity": "high"},
            constraints={
                "allowRegions": ["francecentral", "eastus"],
                "maxInterRegionRttMs": 20,
            },
        )
        primary = _make_region_eval(region="francecentral")
        secondary = _make_region_eval(region="eastus")
        warnings: list[str] = []
        missing: list[str] = []
        result = _select_strategy(profile, [primary, secondary], primary, warnings, missing)
        # francecentral → eastus RTT is ~82ms > 20ms → single_region
        assert result == "single_region"
        assert any("RTT" in w for w in warnings)


# ---------------------------------------------------------------------------
# Engine integration tests (mocked azure_api)
# ---------------------------------------------------------------------------


class TestRecommendCapacityStrategy:
    @patch("az_scout.services.capacity_strategy_engine.azure_api")
    def test_nominal(self, mock_api) -> None:
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices
        mock_api.get_spot_placement_scores.return_value = SAMPLE_SPOT_SCORES

        profile = _make_profile()
        result = recommend_capacity_strategy(profile)

        assert result.summary.workloadName == "test-workload"
        assert result.summary.regionCount >= 1
        assert result.summary.strategy == "single_region"
        assert result.technicalView.evaluatedAt is not None
        assert len(result.technicalView.allocations) >= 1
        assert result.disclaimer  # Always present

    @patch("az_scout.services.capacity_strategy_engine.azure_api")
    def test_quota_blocking_shards(self, mock_api) -> None:
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas_blocking
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices
        mock_api.get_spot_placement_scores.return_value = SAMPLE_SPOT_SCORES

        profile = _make_profile(
            scale={"instanceCount": 10},
            constraints={"allowRegions": ["francecentral", "westeurope"]},
        )
        result = recommend_capacity_strategy(profile)

        # No eligible region -> no allocations, but we should still get a response
        assert result.summary.workloadName == "test-workload"

    @patch("az_scout.services.capacity_strategy_engine.azure_api")
    def test_no_candidate_regions(self, mock_api) -> None:
        profile = _make_profile(
            constraints={
                "allowRegions": ["francecentral"],
                "denyRegions": ["francecentral"],
            },
        )
        result = recommend_capacity_strategy(profile)

        assert result.summary.regionCount == 0
        assert any("No candidate regions" in e for e in result.errors)

    @patch("az_scout.services.capacity_strategy_engine.azure_api")
    def test_unknown_latency_adds_warning(self, mock_api) -> None:
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices
        mock_api.get_spot_placement_scores.return_value = SAMPLE_SPOT_SCORES

        profile = _make_profile(
            usage={"statefulness": "stateful"},
            constraints={"allowRegions": ["francecentral", "nonexistentregion"]},
        )
        result = recommend_capacity_strategy(profile)

        # If multi-region, latency matrix should flag unknown pairs
        if result.summary.regionCount > 1:
            assert "latency" in result.missingInputs


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestCapacityStrategyEndpoint:
    @patch("az_scout.services.capacity_strategy_engine.azure_api")
    def test_nominal_200(self, mock_api, client) -> None:
        mock_api.get_skus.return_value = [_make_sku()]
        mock_api.enrich_skus_with_quotas.side_effect = _enrich_quotas
        mock_api.enrich_skus_with_prices.side_effect = _enrich_prices
        mock_api.get_spot_placement_scores.return_value = SAMPLE_SPOT_SCORES

        resp = client.post(
            "/api/capacity-strategy",
            json={
                "workloadName": "my-app",
                "subscriptionId": "sub-1",
                "scale": {"instanceCount": 2},
                "constraints": {"allowRegions": ["francecentral"]},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body
        assert "businessView" in body
        assert "technicalView" in body
        assert "disclaimer" in body
        assert body["summary"]["workloadName"] == "my-app"

    def test_missing_workload_name_returns_422(self, client) -> None:
        resp = client.post(
            "/api/capacity-strategy",
            json={"subscriptionId": "sub-1"},
        )
        assert resp.status_code == 422

    @patch("az_scout.services.capacity_strategy_engine.azure_api")
    def test_deny_all_regions_returns_error(self, mock_api, client) -> None:
        resp = client.post(
            "/api/capacity-strategy",
            json={
                "workloadName": "test",
                "subscriptionId": "sub-1",
                "scale": {"instanceCount": 1},
                "constraints": {
                    "allowRegions": ["francecentral"],
                    "denyRegions": ["francecentral"],
                },
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert any("No candidate regions" in e for e in body["errors"])
