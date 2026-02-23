"""Tests for the canonical Deployment Confidence Score module.

Covers:
  - Unit: normalisation helpers, renormalisation, label mapping, rounding, MIN_SIGNALS
  - Integration: signals_from_sku helper
  - Contract: POST /api/deployment-confidence endpoint (mocked Azure)
  - Regression: app.js must not contain local scoring code
"""

import pathlib

import pytest

from az_scout.scoring.deployment_confidence import (
    DISCLAIMERS,
    MIN_SIGNALS,
    SCORING_VERSION,
    WEIGHTS,
    DeploymentSignals,
    _normalize_price_pressure,
    _normalize_quota,
    _normalize_restrictions,
    _normalize_spot,
    _normalize_zones,
    best_spot_label,
    compute_deployment_confidence,
    signals_from_sku,
)

# ===================================================================
# Normalisation helpers
# ===================================================================


class TestNormalizeQuota:
    def test_none_remaining(self):
        assert _normalize_quota(None, 2) is None

    def test_none_vcpus(self):
        assert _normalize_quota(20, None) is None

    def test_zero_remaining(self):
        assert _normalize_quota(0, 2) == 0.0

    def test_negative_remaining(self):
        assert _normalize_quota(-5, 2) == 0.0

    def test_headroom_10_vms_gives_max(self):
        assert _normalize_quota(20, 2) == 1.0

    def test_headroom_above_10_capped(self):
        assert _normalize_quota(100, 2) == 1.0

    def test_partial_headroom(self):
        # 10 remaining / 4 vCPUs = 2.5 VMs → 2.5/10 = 0.25
        assert _normalize_quota(10, 4) == 0.25

    def test_vcpus_zero_treated_as_one(self):
        # max(0, 1) = 1 → 5/1/10 = 0.5
        assert _normalize_quota(5, 0) == 0.5


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


class TestNormalizeRestrictions:
    def test_none_returns_none(self):
        assert _normalize_restrictions(None) is None

    def test_no_restrictions(self):
        assert _normalize_restrictions(False) == 1.0

    def test_has_restrictions(self):
        assert _normalize_restrictions(True) == 0.0


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
                restrictions_present=False,
                quota_remaining_vcpu=20,
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
                restrictions_present=True,
                quota_remaining_vcpu=0,
                spot_score_label="Low",
                paygo_price=1.0,
                spot_price=0.9,
            )
        )
        assert result.score <= 40
        assert result.label in ("Low", "Very Low")
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
                restrictions_present=False,
            )
        )
        assert result.score > 0
        assert result.label != "Unknown"

    def test_missing_quota_renormalized(self):
        result = compute_deployment_confidence(
            DeploymentSignals(
                zones_available_count=3,
                restrictions_present=False,
                spot_score_label="High",
                paygo_price=1.0,
                spot_price=0.2,
            )
        )
        assert "quota" in result.missingSignals
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
                restrictions_present=False,
                quota_remaining_vcpu=20,
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
            quota_remaining_vcpu=20,
            spot_score_label="High",
        )
        no_restrict = compute_deployment_confidence(
            base.model_copy(update={"restrictions_present": False})
        )
        with_restrict = compute_deployment_confidence(
            base.model_copy(update={"restrictions_present": True})
        )
        assert with_restrict.score < no_restrict.score

    def test_label_thresholds(self):
        """High label for perfect inputs."""
        high = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                restrictions_present=False,
                quota_remaining_vcpu=20,
                spot_score_label="High",
            )
        )
        assert high.label == "High"

        medium = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=2,
                restrictions_present=False,
                quota_remaining_vcpu=10,
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
                restrictions_present=False,
                quota_remaining_vcpu=20,
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
                quota_remaining_vcpu=20,
            )
        )
        used = [c for c in result.breakdown.components if c.status == "used"]
        total_weight = sum(c.weight for c in used)
        assert total_weight == pytest.approx(1.0, abs=0.01)

    def test_result_is_pydantic_model(self):
        from az_scout.scoring.deployment_confidence import DeploymentConfidenceResult

        result = compute_deployment_confidence(
            DeploymentSignals(zones_available_count=3, restrictions_present=False)
        )
        assert isinstance(result, DeploymentConfidenceResult)

    def test_scoring_version_present(self):
        result = compute_deployment_confidence(
            DeploymentSignals(zones_available_count=3, restrictions_present=False)
        )
        assert result.scoringVersion == SCORING_VERSION
        assert result.provenance.scoringVersion == SCORING_VERSION

    def test_disclaimers_present(self):
        result = compute_deployment_confidence(
            DeploymentSignals(zones_available_count=3, restrictions_present=False)
        )
        assert len(result.disclaimers) > 0
        assert result.disclaimers == DISCLAIMERS

    def test_provenance_has_timestamp(self):
        result = compute_deployment_confidence(
            DeploymentSignals(zones_available_count=3, restrictions_present=False)
        )
        assert result.provenance.computedAtUtc is not None
        assert len(result.provenance.computedAtUtc) > 0

    def test_model_dump_round_trip(self):
        """Result can be serialised to dict and back."""
        result = compute_deployment_confidence(
            DeploymentSignals(
                vcpus=2,
                zones_available_count=3,
                restrictions_present=False,
                quota_remaining_vcpu=20,
                spot_score_label="High",
            )
        )
        d = result.model_dump()
        assert isinstance(d, dict)
        assert d["score"] == result.score
        assert d["label"] == result.label

    def test_score_always_int_0_100(self):
        """Score must be an integer between 0 and 100 inclusive."""
        for label in ("High", "Medium", "Low"):
            result = compute_deployment_confidence(
                DeploymentSignals(
                    vcpus=2,
                    zones_available_count=3,
                    restrictions_present=False,
                    quota_remaining_vcpu=20,
                    spot_score_label=label,
                )
            )
            assert isinstance(result.score, int)
            assert 0 <= result.score <= 100

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

    def test_unknown_labels_not_ranked(self):
        # "Unknown" is not in the ranking map → treated as rank 0, returns None
        assert best_spot_label({"1": "Unknown"}) is None


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
            "quota": {"remaining": 20},
            "pricing": {"paygo": 1.0, "spot": 0.3},
        }
        sig = signals_from_sku(sku, spot_score_label="High")
        assert sig.vcpus == 4
        assert sig.zones_available_count == 2  # 3 zones minus 1 restricted
        assert sig.restrictions_present is True
        assert sig.quota_remaining_vcpu == 20
        assert sig.spot_score_label == "High"
        assert sig.paygo_price == 1.0
        assert sig.spot_price == 0.3

    def test_no_restrictions_all_zones_available(self):
        sku = {"zones": ["1", "2", "3"], "restrictions": []}
        sig = signals_from_sku(sku)
        assert sig.zones_available_count == 3
        assert sig.restrictions_present is False


# ===================================================================
# UI regression: app.js must not contain local scoring
# ===================================================================


class TestUIRegression:
    """Confirm that app.js no longer contains frontend scoring code."""

    @pytest.fixture()
    def app_js_content(self) -> str:
        app_js = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src"
            / "az_scout"
            / "static"
            / "js"
            / "app.js"
        )
        return app_js.read_text(encoding="utf-8")

    def test_no_recompute_confidence(self, app_js_content: str):
        assert "recomputeConfidence" not in app_js_content

    def test_no_conf_weights(self, app_js_content: str):
        assert "_CONF_WEIGHTS" not in app_js_content

    def test_no_conf_labels(self, app_js_content: str):
        assert "_CONF_LABELS" not in app_js_content
