"""Tests for the Deployment Confidence Score module."""

import pytest

from az_mapping.services.capacity_confidence import (
    WEIGHTS,
    _price_pressure_score,
    _quota_score,
    _restriction_score,
    _spot_score,
    _zone_score,
    compute_capacity_confidence,
)

# ---------------------------------------------------------------------------
# Internal signal normalisation helpers
# ---------------------------------------------------------------------------


class TestQuotaScore:
    """Tests for _quota_score."""

    def test_none_remaining(self):
        assert _quota_score(None, 2) is None

    def test_none_vcpus(self):
        assert _quota_score(20, None) is None

    def test_zero_remaining(self):
        assert _quota_score(0, 2) == 0.0

    def test_negative_remaining(self):
        assert _quota_score(-5, 2) == 0.0

    def test_headroom_of_10_vms_gives_max(self):
        # 20 remaining / 2 vCPUs = 10 VMs → 100
        assert _quota_score(20, 2) == 100.0

    def test_headroom_above_10_capped(self):
        assert _quota_score(100, 2) == 100.0

    def test_partial_headroom(self):
        # 10 remaining / 4 vCPUs = 2.5 VMs → 2.5/10 * 100 = 25
        assert _quota_score(10, 4) == 25.0

    def test_vcpus_zero_treated_as_one(self):
        # Avoid division by zero: max(0, 1) = 1
        assert _quota_score(5, 0) == 50.0


class TestSpotScore:
    """Tests for _spot_score."""

    def test_none_returns_none(self):
        assert _spot_score(None) is None

    def test_high(self):
        assert _spot_score("High") == 100.0

    def test_medium_case_insensitive(self):
        assert _spot_score("medium") == 60.0

    def test_low(self):
        assert _spot_score("Low") == 25.0

    def test_unknown_label_returns_none(self):
        assert _spot_score("Unknown") is None


class TestZoneScore:
    """Tests for _zone_score."""

    def test_none_returns_none(self):
        assert _zone_score(None) is None

    def test_zero_zones(self):
        assert _zone_score(0) == 0.0

    def test_one_zone(self):
        assert _zone_score(1) == pytest.approx(33.33, rel=0.01)

    def test_two_zones(self):
        assert _zone_score(2) == pytest.approx(66.67, rel=0.01)

    def test_three_zones(self):
        assert _zone_score(3) == 100.0

    def test_more_than_three_capped(self):
        assert _zone_score(5) == 100.0


class TestRestrictionScore:
    """Tests for _restriction_score."""

    def test_none_returns_none(self):
        assert _restriction_score(None) is None

    def test_no_restrictions(self):
        assert _restriction_score(False) == 100.0

    def test_has_restrictions(self):
        assert _restriction_score(True) == 0.0


class TestPricePressureScore:
    """Tests for _price_pressure_score."""

    def test_none_paygo(self):
        assert _price_pressure_score(None, 0.5) is None

    def test_none_spot(self):
        assert _price_pressure_score(1.0, None) is None

    def test_zero_paygo(self):
        assert _price_pressure_score(0.0, 0.5) is None

    def test_very_low_ratio(self):
        # spot/paygo = 0.1 → (0.8-0.1)/0.6 = 1.167 → capped at 1.0 → 100
        assert _price_pressure_score(1.0, 0.1) == 100.0

    def test_ratio_at_0_2(self):
        # spot/paygo = 0.2 → (0.8-0.2)/0.6 = 1.0 → 100
        assert _price_pressure_score(1.0, 0.2) == 100.0

    def test_ratio_at_0_5(self):
        # spot/paygo = 0.5 → (0.8-0.5)/0.6 = 0.5 → 50
        assert _price_pressure_score(1.0, 0.5) == pytest.approx(50.0)

    def test_ratio_at_0_8(self):
        # spot/paygo = 0.8 → (0.8-0.8)/0.6 = 0 → 0
        assert _price_pressure_score(1.0, 0.8) == 0.0

    def test_ratio_above_0_8(self):
        # spot/paygo = 0.95 → (0.8-0.95)/0.6 = -0.25 → capped at 0 → 0
        assert _price_pressure_score(1.0, 0.95) == 0.0


# ---------------------------------------------------------------------------
# Main compute_capacity_confidence function
# ---------------------------------------------------------------------------


class TestComputeCapacityConfidence:
    """Tests for the main compute_capacity_confidence function."""

    def test_all_signals_present_high_confidence(self):
        result = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label="High",
            paygo_price=1.0,
            spot_price=0.2,
        )
        assert result["score"] == 100
        assert result["label"] == "High"
        assert result["missing"] == []
        assert len(result["breakdown"]) == 5

    def test_all_signals_present_low_confidence(self):
        result = compute_capacity_confidence(
            vcpus=4,
            zones_supported_count=0,
            restrictions_present=True,
            quota_remaining_vcpu=0,
            spot_score_label="Low",
            paygo_price=1.0,
            spot_price=0.9,
        )
        assert result["score"] <= 40
        assert result["label"] in ("Low", "Very Low")
        assert result["missing"] == []

    def test_no_signals_returns_zero(self):
        result = compute_capacity_confidence()
        assert result["score"] == 0
        assert result["label"] == "Very Low"
        assert len(result["missing"]) == 5
        assert result["breakdown"] == []

    def test_missing_quota_renormalized(self):
        """When quota is missing, weights renormalize over 4 remaining signals."""
        result = compute_capacity_confidence(
            zones_supported_count=3,
            restrictions_present=False,
            spot_score_label="High",
            paygo_price=1.0,
            spot_price=0.2,
        )
        assert "quota" in result["missing"]
        assert len(result["breakdown"]) == 4
        # All remaining signals are at 100 → score should be 100
        assert result["score"] == 100
        assert result["label"] == "High"

    def test_missing_spot_renormalized(self):
        """Spot signal missing (common server-side scenario)."""
        result = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            paygo_price=1.0,
            spot_price=0.2,
        )
        assert "spot" in result["missing"]
        assert len(result["breakdown"]) == 4
        # All present signals at 100 → score = 100
        assert result["score"] == 100

    def test_unknown_spot_label_treated_as_missing(self):
        result = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label="NotALabel",
        )
        assert "spot" in result["missing"]
        assert "pricePressure" in result["missing"]

    def test_restrictions_present_lowers_score(self):
        """With restrictions, restriction signal = 0, pulling score down."""
        no_restrict = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label="High",
        )
        with_restrict = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=True,
            quota_remaining_vcpu=20,
            spot_score_label="High",
        )
        assert with_restrict["score"] < no_restrict["score"]

    def test_label_thresholds(self):
        """Verify label assignment at boundary scores."""
        # Score 80 → High
        high = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label="High",
        )
        assert high["label"] == "High"

        # Medium: get a score between 60 and 79
        medium = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=2,
            restrictions_present=False,
            quota_remaining_vcpu=10,
            spot_score_label="Medium",
        )
        assert 60 <= medium["score"] < 80
        assert medium["label"] == "Medium"

    def test_single_signal_only(self):
        """Only zones present, everything else missing."""
        result = compute_capacity_confidence(zones_supported_count=3)
        assert result["score"] == 100
        assert result["label"] == "High"
        assert len(result["missing"]) == 4
        assert len(result["breakdown"]) == 1
        assert result["breakdown"][0]["signal"] == "zones"
        assert result["breakdown"][0]["weight"] == 1.0

    def test_breakdown_weights_sum_to_one(self):
        """Effective weights of present signals should sum to ~1.0."""
        result = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label="High",
            paygo_price=1.0,
            spot_price=0.2,
        )
        total_weight = sum(b["weight"] for b in result["breakdown"])
        assert total_weight == pytest.approx(1.0, abs=0.01)

    def test_breakdown_weights_renormalized_sum_to_one(self):
        """Even with missing signals, effective weights sum to ~1.0."""
        result = compute_capacity_confidence(
            vcpus=2,
            zones_supported_count=3,
            quota_remaining_vcpu=20,
        )
        total_weight = sum(b["weight"] for b in result["breakdown"])
        assert total_weight == pytest.approx(1.0, abs=0.01)

    def test_result_is_typed_dict(self):
        result = compute_capacity_confidence(zones_supported_count=1)
        assert isinstance(result, dict)
        assert "score" in result
        assert "label" in result
        assert "breakdown" in result
        assert "missing" in result

    def test_weights_constant_sums_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)
