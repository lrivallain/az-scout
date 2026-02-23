"""Tests for azure_api helper functions."""

from az_scout.azure_api import _sku_name_matches


class TestSkuNameMatches:
    """Tests for the fuzzy SKU name matching logic."""

    def test_exact_substring(self) -> None:
        assert _sku_name_matches("d2s", "standard_d2s_v3")

    def test_hyphen_normalised_to_underscore(self) -> None:
        assert _sku_name_matches("d2s-v3", "standard_d2s_v3")

    def test_multi_part_fuzzy(self) -> None:
        # "FX48-v2" → parts ["fx48", "v2"] both in "standard_fx48mds_v2"
        assert _sku_name_matches("fx48-v2", "standard_fx48mds_v2")

    def test_multi_part_order_matters(self) -> None:
        # Parts must appear in order
        assert not _sku_name_matches("v2-fx48", "standard_fx48mds_v2")

    def test_single_part_no_match(self) -> None:
        assert not _sku_name_matches("xyz", "standard_d2s_v3")

    def test_single_part_match(self) -> None:
        assert _sku_name_matches("d2s", "standard_d2s_v5")

    def test_empty_sku_name(self) -> None:
        assert not _sku_name_matches("d2s", "")

    def test_case_insensitive_assumed(self) -> None:
        # Caller is responsible for lowering; test with already-lower inputs
        assert _sku_name_matches("nc24", "standard_nc24ads_a100_v4")

    def test_multi_part_three_segments(self) -> None:
        assert _sku_name_matches("nc-a100-v4", "standard_nc24ads_a100_v4")

    def test_no_false_positive_partial_overlap(self) -> None:
        # "d48-v3" should not match "standard_d4s_v3" (d4 != d48)
        assert not _sku_name_matches("d48-v3", "standard_d4s_v3")
