"""Authentication helpers for Azure ARM API calls."""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager

from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

AZURE_API_VERSION = "2022-12-01"
AZURE_MGMT_URL = "https://management.azure.com"

credential = DefaultAzureCredential()


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
    """
    kwargs: dict[str, str] = {}
    if tenant_id:
        kwargs["tenant_id"] = tenant_id
    token = credential.get_token(f"{AZURE_MGMT_URL}/.default", **kwargs)
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
        return tid
    except Exception:
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
