"""Tests for Admission Intelligence services.

Covers:
- Fragmentation scoring
- Volatility calculation
- Eviction rate mapping
- Admission confidence weighted renormalisation
- Signal collector 429 retry/backoff
- MCP tool outputs (breakdown + disclaimers)

No real network calls are made.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from az_scout.mcp_server import mcp
from az_scout.services.admission_confidence import (
    ADMISSION_WEIGHTS,
    compute_admission_confidence,
)
from az_scout.services.eviction_rate import _map_eviction_rate
from az_scout.services.fragmentation import (
    estimate_fragmentation_risk,
    fragmentation_to_normalized,
)
from az_scout.services.signal_collector import (
    _backoff_delay,
    clear_cache,
    collect_sku_signal,
)
from az_scout.services.volatility import (
    _price_volatility_percent,
    _score_changes_per_day,
    _time_in_low_percent,
    compute_volatility,
    volatility_to_normalized,
)


class TestFragmentationScoring:
    """Tests for estimate_fragmentation_risk."""

    def test_no_risk_factors(self):
        result = estimate_fragmentation_risk()
        assert result["score"] == 0.0
        assert result["label"] == "low"
        assert result["factors"] == []
        assert "heuristic" in result["disclaimer"].lower()

    def test_gpu_adds_025(self):
        result = estimate_fragmentation_risk(gpu_count=2)
        assert result["score"] == 0.25
        assert "gpu" in result["factors"]

    def test_large_vcpu(self):
        result = estimate_fragmentation_risk(vcpu=64)
        assert result["score"] == 0.15
        assert "large_vcpu" in result["factors"]

    def test_large_memory(self):
        result = estimate_fragmentation_risk(memory_gb=512)
        assert result["score"] == 0.15
        assert "large_memory" in result["factors"]

    def test_zonal(self):
        result = estimate_fragmentation_risk(require_zonal=True)
        assert result["score"] == 0.15
        assert "zonal" in result["factors"]

    def test_rdma(self):
        result = estimate_fragmentation_risk(rdma=True)
        assert result["score"] == 0.10
        assert "rdma" in result["factors"]

    def test_ultrassd(self):
        result = estimate_fragmentation_risk(ultra_ssd=True)
        assert result["score"] == 0.05
        assert "ultrassd" in result["factors"]

    def test_spot_low(self):
        result = estimate_fragmentation_risk(spot_score="Low")
        assert result["score"] == 0.10
        assert "spot_low" in result["factors"]

    def test_spot_high_no_contribution(self):
        result = estimate_fragmentation_risk(spot_score="High")
        assert result["score"] == 0.0

    def test_price_pressure(self):
        result = estimate_fragmentation_risk(price_ratio=0.9)
        assert result["score"] == 0.10
        assert "price_pressure" in result["factors"]

    def test_combined_high_risk(self):
        result = estimate_fragmentation_risk(
            gpu_count=4,
            vcpu=128,
            memory_gb=1024,
            require_zonal=True,
            rdma=True,
            ultra_ssd=True,
            spot_score="Low",
            price_ratio=0.95,
        )
        assert result["score"] == 1.0  # clamped
        assert result["label"] == "high"

    def test_medium_label(self):
        result = estimate_fragmentation_risk(gpu_count=1)
        assert result["label"] == "medium"

    def test_clamp_at_1(self):
        # All factors active: 0.25+0.15+0.15+0.15+0.10+0.05+0.10+0.10 = 1.05
        result = estimate_fragmentation_risk(
            gpu_count=1,
            vcpu=64,
            memory_gb=512,
            require_zonal=True,
            rdma=True,
            ultra_ssd=True,
            spot_score="Low",
            price_ratio=0.9,
        )
        assert result["score"] <= 1.0

    def test_normalized_mapping(self):
        assert fragmentation_to_normalized("low") == 1.0
        assert fragmentation_to_normalized("medium") == 0.6
        assert fragmentation_to_normalized("high") == 0.3
        assert fragmentation_to_normalized("unknown") is None


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


class TestVolatilityCalculation:
    """Tests for volatility metrics."""

    def test_score_changes_per_day_no_data(self):
        assert _score_changes_per_day([], [], 24) is None

    def test_score_changes_per_day_stable(self):
        scores = ["High", "High", "High"]
        ts = ["2026-01-01T00:00:00", "2026-01-01T12:00:00", "2026-01-02T00:00:00"]
        result = _score_changes_per_day(scores, ts, 24)
        assert result == 0.0

    def test_score_changes_per_day_volatile(self):
        scores = ["High", "Low", "High", "Low"]
        ts = ["t1", "t2", "t3", "t4"]
        result = _score_changes_per_day(scores, ts, 24)
        assert result == 3.0  # 3 changes in 1 day

    def test_time_in_low_percent(self):
        assert _time_in_low_percent([]) is None
        assert _time_in_low_percent(["Low", "Low", "High", "Medium"]) == 50.0
        assert _time_in_low_percent(["High", "High"]) == 0.0
        assert _time_in_low_percent([None, None]) is None

    def test_price_volatility_percent_single(self):
        assert _price_volatility_percent([1.0]) is None

    def test_price_volatility_percent_stable(self):
        assert _price_volatility_percent([1.0, 1.0, 1.0]) == 0.0

    def test_price_volatility_percent_volatile(self):
        result = _price_volatility_percent([1.0, 2.0, 3.0])
        assert result is not None
        assert result > 0

    def test_price_volatility_ignores_none(self):
        assert _price_volatility_percent([None, None]) is None

    def test_compute_volatility_insufficient_samples(self):
        with patch("az_scout.services.volatility.get_signals", return_value=[]):
            result = compute_volatility("eastus", "Standard_D2s_v3")
        assert result["label"] == "unknown"
        assert result["sampleCount"] == 0

    def test_compute_volatility_stable(self):
        signals = [
            {"spot_score": "High", "spot_price": 0.01, "timestamp": f"2026-01-01T{h:02d}:00:00"}
            for h in range(5)
        ]
        with patch("az_scout.services.volatility.get_signals", return_value=signals):
            result = compute_volatility("eastus", "Standard_D2s_v3", window="24h")
        assert result["label"] == "stable"
        assert result["sampleCount"] == 5

    def test_compute_volatility_unstable(self):
        scores = ["High", "Low", "High", "Low", "High"]
        signals = [
            {
                "spot_score": s,
                "spot_price": 0.01 * (i + 1),
                "timestamp": f"2026-01-01T{i:02d}:00:00",
            }
            for i, s in enumerate(scores)
        ]
        with patch("az_scout.services.volatility.get_signals", return_value=signals):
            result = compute_volatility("eastus", "Standard_D2s_v3")
        assert result["label"] in ("moderate", "unstable")

    def test_normalized_mapping(self):
        assert volatility_to_normalized("stable") == 1.0
        assert volatility_to_normalized("moderate") == 0.65
        assert volatility_to_normalized("unstable") == 0.3
        assert volatility_to_normalized("unknown") is None


# ---------------------------------------------------------------------------
# Eviction rate mapping
# ---------------------------------------------------------------------------


class TestEvictionMapping:
    """Tests for _map_eviction_rate."""

    def test_none_returns_none(self):
        assert _map_eviction_rate(None) is None

    def test_band_0_5(self):
        assert _map_eviction_rate("0-5") == 1.0
        assert _map_eviction_rate("0-5%") == 1.0

    def test_band_5_10(self):
        assert _map_eviction_rate("5-10") == 0.8

    def test_band_10_15(self):
        assert _map_eviction_rate("10-15") == 0.6

    def test_band_15_20(self):
        assert _map_eviction_rate("15-20") == 0.4

    def test_band_20_plus(self):
        assert _map_eviction_rate("20+") == 0.2
        assert _map_eviction_rate("20+%") == 0.2

    def test_unknown_string(self):
        assert _map_eviction_rate("unknown") is None

    def test_plain_number(self):
        assert _map_eviction_rate("3") == 1.0
        assert _map_eviction_rate("12") == 0.6
        assert _map_eviction_rate("25") == 0.2


# ---------------------------------------------------------------------------
# Admission Confidence – weighted renormalisation
# ---------------------------------------------------------------------------


class TestAdmissionConfidence:
    """Tests for the Admission Confidence Score."""

    def test_all_signals_high(self):
        result = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
            volatility_normalized=1.0,
            fragmentation_normalized=1.0,
            quota_remaining_vcpu=20,
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
        )
        assert result["score"] == 100
        assert result["label"] == "High"
        assert result["signalsAvailable"] == 6
        assert len(result["breakdown"]) == 6
        assert result["missingInputs"] == []

    def test_all_signals_low(self):
        result = compute_admission_confidence(
            spot_score_label="Low",
            eviction_rate_normalized=0.2,
            volatility_normalized=0.3,
            fragmentation_normalized=0.3,
            quota_remaining_vcpu=0,
            vcpus=2,
            zones_supported_count=0,
            restrictions_present=True,
        )
        assert result["score"] < 40
        assert result["label"] in ("Low", "Very Low")

    def test_no_signals_returns_unknown(self):
        result = compute_admission_confidence()
        assert result["label"] == "Unknown"
        assert result["score"] == 0
        assert result["signalsAvailable"] == 0

    def test_fewer_than_3_signals_unknown(self):
        result = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
        )
        assert result["label"] == "Unknown"
        assert result["signalsAvailable"] == 2

    def test_exactly_3_signals_computes(self):
        result = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
            volatility_normalized=1.0,
        )
        assert result["label"] != "Unknown"
        assert result["signalsAvailable"] == 3
        assert len(result["breakdown"]) == 3

    def test_renormalized_weights_sum_to_one(self):
        result = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
            volatility_normalized=1.0,
            fragmentation_normalized=1.0,
        )
        total_w = sum(b["weight"] for b in result["breakdown"])
        assert total_w == pytest.approx(1.0, abs=0.01)

    def test_missing_signals_listed(self):
        result = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
            volatility_normalized=1.0,
        )
        assert "FRAG" in result["missingInputs"]
        assert "QUOTA" in result["missingInputs"]
        assert "POLICY" in result["missingInputs"]

    def test_always_includes_disclaimers(self):
        result = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
            volatility_normalized=1.0,
        )
        assert len(result["disclaimers"]) >= 3
        assert any("heuristic" in d.lower() for d in result["disclaimers"])
        assert any("guarantee" in d.lower() for d in result["disclaimers"])

    def test_weights_sum_to_one(self):
        assert sum(ADMISSION_WEIGHTS.values()) == pytest.approx(1.0)

    def test_label_thresholds(self):
        # >= 80 High
        r = compute_admission_confidence(
            spot_score_label="High",
            eviction_rate_normalized=1.0,
            volatility_normalized=1.0,
            fragmentation_normalized=1.0,
            quota_remaining_vcpu=20,
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
        )
        assert r["label"] == "High"

    def test_partial_signals_score_meaningful(self):
        """With 4 out of 6 signals, still produces a meaningful score."""
        result = compute_admission_confidence(
            spot_score_label="Medium",
            volatility_normalized=0.65,
            fragmentation_normalized=0.6,
            zones_supported_count=2,
            restrictions_present=False,
        )
        assert result["label"] != "Unknown"
        assert 0 < result["score"] <= 100


# ---------------------------------------------------------------------------
# Signal collector – 429 retry/backoff
# ---------------------------------------------------------------------------


class TestCollectorBackoff:
    """Tests for the 429 retry/backoff mechanism."""

    def test_backoff_exponential_growth(self):
        d0 = _backoff_delay(0)
        d1 = _backoff_delay(1)
        d2 = _backoff_delay(2)
        # Each should be roughly double (with jitter)
        assert d1 > d0 * 0.5
        assert d2 > d1 * 0.5

    def test_backoff_respects_retry_after(self):
        delay = _backoff_delay(0, retry_after=10)
        assert delay >= 10.0
        assert delay <= 32.0  # 10 + max jitter

    def test_backoff_caps_at_max(self):
        delay = _backoff_delay(10)  # very high attempt
        assert delay <= 40.0  # MAX_DELAY + jitter

    def test_collector_uses_cache(self):
        """Second call for same key returns cached result."""
        clear_cache()
        mock_signal = {
            "region": "eastus",
            "sku": "Standard_D2s_v3",
            "spot_score": "High",
            "paygo_price": 0.1,
            "spot_price": 0.02,
            "zones_supported_count": 3,
            "restrictions_present": False,
            "confidence_score": 85,
        }
        with patch(
            "az_scout.services.signal_collector._do_collect",
            return_value=mock_signal,
        ) as mock_fn:
            r1 = collect_sku_signal("eastus", "Standard_D2s_v3", "sub-1")
            r2 = collect_sku_signal("eastus", "Standard_D2s_v3", "sub-1")

        assert r1 == r2
        mock_fn.assert_called_once()  # only 1 actual call
        clear_cache()


# ---------------------------------------------------------------------------
# MCP tool tests – breakdown + disclaimers
# ---------------------------------------------------------------------------


class TestMcpAdmissionIntelligence:
    """Tests for the sku_admission_intelligence MCP tool."""

    @pytest.fixture(autouse=True)
    def _mock_cred(self):
        mock_token = MagicMock()
        mock_token.token = "fake-token"
        with patch("az_scout.azure_api.credential") as cred:
            cred.get_token.return_value = mock_token
            yield cred

    @pytest.mark.anyio()
    async def test_output_contains_breakdown_and_disclaimers(self):
        mock_skus = [
            {
                "name": "Standard_D2s_v3",
                "zones": ["1", "2", "3"],
                "restrictions": [],
                "capabilities": {"vCPUs": "2", "MemoryGB": "8"},
            }
        ]
        mock_spot = {"scores": {"Standard_D2s_v3": {"1": "High"}}, "errors": []}
        mock_prices = {"Standard_D2s_v3": {"paygo": 0.1, "spot": 0.02, "currency": "USD"}}
        mock_eviction = {
            "evictionRate": "0-5",
            "normalizedScore": 1.0,
            "status": "available",
            "disclaimer": "test",
        }

        with (
            patch("az_scout.azure_api.get_skus", return_value=mock_skus),
            patch("az_scout.azure_api.get_spot_placement_scores", return_value=mock_spot),
            patch("az_scout.azure_api.get_retail_prices", return_value=mock_prices),
            patch("az_scout.azure_api.get_compute_usages", return_value=[]),
            patch(
                "az_scout.services.eviction_rate.get_spot_eviction_rate", return_value=mock_eviction
            ),
            patch("az_scout.services.volatility.get_signals", return_value=[]),
        ):
            content, _ = await mcp.call_tool(
                "sku_admission_intelligence",
                {
                    "region": "eastus",
                    "sku_name": "Standard_D2s_v3",
                    "subscription_id": "sub-1",
                },
            )

        data = json.loads(content[0].text)

        # Structure checks
        assert "fragmentationRisk" in data
        assert "volatility24h" in data
        assert "volatility7d" in data
        assert "evictionRate" in data
        assert "admissionConfidence" in data

        # Disclaimers present in admissionConfidence
        assert len(data["admissionConfidence"]["disclaimers"]) >= 3
        assert any("heuristic" in d.lower() for d in data["admissionConfidence"]["disclaimers"])
        assert any("guarantee" in d.lower() for d in data["admissionConfidence"]["disclaimers"])

        # Fragmentation has disclaimer
        assert "disclaimer" in data["fragmentationRisk"]

        # Admission confidence breakdown
        admission = data["admissionConfidence"]
        assert "breakdown" in admission
        assert "missingInputs" in admission
        assert "label" in admission

    @pytest.mark.anyio()
    async def test_graceful_with_no_data(self):
        """Tool returns Unknown when all data sources fail."""
        with (
            patch("az_scout.azure_api.get_skus", side_effect=Exception("fail")),
            patch("az_scout.azure_api.get_spot_placement_scores", side_effect=Exception("fail")),
            patch("az_scout.azure_api.get_retail_prices", side_effect=Exception("fail")),
            patch(
                "az_scout.services.eviction_rate.get_spot_eviction_rate",
                return_value={
                    "evictionRate": None,
                    "normalizedScore": None,
                    "status": "error",
                    "disclaimer": "test",
                },
            ),
            patch("az_scout.services.volatility.get_signals", return_value=[]),
        ):
            content, _ = await mcp.call_tool(
                "sku_admission_intelligence",
                {
                    "region": "eastus",
                    "sku_name": "Standard_D2s_v3",
                    "subscription_id": "sub-1",
                },
            )

        data = json.loads(content[0].text)
        assert data["admissionConfidence"]["label"] == "Unknown"
        assert len(data["admissionConfidence"]["disclaimers"]) >= 3
