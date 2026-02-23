"""ISO test – old compute_capacity_confidence vs canonical compute_deployment_confidence.

Validates that, for identical numeric inputs, both functions produce the
same score (int 0-100), same label, and same missing-signal list.

Run with:
    uv run pytest tests/test_scoring_iso.py -v
"""

import pytest

from az_scout.scoring.deployment_confidence import (
    DeploymentSignals,
    compute_deployment_confidence,
)
from az_scout.services.capacity_confidence import (
    compute_capacity_confidence,
)

# Each tuple: (description, kwargs shared by both functions)
# NOTE: the old function uses `zones_supported_count`; the canonical uses
# `zones_available_count`.  When there are NO restrictions the two values
# are identical, so we can compare directly.  When restrictions exist we
# must pass the same integer to both and accept that the *semantic* meaning
# changed (old = total zones, new = available zones) – see SEMANTIC_DIFF tests.
_CASES: list[tuple[str, dict]] = [
    (
        "all_signals_max",
        dict(
            vcpus=2,
            zones_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label="High",
            paygo_price=1.0,
            spot_price=0.2,
        ),
    ),
    (
        "all_signals_min",
        dict(
            vcpus=4,
            zones_count=0,
            restrictions_present=True,
            quota_remaining_vcpu=0,
            spot_score_label="Low",
            paygo_price=1.0,
            spot_price=0.9,
        ),
    ),
    (
        "no_signals",
        dict(
            vcpus=None,
            zones_count=None,
            restrictions_present=None,
            quota_remaining_vcpu=None,
            spot_score_label=None,
            paygo_price=None,
            spot_price=None,
        ),
    ),
    (
        "missing_spot",
        dict(
            vcpus=2,
            zones_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=20,
            spot_score_label=None,
            paygo_price=1.0,
            spot_price=0.2,
        ),
    ),
    (
        "missing_quota",
        dict(
            vcpus=None,
            zones_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=None,
            spot_score_label="High",
            paygo_price=1.0,
            spot_price=0.2,
        ),
    ),
    (
        "missing_price_pressure",
        dict(
            vcpus=2,
            zones_count=2,
            restrictions_present=False,
            quota_remaining_vcpu=10,
            spot_score_label="Medium",
            paygo_price=None,
            spot_price=None,
        ),
    ),
    (
        "medium_confidence",
        dict(
            vcpus=4,
            zones_count=2,
            restrictions_present=False,
            quota_remaining_vcpu=10,
            spot_score_label="Medium",
            paygo_price=1.0,
            spot_price=0.5,
        ),
    ),
    (
        "only_zones_and_restrictions",
        dict(
            vcpus=None,
            zones_count=3,
            restrictions_present=False,
            quota_remaining_vcpu=None,
            spot_score_label=None,
            paygo_price=None,
            spot_price=None,
        ),
    ),
    (
        "only_spot",
        dict(
            vcpus=None,
            zones_count=None,
            restrictions_present=None,
            quota_remaining_vcpu=None,
            spot_score_label="High",
            paygo_price=None,
            spot_price=None,
        ),
    ),
    (
        "low_spot_with_restrictions",
        dict(
            vcpus=2,
            zones_count=1,
            restrictions_present=True,
            quota_remaining_vcpu=5,
            spot_score_label="Low",
            paygo_price=1.0,
            spot_price=0.7,
        ),
    ),
]


def _call_old(kwargs: dict) -> dict:
    """Call the OLD compute_capacity_confidence."""
    return compute_capacity_confidence(
        vcpus=kwargs["vcpus"],
        zones_supported_count=kwargs["zones_count"],
        restrictions_present=kwargs["restrictions_present"],
        quota_remaining_vcpu=kwargs["quota_remaining_vcpu"],
        spot_score_label=kwargs["spot_score_label"],
        paygo_price=kwargs["paygo_price"],
        spot_price=kwargs["spot_price"],
    )


def _call_new(kwargs: dict) -> dict:
    """Call the NEW compute_deployment_confidence and normalise to comparable dict."""
    result = compute_deployment_confidence(
        DeploymentSignals(
            vcpus=kwargs["vcpus"],
            zones_available_count=kwargs["zones_count"],
            restrictions_present=kwargs["restrictions_present"],
            quota_remaining_vcpu=kwargs["quota_remaining_vcpu"],
            spot_score_label=kwargs["spot_score_label"],
            paygo_price=kwargs["paygo_price"],
            spot_price=kwargs["spot_price"],
        )
    )
    return {
        "score": result.score,
        "label": result.label,
        "missing": sorted(result.missingSignals),
    }


def _normalise_old(old_result: dict) -> dict:
    """Normalise old result for comparison."""
    return {
        "score": old_result["score"],
        "label": old_result["label"],
        "missing": sorted(old_result["missing"]),
    }


_KNOWN_DIVERGENCES = {"no_signals", "only_spot"}
"""Cases where MIN_SIGNALS=2 in the canonical module intentionally returns
score=0 / label="Unknown" instead of computing a value from <2 signals."""


class TestScoringIso:
    """For identical numeric inputs, old and new must produce the same output."""

    @pytest.mark.parametrize("desc,kwargs", _CASES, ids=[c[0] for c in _CASES])
    def test_score_matches(self, desc: str, kwargs: dict):
        if desc in _KNOWN_DIVERGENCES:
            pytest.xfail(f"[{desc}] intentional MIN_SIGNALS=2 divergence")
        old = _normalise_old(_call_old(kwargs))
        new = _call_new(kwargs)
        assert old["score"] == new["score"], (
            f"[{desc}] score divergence: old={old['score']} new={new['score']}"
        )

    @pytest.mark.parametrize("desc,kwargs", _CASES, ids=[c[0] for c in _CASES])
    def test_label_matches(self, desc: str, kwargs: dict):
        if desc in _KNOWN_DIVERGENCES:
            pytest.xfail(f"[{desc}] intentional MIN_SIGNALS=2 divergence")
        old = _normalise_old(_call_old(kwargs))
        new = _call_new(kwargs)
        assert old["label"] == new["label"], (
            f"[{desc}] label divergence: old={old['label']} new={new['label']}"
        )

    @pytest.mark.parametrize("desc,kwargs", _CASES, ids=[c[0] for c in _CASES])
    def test_missing_matches(self, desc: str, kwargs: dict):
        old = _normalise_old(_call_old(kwargs))
        new = _call_new(kwargs)
        assert old["missing"] == new["missing"], (
            f"[{desc}] missing divergence: old={old['missing']} new={new['missing']}"
        )
