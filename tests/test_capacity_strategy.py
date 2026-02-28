"""Tests for shared evaluation helpers.

Covers:
- best_spot_label helper (unit tests)
"""

from az_scout.services._evaluation_helpers import best_spot_label

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
