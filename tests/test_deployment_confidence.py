"""Tests for the canonical Deployment Confidence Score module.

Covers:
  - Unit: normalisation helpers, renormalisation, label mapping, rounding, MIN_SIGNALS
  - Integration: signals_from_sku helper
  - Contract: POST /api/deployment-confidence endpoint (mocked Azure)
  - Regression: JS files must not contain local scoring code
"""

import pathlib

import pytest

from az_scout.scoring.deployment_confidence import (
    DISCLAIMERS,
    MIN_SIGNALS,
    WEIGHTS,
    DeploymentSignals,
    _check_knockouts,
    _normalize_price_pressure,
    _normalize_quota_pressure,
    _normalize_restriction_density,
    _normalize_spot,
    _normalize_zones,
    best_spot_label,
    compute_deployment_confidence,
    signals_from_sku,
)

# ===================================================================
# Normalisation helpers
# ===================================================================


class TestNormalizeQuotaPressure:
    def test_none_remaining(self):
        assert _normalize_quota_pressure(None, None, None, 2) is None

    def test_none_vcpus(self):
        assert _normalize_quota_pressure(10, 50, 20, None) is None

    def test_hard_failure_cannot_fit_one_vm(self):
        # remaining=1, vcpus=2 → can't fit a single VM
        assert _normalize_quota_pressure(49, 50, 1, 2) == 0.0

    def test_hard_failure_zero_remaining(self):
        assert _normalize_quota_pressure(50, 50, 0, 2) == 0.0

    def test_healthy_low_usage(self):
        # projected = (10+2)/100 = 12% → healthy
        assert _normalize_quota_pressure(10, 100, 90, 2) == 1.0

    def test_moderate_pressure(self):
        # projected = (70+2)/100 = 72% → moderate
        assert _normalize_quota_pressure(70, 100, 30, 2) == 0.7

    def test_danger_zone(self):
        # projected = (88+2)/100 = 90% → danger
        assert _normalize_quota_pressure(88, 100, 12, 2) == 0.3

    def test_critical_zone(self):
        # projected = (96+2)/100 = 98% → critical
        assert _normalize_quota_pressure(96, 100, 4, 2) == 0.1

    def test_fallback_when_no_usage_data(self):
        # No used/limit but remaining + vcpus present → linear fallback
        assert _normalize_quota_pressure(None, None, 20, 2) == 1.0

    def test_fallback_partial_headroom(self):
        # 10 remaining / 4 vCPUs = 2.5 VMs → 2.5/10 = 0.25
        assert _normalize_quota_pressure(None, None, 10, 4) == 0.25

    def test_boundary_60_percent(self):
        # projected = (58+2)/100 = 60% → moderate (boundary)
        assert _normalize_quota_pressure(58, 100, 42, 2) == 0.7
        # projected = (57+2)/100 = 59% → still healthy
        assert _normalize_quota_pressure(57, 100, 43, 2) == 1.0

    def test_boundary_80_percent(self):
        # projected = (77+2)/100 = 79% → moderate
        assert _normalize_quota_pressure(77, 100, 23, 2) == 0.7
        # projected = (78+2)/100 = 80% → danger
        assert _normalize_quota_pressure(78, 100, 22, 2) == 0.3

    def test_boundary_95_percent(self):
        # projected = (92+2)/100 = 94% → danger
        assert _normalize_quota_pressure(92, 100, 8, 2) == 0.3
        # projected = (93+2)/100 = 95% → critical
        assert _normalize_quota_pressure(93, 100, 7, 2) == 0.1

    # ----- demand-adjusted (instance_count > 1) -----------------------

    def test_instance_count_default_matches_single(self):
        """instance_count=1 (default) behaves same as not passing it."""
        assert _normalize_quota_pressure(10, 100, 90, 2, 1) == 1.0

    def test_fleet_hard_failure(self):
        """10×16 vCPU = 160 vCPU demand > 90 remaining → hard fail."""
        assert _normalize_quota_pressure(10, 100, 90, 16, 10) == 0.0

    def test_fleet_projected_healthy(self):
        """10 used + 2×2 = 14 projected → 14% → healthy."""
        assert _normalize_quota_pressure(10, 100, 90, 2, 2) == 1.0

    def test_fleet_projected_moderate(self):
        """10 used + 2×25 = 60 projected → 60% → moderate."""
        assert _normalize_quota_pressure(10, 100, 90, 2, 25) == 0.7

    def test_fleet_projected_danger(self):
        """10 used + 4×20 = 90 projected → 90% → danger."""
        assert _normalize_quota_pressure(10, 100, 90, 4, 20) == 0.3

    def test_fleet_projected_critical(self):
        """10 used + 2×43 = 96 projected → 96% → critical."""
        assert _normalize_quota_pressure(10, 100, 90, 2, 43) == 0.1

    def test_fleet_fallback_no_usage(self):
        """No used/limit → linear fallback with fleet_vcpus."""
        # 90 remaining / (2*5=10 fleet) / 10 = 0.9
        assert _normalize_quota_pressure(None, None, 90, 2, 5) == 0.9


class TestNormalizeSpot:
    def test_none_returns_none(self):
        assert _normalize_spot(None) is None

    def test_high(self):
        assert _normalize_spot("High") == 1.0

    def test_medium_case_insensitive(self):
        assert _normalize_spot("medium") == 0.6

    def test_low(self):
        assert _normalize_spot("Low") == 0.25

    def test_unknown_label_returns_none(self):
        assert _normalize_spot("Unknown") is None

    def test_restricted_returns_zero(self):
        assert _normalize_spot("RestrictedSkuNotAvailable") == 0.0

    def test_restricted_short_returns_zero(self):
        assert _normalize_spot("Restricted") == 0.0


class TestNormalizeZones:
    def test_none_returns_none(self):
        assert _normalize_zones(None) is None

    def test_zero_zones(self):
        assert _normalize_zones(0) == 0.0

    def test_three_zones(self):
        assert _normalize_zones(3) == 1.0

    def test_more_than_three_capped(self):
        assert _normalize_zones(5) == 1.0

    def test_one_zone(self):
        assert _normalize_zones(1) == pytest.approx(1.0 / 3.0)


class TestNormalizeRestrictionDensity:
    def test_none_restricted(self):
        assert _normalize_restriction_density(None, 3) is None

    def test_none_total(self):
        assert _normalize_restriction_density(0, None) is None

    def test_no_restrictions(self):
        assert _normalize_restriction_density(0, 3) == 1.0

    def test_one_of_three_restricted(self):
        assert _normalize_restriction_density(1, 3) == pytest.approx(2.0 / 3.0)

    def test_two_of_three_restricted(self):
        assert _normalize_restriction_density(2, 3) == pytest.approx(1.0 / 3.0)

    def test_all_restricted(self):
        assert _normalize_restriction_density(3, 3) == 0.0

    def test_zero_total_zones(self):
        assert _normalize_restriction_density(0, 0) == 0.0


class TestNormalizePricePressure:
    def test_none_paygo(self):
        assert _normalize_price_pressure(None, 0.5) is None

    def test_none_spot(self):
        assert _normalize_price_pressure(1.0, None) is None

    def test_zero_paygo(self):
        assert _normalize_price_pressure(0.0, 0.5) is None

    def test_very_low_ratio(self):
        # 0.1/1.0 = 0.1 → (0.8-0.1)/0.6 = 1.167 → capped at 1.0
        assert _normalize_price_pressure(1.0, 0.1) == 1.0

    def test_ratio_at_0_2(self):
        assert _normalize_price_pressure(1.0, 0.2) == 1.0

    def test_ratio_at_0_5(self):
        assert _normalize_price_pressure(1.0, 0.5) == pytest.approx(0.5)

    def test_ratio_at_0_8(self):
        assert _normalize_price_pressure(1.0, 0.8) == 0.0

    def test_ratio_above_0_8(self):
        assert _normalize_price_pressure(1.0, 0.95) == 0.0


# ===================================================================
# Main compute_deployment_confidence
# ===================================================================


class TestComputeDeploymentConfidence:
    """Tests for the main compute_deployment_confidence function."""

    def test_all_signals_high_confidence(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                spot_score_label="High",
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert result.score == 100
        assert result.label == "High"
        assert result.missingSignals == []
        used = [c for c in result.breakdown.components if c.status == "used"]
        assert len(used) == 5

    def test_all_signals_low_confidence(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=4,
                zones_available_count=0,
                zones_total_count=3,
                restricted_zones_count=3,
                quota_used_vcpu=50,
                quota_limit_vcpu=50,
                quota_remaining_vcpu=0,
                spot_score_label="Low",
                paygo_price=1.0,
                spot_price=0.9,
            )
        )
        # Both quota and zone knockouts fire → Blocked
        assert result.score == 0
        assert result.label == "Blocked"
        assert result.scoreType == "blocked"
        assert len(result.knockoutReasons) == 2
        assert result.missingSignals == []

    def test_no_signals_returns_unknown(self):
        result = compute_deployment_confidence(DeploymentSignals())
        assert result.score == 0
        assert result.label == "Unknown"
        assert len(result.missingSignals) == 5

    def test_single_signal_below_min_signals(self):
        """Only one signal: below MIN_SIGNALS → Unknown."""
        result = compute_deployment_confidence(DeploymentSignals(zones_available_count=3))
        assert result.score == 0
        assert result.label == "Unknown"
        assert len(result.missingSignals) == 4

    def test_two_signals_meets_min_signals(self):
        """Exactly MIN_SIGNALS (2) → produces a real score."""
        result = compute_deployment_confidence(
            DeploymentSignals(
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
            )
        )
        assert result.score > 0
        assert result.label != "Unknown"

    def test_missing_quota_renormalized(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                spot_score_label="High",
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert "quotaPressure" in result.missingSignals
        assert result.breakdown.renormalized is True
        used = [c for c in result.breakdown.components if c.status == "used"]
        assert len(used) == 4
        # All 4 remaining signals at max → score stays 100
        assert result.score == 100
        assert result.label == "High"

    def test_missing_spot_renormalized(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert "spot" in result.missingSignals
        assert result.breakdown.renormalized is True
        used = [c for c in result.breakdown.components if c.status == "used"]
        assert len(used) == 4
        assert result.score == 100

    def test_restrictions_present_lowers_score(self):
        base = DeploymentSignals(
            vcpus=2,
            zones_available_count=3,
            zones_total_count=3,
            quota_used_vcpu=10,
            quota_limit_vcpu=100,
            quota_remaining_vcpu=90,
            spot_score_label="High",
        )
        no_restrict = compute_deployment_confidence(
            base.model_copy(update={"restricted_zones_count": 0})
        )
        with_restrict = compute_deployment_confidence(
            base.model_copy(update={"restricted_zones_count": 2})
        )
        assert with_restrict.score < no_restrict.score

    def test_label_thresholds(self):
        """High label for perfect inputs."""
        high = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                spot_score_label="High",
            )
        )
        assert high.label == "High"

        medium = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=2,
                zones_total_count=3,
                restricted_zones_count=1,
                quota_used_vcpu=70,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=30,
                spot_score_label="Medium",
            )
        )
        assert 60 <= medium.score < 80
        assert medium.label == "Medium"

    def test_breakdown_weights_sum_to_one(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                spot_score_label="High",
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        used = [c for c in result.breakdown.components if c.status == "used"]
        total_weight = sum(c.weight for c in used)
        assert total_weight == pytest.approx(1.0, abs=0.01)

    def test_breakdown_renormalized_weights_sum_to_one(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
            )
        )
        used = [c for c in result.breakdown.components if c.status == "used"]
        total_weight = sum(c.weight for c in used)
        assert total_weight == pytest.approx(1.0, abs=0.01)

    def test_result_is_pydantic_model(self):
        from az_scout.scoring.deployment_confidence import DeploymentConfidenceResult

        result = compute_deployment_confidence(
            DeploymentSignals(
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
            )
        )
        assert isinstance(result, DeploymentConfidenceResult)

    def test_disclaimers_present(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                zones_available_count=3, zones_total_count=3, restricted_zones_count=0
            )
        )
        assert len(result.disclaimers) > 0
        assert result.disclaimers == DISCLAIMERS

    def test_provenance_has_timestamp(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                zones_available_count=3, zones_total_count=3, restricted_zones_count=0
            )
        )
        assert result.provenance.computedAtUtc is not None
        assert len(result.provenance.computedAtUtc) > 0

    def test_model_dump_round_trip(self):
        """Result can be serialised to dict and back."""
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                spot_score_label="High",
            )
        )
        d = result.model_dump()
        assert isinstance(d, dict)
        assert d["score"] == result.score
        assert d["label"] == result.label
        assert d["scoreType"] in ("basic", "basic+spot")

    def test_score_type_basic_when_no_spot(self):
        """scoreType is 'basic' when spot signal is missing."""
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert result.scoreType == "basic"
        assert "spot" in result.missingSignals

    def test_score_type_basic_spot_when_spot_present(self):
        """scoreType is 'basic+spot' when spot signal is provided."""
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                spot_score_label="High",
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert result.scoreType == "basic+spot"
        assert "spot" not in result.missingSignals

    def test_score_type_basic_with_all_missing(self):
        """scoreType is 'basic' even when all signals are missing (Unknown)."""
        result = compute_deployment_confidence(DeploymentSignals())
        assert result.scoreType == "basic"
        assert result.label == "Unknown"

    def test_score_type_basic_spot_in_unknown(self):
        """scoreType is 'basic+spot' if only spot is provided but below MIN_SIGNALS."""
        result = compute_deployment_confidence(DeploymentSignals(spot_score_label="High"))
        assert result.scoreType == "basic+spot"
        assert result.label == "Unknown"

    def test_score_always_int_0_100(self):
        """Score must be an integer between 0 and 100 inclusive."""
        for label in ("High", "Medium", "Low"):
            result = compute_deployment_confidence(
                DeploymentSignals(
                    vcpus=2,
                    zones_available_count=3,
                    zones_total_count=3,
                    restricted_zones_count=0,
                    quota_used_vcpu=10,
                    quota_limit_vcpu=100,
                    quota_remaining_vcpu=90,
                    spot_score_label=label,
                )
            )
            assert isinstance(result.score, int)
            assert 0 <= result.score <= 100

    def test_restricted_spot_included_as_zero_score(self):
        """Restricted spot label is included with score 0, not treated as missing."""
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                zones_total_count=3,
                restricted_zones_count=0,
                quota_used_vcpu=10,
                quota_limit_vcpu=100,
                quota_remaining_vcpu=90,
                spot_score_label="RestrictedSkuNotAvailable",
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert result.scoreType == "basic+spot"
        assert "spot" not in result.missingSignals
        spot_component = next(c for c in result.breakdown.components if c.name == "spot")
        assert spot_component.status == "used"
        assert spot_component.score01 == 0.0

    def test_weights_constant_sums_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_min_signals_is_two(self):
        assert MIN_SIGNALS == 2


# ===================================================================
# best_spot_label helper
# ===================================================================


class TestBestSpotLabel:
    def test_empty_dict_returns_none(self):
        assert best_spot_label({}) is None

    def test_single_zone(self):
        assert best_spot_label({"1": "Medium"}) == "Medium"

    def test_picks_highest(self):
        assert best_spot_label({"1": "Low", "2": "High", "3": "Medium"}) == "High"

    def test_case_insensitive(self):
        assert best_spot_label({"1": "low", "2": "HIGH"}) == "HIGH"

    def test_unknown_labels_returns_fallback(self):
        # "Unknown" is not in the scoring map but zone data exists,
        # so the first label is returned as a fallback.
        assert best_spot_label({"1": "Unknown"}) == "Unknown"

    def test_restricted_labels_returned_as_fallback(self):
        result = best_spot_label(
            {"1": "RestrictedSkuNotAvailable", "2": "RestrictedSkuNotAvailable"}
        )
        assert result == "RestrictedSkuNotAvailable"

    def test_scorable_preferred_over_restricted(self):
        result = best_spot_label(
            {"1": "RestrictedSkuNotAvailable", "2": "Low", "3": "RestrictedSkuNotAvailable"}
        )
        assert result == "Low"


# ===================================================================
# signals_from_sku helper
# ===================================================================


class TestSignalsFromSku:
    def test_empty_sku(self):
        sig = signals_from_sku({})
        assert sig.vcpus is not None  # defaults to int(0)
        assert sig.zones_available_count == 0
        assert sig.spot_score_label is None

    def test_full_sku(self):
        sku = {
            "capabilities": {"vCPUs": "4"},
            "zones": ["1", "2", "3"],
            "restrictions": ["2"],
            "quota": {"used": 80, "limit": 100, "remaining": 20},
            "pricing": {"paygo": 1.0, "spot": 0.3},
        }
        sig = signals_from_sku(sku, spot_score_label="High")
        assert sig.vcpus == 4
        assert sig.instance_count == 1  # default
        assert sig.zones_available_count == 2  # 3 zones minus 1 restricted
        assert sig.zones_total_count == 3
        assert sig.restricted_zones_count == 1
        assert sig.quota_used_vcpu == 80
        assert sig.quota_limit_vcpu == 100
        assert sig.quota_remaining_vcpu == 20
        assert sig.spot_score_label == "High"
        assert sig.paygo_price == 1.0
        assert sig.spot_price == 0.3

    def test_instance_count_passed_through(self):
        sku = {"capabilities": {"vCPUs": "2"}, "zones": ["1", "2", "3"]}
        sig = signals_from_sku(sku, instance_count=5)
        assert sig.instance_count == 5

    def test_no_restrictions_all_zones_available(self):
        sku = {"zones": ["1", "2", "3"], "restrictions": []}
        sig = signals_from_sku(sku)
        assert sig.zones_available_count == 3
        assert sig.zones_total_count == 3
        assert sig.restricted_zones_count == 0


# ===================================================================
# Knockout layer
# ===================================================================


class TestCheckKnockouts:
    """Unit tests for _check_knockouts helper."""

    def test_no_knockout_when_quota_sufficient(self):
        signals = DeploymentSignals(
            quota_remaining_vcpu=100,
            vcpus=4,
            instance_count=5,
        )
        assert _check_knockouts(signals) == []

    def test_quota_knockout_exact_boundary(self):
        """Remaining == fleet vCPUs → just fits, no knockout."""
        signals = DeploymentSignals(
            quota_remaining_vcpu=20,
            vcpus=4,
            instance_count=5,
        )
        assert _check_knockouts(signals) == []

    def test_quota_knockout_one_below(self):
        """Remaining < fleet vCPUs → knockout."""
        signals = DeploymentSignals(
            quota_remaining_vcpu=19,
            vcpus=4,
            instance_count=5,
        )
        reasons = _check_knockouts(signals)
        assert len(reasons) == 1
        assert "Insufficient quota" in reasons[0]
        assert "19 vCPUs remaining" in reasons[0]
        assert "20 required" in reasons[0]

    def test_zone_knockout(self):
        signals = DeploymentSignals(zones_available_count=0)
        reasons = _check_knockouts(signals)
        assert len(reasons) == 1
        assert "No availability zones" in reasons[0]

    def test_no_zone_knockout_when_zones_available(self):
        signals = DeploymentSignals(zones_available_count=1)
        assert _check_knockouts(signals) == []

    def test_both_knockouts_simultaneously(self):
        signals = DeploymentSignals(
            quota_remaining_vcpu=0,
            vcpus=4,
            instance_count=1,
            zones_available_count=0,
        )
        reasons = _check_knockouts(signals)
        assert len(reasons) == 2
        assert any("Insufficient quota" in r for r in reasons)
        assert any("No availability zones" in r for r in reasons)

    def test_no_knockout_with_none_signals(self):
        """Missing quota or zone data → no knockout (conservative)."""
        signals = DeploymentSignals()
        assert _check_knockouts(signals) == []

    def test_quota_knockout_default_instance_count(self):
        """instance_count defaults to 1."""
        signals = DeploymentSignals(
            quota_remaining_vcpu=3,
            vcpus=4,
            instance_count=1,
        )
        reasons = _check_knockouts(signals)
        assert len(reasons) == 1
        assert "4 required (4 × 1)" in reasons[0]


class TestKnockoutIntegration:
    """Integration tests: knockout layer in compute_deployment_confidence."""

    @pytest.fixture()
    def healthy_signals(self) -> DeploymentSignals:
        """Signals that would normally produce a *high* score."""
        return DeploymentSignals(
            quota_used_vcpu=10,
            quota_limit_vcpu=200,
            quota_remaining_vcpu=190,
            vcpus=4,
            instance_count=1,
            spot_score_label="High",
            zones_available_count=3,
            zones_total_count=3,
            restricted_zones_count=0,
            paygo_price=1.0,
            spot_price=0.3,
        )

    def test_healthy_signals_not_blocked(self, healthy_signals: DeploymentSignals):
        result = compute_deployment_confidence(healthy_signals)
        assert result.scoreType != "blocked"
        assert result.knockoutReasons == []
        assert result.score > 0

    def test_quota_knockout_forces_blocked(self, healthy_signals: DeploymentSignals):
        healthy_signals.quota_remaining_vcpu = 3  # < 4 × 1
        result = compute_deployment_confidence(healthy_signals)
        assert result.score == 0
        assert result.label == "Blocked"
        assert result.scoreType == "blocked"
        assert len(result.knockoutReasons) == 1
        assert "Insufficient quota" in result.knockoutReasons[0]
        # Breakdown components are still computed
        used = [c for c in result.breakdown.components if c.status == "used"]
        assert len(used) > 0

    def test_zone_knockout_forces_blocked(self, healthy_signals: DeploymentSignals):
        healthy_signals.zones_available_count = 0
        result = compute_deployment_confidence(healthy_signals)
        assert result.score == 0
        assert result.label == "Blocked"
        assert result.scoreType == "blocked"
        assert any("No availability zones" in r for r in result.knockoutReasons)

    def test_knockout_with_high_instance_count(self, healthy_signals: DeploymentSignals):
        healthy_signals.instance_count = 50  # 4 × 50 = 200 > 190 remaining
        result = compute_deployment_confidence(healthy_signals)
        assert result.score == 0
        assert result.label == "Blocked"
        assert result.scoreType == "blocked"
        assert "200 required" in result.knockoutReasons[0]

    def test_knockout_with_just_enough_quota(self, healthy_signals: DeploymentSignals):
        healthy_signals.instance_count = 47  # 4 × 47 = 188 < 190 remaining
        result = compute_deployment_confidence(healthy_signals)
        assert result.scoreType != "blocked"
        assert result.knockoutReasons == []

    def test_knockout_preserves_disclaimers(self, healthy_signals: DeploymentSignals):
        healthy_signals.quota_remaining_vcpu = 0
        result = compute_deployment_confidence(healthy_signals)
        assert result.disclaimers == DISCLAIMERS

    def test_knockout_preserves_provenance(self, healthy_signals: DeploymentSignals):
        healthy_signals.quota_remaining_vcpu = 0
        result = compute_deployment_confidence(healthy_signals)
        assert result.provenance.computedAtUtc is not None

    def test_dual_knockout_both_reasons(self, healthy_signals: DeploymentSignals):
        healthy_signals.quota_remaining_vcpu = 0
        healthy_signals.zones_available_count = 0
        result = compute_deployment_confidence(healthy_signals)
        assert result.score == 0
        assert result.label == "Blocked"
        assert len(result.knockoutReasons) == 2


# ===================================================================
# UI regression: JS files must not contain local scoring
# ===================================================================


class TestUIRegression:
    """Confirm that frontend JS no longer contains local scoring code."""

    @pytest.fixture()
    def all_js_content(self) -> str:
        js_dir = (
            pathlib.Path(__file__).resolve().parent.parent / "src" / "az_scout" / "static" / "js"
        )
        parts = []
        for js_file in sorted(js_dir.glob("*.js")):
            parts.append(js_file.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def test_no_recompute_confidence(self, all_js_content: str):
        assert "recomputeConfidence" not in all_js_content

    def test_no_conf_weights(self, all_js_content: str):
        assert "_CONF_WEIGHTS" not in all_js_content

    def test_no_conf_labels(self, all_js_content: str):
        assert "_CONF_LABELS" not in all_js_content
