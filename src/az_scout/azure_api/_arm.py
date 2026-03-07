"""High-level ARM HTTP helpers with authentication, retry, and pagination.

These functions form the **recommended** way for both core modules and
plugins to interact with Azure Resource Manager endpoints.

All three helpers (``arm_get``, ``arm_post``, ``arm_paginate``) are part
of the stable public plugin API (see ``__all__`` in ``__init__.py``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

# Import requests through a module-level reference that test fixtures
# can patch via ``az_scout.azure_api._arm.requests``.
import requests

from az_scout.azure_api._auth import _get_headers

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0  # seconds
_MAX_BACKOFF = 16.0  # seconds


class ArmRequestError(Exception):
    """Raised when an ARM request fails after all retries."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class ArmAuthorizationError(ArmRequestError):
    """Raised on HTTP 403 — the caller lacks permissions."""


class ArmNotFoundError(ArmRequestError):
    """Raised on HTTP 404 — the resource does not exist."""


def _compute_backoff(attempt: int, retry_after: str | None = None) -> float:
    """Compute wait time from ``Retry-After`` header or exponential backoff."""
    if retry_after:
        try:
            wait = float(retry_after)
            return float(min(wait, _MAX_BACKOFF))
        except ValueError:
            pass
    return float(min(_INITIAL_BACKOFF * (2**attempt), _MAX_BACKOFF))


def _should_retry(status_code: int) -> bool:
    """Return True for status codes that warrant a retry."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    return code == 429 or code >= 500


def _arm_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Execute an ARM HTTP request with retry and structured error handling."""
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            if method == "POST":
                resp = requests.post(
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=timeout,
                )
            else:
                resp = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=timeout,
                )

            if resp.status_code == 403:
                raise ArmAuthorizationError(
                    f"Authorization failed: {resp.text[:200]}",
                    status_code=403,
                    url=url,
                )

            if resp.status_code == 404:
                raise ArmNotFoundError(
                    f"Resource not found: {url}",
                    status_code=404,
                    url=url,
                )

            if _should_retry(resp.status_code) and attempt < max_retries - 1:
                wait = _compute_backoff(attempt, resp.headers.get("Retry-After"))
                logger.warning(
                    "ARM %s %s returned %d, retrying in %.1fs (attempt %d/%d)",
                    method,
                    url[:120],
                    resp.status_code,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        except (ArmAuthorizationError, ArmNotFoundError):
            raise
        except requests.exceptions.ReadTimeout:
            last_exc = requests.exceptions.ReadTimeout()
            if attempt < max_retries - 1:
                wait = _compute_backoff(attempt)
                logger.warning(
                    "ARM %s %s timed out, retrying in %.1fs (attempt %d/%d)",
                    method,
                    url[:120],
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                continue
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = _compute_backoff(attempt)
                logger.warning(
                    "ARM %s %s failed (%s), retrying in %.1fs (attempt %d/%d)",
                    method,
                    url[:120],
                    exc,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                continue

    raise ArmRequestError(
        f"ARM {method} {url[:120]} failed after {max_retries} attempts: {last_exc}",
        url=url,
    )


def arm_get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    tenant_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """GET an ARM endpoint with authentication, retry on 429/5xx, and error handling.

    Parameters
    ----------
    url:
        Full ARM URL (e.g. ``https://management.azure.com/subscriptions/…``).
    params:
        Optional query parameters (merged with the URL).
    tenant_id:
        Scope the Bearer token to a specific Azure AD tenant.
    timeout:
        HTTP request timeout in seconds (default 30).
    max_retries:
        Maximum number of attempts (default 3).

    Returns
    -------
    dict[str, Any]
        Parsed JSON response body.

    Raises
    ------
    ArmAuthorizationError
        On HTTP 403.
    ArmNotFoundError
        On HTTP 404.
    ArmRequestError
        On other failures after all retries are exhausted.
    """
    headers = _get_headers(tenant_id)
    logger.debug("ARM GET %s (tenant=%s)", url[:120], tenant_id or "default")
    return _arm_request(
        "GET",
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        max_retries=max_retries,
    )


def arm_post(
    url: str,
    *,
    json: dict[str, Any],
    tenant_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """POST to an ARM endpoint with authentication, retry on 429/5xx, and error handling.

    Parameters
    ----------
    url:
        Full ARM URL.
    json:
        Request body as a dictionary.
    tenant_id:
        Scope the Bearer token to a specific Azure AD tenant.
    timeout:
        HTTP request timeout in seconds (default 30).
    max_retries:
        Maximum number of attempts (default 3).

    Returns
    -------
    dict[str, Any]
        Parsed JSON response body.

    Raises
    ------
    ArmAuthorizationError
        On HTTP 403.
    ArmNotFoundError
        On HTTP 404.
    ArmRequestError
        On other failures after all retries are exhausted.
    """
    headers = _get_headers(tenant_id)
    logger.debug("ARM POST %s (tenant=%s)", url[:120], tenant_id or "default")
    return _arm_request(
        "POST",
        url,
        headers=headers,
        json_body=json,
        timeout=timeout,
        max_retries=max_retries,
    )


def arm_paginate(
    url: str,
    *,
    params: dict[str, str] | None = None,
    tenant_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> list[dict[str, Any]]:
    """Fetch all pages from an ARM list endpoint and return the merged ``value`` items.

    Follows ``nextLink`` references automatically.  Each page request uses
    ``arm_get`` (with retry and 429 handling).

    Parameters
    ----------
    url:
        Initial ARM list URL.
    params:
        Optional query parameters for the **first** request only (``nextLink``
        URLs include their own query parameters).
    tenant_id:
        Scope the Bearer token to a specific Azure AD tenant.
    timeout:
        Per-page HTTP request timeout in seconds (default 30).
    max_retries:
        Maximum retry attempts per page (default 3).

    Returns
    -------
    list[dict[str, Any]]
        Merged ``value`` items from all pages.
    """
    headers = _get_headers(tenant_id)
    items: list[dict[str, Any]] = []
    page_count = 0
    current_params = params

    while url:
        data = _arm_request(
            "GET",
            url,
            headers=headers,
            params=current_params,
            timeout=timeout,
            max_retries=max_retries,
        )
        page_items = data.get("value", [])
        items.extend(page_items)
        page_count += 1
        url = data.get("nextLink", "")
        # nextLink URLs include query params — don't re-send ours
        current_params = None

    logger.debug("ARM paginate: %d pages, %d items total", page_count, len(items))
    return items


# Public aliases for the stable API
get_headers = _get_headers
"""Public alias for ``_get_headers`` — returns Bearer-token headers.

Use this when you need raw authorization headers for a custom HTTP call
(e.g. non-ARM endpoints or custom libraries).  For standard ARM calls,
prefer ``arm_get`` / ``arm_post`` / ``arm_paginate`` instead.
"""
