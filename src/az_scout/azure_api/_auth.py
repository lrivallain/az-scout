"""Authentication helpers for Azure ARM API calls."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager

from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

AZURE_API_VERSION = "2022-12-01"
AZURE_MGMT_URL = "https://management.azure.com"

credential = DefaultAzureCredential()

# Token cache: (tenant_key → (token_str, expires_on_epoch))
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = threading.Lock()
_TOKEN_REFRESH_MARGIN = 120  # refresh 2 min before expiry


@contextmanager
def _suppress_stderr() -> Generator[None]:
    """Temporarily redirect OS-level stderr to ``/dev/null``.

    This silences subprocess output (e.g. from ``AzureCliCredential``)
    that bypasses Python's logging system.
    """
    original_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(original_fd, 2)
        os.close(original_fd)


def _get_headers(
    tenant_id: str | None = None,
    *,
    user_token: str | None = None,
) -> dict[str, str]:
    """Return authorization headers for ARM API calls.

    When *user_token* is provided and OBO is configured, performs an
    On-Behalf-Of token exchange so ARM calls use the **user's** RBAC
    permissions instead of the app's identity.

    When *user_token* is ``None`` (or OBO is not configured), falls back
    to ``DefaultAzureCredential`` (local dev / managed identity).

    If explicit *user_token* is not supplied, the function
    checks the per-request context set by ``AuthContextMiddleware``.

    Tokens are cached in-memory and reused until 2 minutes before expiry.
    """
    # Fall back to request-scoped auth when explicit params not provided
    if user_token is None:
        from az_scout.auth import _NO_TOKEN, get_request_auth

        user_token = get_request_auth()

        # Sentinel means "middleware ran but no user token" → block in OBO mode
        if user_token == _NO_TOKEN:
            user_token = None

    # OBO path: exchange user token for ARM token
    if user_token:
        from az_scout.azure_api._obo import is_obo_enabled, obo_exchange

        if is_obo_enabled():
            return obo_exchange(user_token, tenant_id=tenant_id)

    # When OBO is configured, require a user token for web requests.
    # If we reach here, user_token is None. Two cases:
    # 1. Middleware ran but no token (sentinel was _NO_TOKEN → cleared above) → block
    # 2. CLI mode (middleware never ran, get_request_auth returned (None, False)) → allow
    # We distinguish by checking if the raw context value was the sentinel.
    from az_scout.azure_api._obo import is_obo_enabled

    if is_obo_enabled() and not user_token:
        from az_scout.auth import _NO_TOKEN, get_request_auth

        raw_token = get_request_auth()
        # If raw value is _NO_TOKEN, it means middleware ran → web request → block
        # If raw value is None, middleware never ran → CLI mode → allow fallthrough
        if raw_token == _NO_TOKEN:
            from az_scout.azure_api._obo import OboTokenError

            raise OboTokenError("Authentication required", error_code="login_required")

    # Default path: app credential (local dev / managed identity — no OBO)
    cache_key = tenant_id or "_default_"
    with _token_lock:
        cached = _token_cache.get(cache_key)
        if cached:
            token_str, expires_on = cached
            if time.time() < expires_on - _TOKEN_REFRESH_MARGIN:
                return {
                    "Authorization": f"Bearer {token_str}",
                    "Content-Type": "application/json",
                }

        kwargs: dict[str, str] = {}
        if tenant_id:
            kwargs["tenant_id"] = tenant_id
        logger.debug("Acquiring ARM token (tenant=%s)", tenant_id or "default")
        token = credential.get_token(f"{AZURE_MGMT_URL}/.default", **kwargs)
        _token_cache[cache_key] = (token.token, token.expires_on)
        logger.debug(
            "ARM token acquired (tenant=%s, expires_on=%d)",
            tenant_id or "default",
            token.expires_on,
        )
        return {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }


def _get_default_tenant_id() -> str | None:
    """Extract the tenant ID from the current credential's token."""
    try:
        token = credential.get_token(f"{AZURE_MGMT_URL}/.default")
        payload = token.token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        tid: str | None = claims.get("tid") or claims.get("tenant_id")
        logger.debug("Default tenant ID resolved: %s", tid)
        return tid
    except Exception:
        logger.debug("Could not resolve default tenant ID", exc_info=True)
        return None


def _check_tenant_auth(tenant_id: str) -> bool:
    """Return *True* if the credential can obtain a token for *tenant_id*."""
    azure_logger = logging.getLogger("azure")
    previous_level = azure_logger.level
    azure_logger.setLevel(logging.CRITICAL)
    try:
        credential.get_token(f"{AZURE_MGMT_URL}/.default", tenant_id=tenant_id)
        return True
    except Exception:
        logger.warning("Authentication failed for tenant %s", tenant_id)
        return False
    finally:
        azure_logger.setLevel(previous_level)
