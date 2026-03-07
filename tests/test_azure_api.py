"""Tests for azure_api helper functions."""

from unittest.mock import MagicMock, patch

import pytest

from az_scout.azure_api import _sku_name_matches
from az_scout.azure_api._arm import (
    ArmAuthorizationError,
    ArmNotFoundError,
    ArmRequestError,
    arm_get,
    arm_paginate,
    arm_post,
    get_headers,
)


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


# ---------------------------------------------------------------------------
# Helpers for ARM tests
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    headers: dict | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# get_headers
# ---------------------------------------------------------------------------


class TestGetHeaders:
    """Tests for the promoted get_headers public alias."""

    def test_returns_authorization_header(self) -> None:
        headers = get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")

    def test_passes_tenant_id(self) -> None:
        headers = get_headers(tenant_id="my-tenant")
        assert "Authorization" in headers


# ---------------------------------------------------------------------------
# arm_get
# ---------------------------------------------------------------------------


class TestArmGet:
    """Tests for arm_get."""

    def test_success(self) -> None:
        mock_resp = _mock_response(json_data={"value": [1, 2, 3]})
        with patch("az_scout.azure_api._arm.requests.get", return_value=mock_resp):
            result = arm_get("https://management.azure.com/test")
        assert result == {"value": [1, 2, 3]}

    def test_passes_params(self) -> None:
        mock_resp = _mock_response(json_data={"ok": True})
        with patch(
            "az_scout.azure_api._arm.requests.get",
            return_value=mock_resp,
        ) as mock_get:
            arm_get(
                "https://management.azure.com/test",
                params={"api-version": "2024-01-01"},
            )
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"api-version": "2024-01-01"}

    def test_raises_on_403(self) -> None:
        mock_resp = _mock_response(status_code=403, text="Forbidden")
        with (
            patch("az_scout.azure_api._arm.requests.get", return_value=mock_resp),
            pytest.raises(ArmAuthorizationError),
        ):
            arm_get("https://management.azure.com/test")

    def test_raises_on_404(self) -> None:
        mock_resp = _mock_response(status_code=404, text="Not Found")
        with (
            patch("az_scout.azure_api._arm.requests.get", return_value=mock_resp),
            pytest.raises(ArmNotFoundError),
        ):
            arm_get("https://management.azure.com/test")

    def test_retries_on_429(self) -> None:
        resp_429 = _mock_response(status_code=429, headers={"Retry-After": "0"})
        resp_ok = _mock_response(json_data={"ok": True})
        with patch(
            "az_scout.azure_api._arm.requests.get",
            side_effect=[resp_429, resp_ok],
        ):
            result = arm_get("https://management.azure.com/test", max_retries=2)
        assert result == {"ok": True}

    def test_retries_on_500(self) -> None:
        resp_500 = _mock_response(status_code=500)
        resp_ok = _mock_response(json_data={"ok": True})
        with patch(
            "az_scout.azure_api._arm.requests.get",
            side_effect=[resp_500, resp_ok],
        ):
            result = arm_get("https://management.azure.com/test", max_retries=2)
        assert result == {"ok": True}

    def test_raises_after_retries_exhausted(self) -> None:
        import requests as req_lib

        with (
            patch(
                "az_scout.azure_api._arm.requests.get",
                side_effect=req_lib.exceptions.ReadTimeout(),
            ),
            pytest.raises(ArmRequestError, match="failed after"),
        ):
            arm_get("https://management.azure.com/test", max_retries=2)


# ---------------------------------------------------------------------------
# arm_post
# ---------------------------------------------------------------------------


class TestArmPost:
    """Tests for arm_post."""

    def test_success(self) -> None:
        mock_resp = _mock_response(json_data={"result": "ok"})
        with patch("az_scout.azure_api._arm.requests.post", return_value=mock_resp):
            result = arm_post(
                "https://management.azure.com/test",
                json={"input": "data"},
            )
        assert result == {"result": "ok"}

    def test_raises_on_403(self) -> None:
        mock_resp = _mock_response(status_code=403, text="Forbidden")
        with (
            patch("az_scout.azure_api._arm.requests.post", return_value=mock_resp),
            pytest.raises(ArmAuthorizationError),
        ):
            arm_post("https://management.azure.com/test", json={})

    def test_retries_on_429(self) -> None:
        resp_429 = _mock_response(status_code=429, headers={"Retry-After": "0"})
        resp_ok = _mock_response(json_data={"ok": True})
        with patch(
            "az_scout.azure_api._arm.requests.post",
            side_effect=[resp_429, resp_ok],
        ):
            result = arm_post(
                "https://management.azure.com/test",
                json={},
                max_retries=2,
            )
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# arm_paginate
# ---------------------------------------------------------------------------


class TestArmPaginate:
    """Tests for arm_paginate."""

    def test_single_page(self) -> None:
        mock_resp = _mock_response(json_data={"value": [{"id": 1}], "nextLink": ""})
        with patch("az_scout.azure_api._arm.requests.get", return_value=mock_resp):
            items = arm_paginate("https://management.azure.com/test")
        assert items == [{"id": 1}]

    def test_multi_page(self) -> None:
        page1 = _mock_response(
            json_data={
                "value": [{"id": 1}],
                "nextLink": "https://management.azure.com/test?page=2",
            },
        )
        page2 = _mock_response(json_data={"value": [{"id": 2}], "nextLink": ""})
        with patch(
            "az_scout.azure_api._arm.requests.get",
            side_effect=[page1, page2],
        ):
            items = arm_paginate("https://management.azure.com/test")
        assert items == [{"id": 1}, {"id": 2}]

    def test_empty_result(self) -> None:
        mock_resp = _mock_response(json_data={"value": []})
        with patch("az_scout.azure_api._arm.requests.get", return_value=mock_resp):
            items = arm_paginate("https://management.azure.com/test")
        assert items == []

    def test_params_only_on_first_page(self) -> None:
        page1 = _mock_response(
            json_data={
                "value": [{"id": 1}],
                "nextLink": "https://management.azure.com/test?skiptoken=abc",
            },
        )
        page2 = _mock_response(json_data={"value": [{"id": 2}]})
        with patch(
            "az_scout.azure_api._arm.requests.get",
            side_effect=[page1, page2],
        ) as mock_get:
            arm_paginate(
                "https://management.azure.com/test",
                params={"api-version": "2024-01-01"},
            )
        calls = mock_get.call_args_list
        assert calls[0].kwargs["params"] == {"api-version": "2024-01-01"}
        assert calls[1].kwargs["params"] is None


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestArmExceptionHierarchy:
    """Verify exception types and attributes."""

    def test_authorization_error_is_arm_error(self) -> None:
        exc = ArmAuthorizationError("denied", status_code=403, url="/test")
        assert isinstance(exc, ArmRequestError)
        assert exc.status_code == 403

    def test_not_found_error_is_arm_error(self) -> None:
        exc = ArmNotFoundError("missing", status_code=404, url="/test")
        assert isinstance(exc, ArmRequestError)
        assert exc.status_code == 404

    def test_arm_request_error_attributes(self) -> None:
        exc = ArmRequestError("fail", status_code=500, url="/endpoint")
        assert str(exc) == "fail"
        assert exc.status_code == 500
        assert exc.url == "/endpoint"
