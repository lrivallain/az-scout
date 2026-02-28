"""Tests for shared evaluation helpers and region latency service.

Covers:
- region_latency service (unit tests)
- best_spot_label helper (unit tests)
"""

from az_scout.services._evaluation_helpers import best_spot_label
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
