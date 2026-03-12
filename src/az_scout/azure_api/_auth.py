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


def _get_headers(tenant_id: str | None = None) -> dict[str, str]:
    """Return authorization headers using *DefaultAzureCredential*.

    When *tenant_id* is provided the token is scoped to that tenant.
    Tokens are cached in-memory and reused until 2 minutes before expiry.
    """
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
