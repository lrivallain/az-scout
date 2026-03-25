"""On-Behalf-Of (OBO) token exchange for delegated user access.

When a user signs in via the frontend (MSAL.js), their access token is
exchanged for an ARM-scoped token using the OAuth 2.0 OBO flow.  This
lets az-scout call Azure ARM APIs **with the user's own RBAC permissions**
instead of the app's managed identity.

The OBO flow is **optional** — when ``AZ_SCOUT_CLIENT_ID`` is not set,
the app falls back to ``DefaultAzureCredential`` (local dev / managed
identity).

Environment variables
---------------------
AZ_SCOUT_CLIENT_ID : str
    App Registration client (application) ID.
AZ_SCOUT_CLIENT_SECRET : str
    App Registration client secret.
AZ_SCOUT_TENANT_ID : str
    Home tenant ID of the App Registration.
    For multi-tenant apps this is the "home" tenant; OBO works across
    tenants once admin consent is granted.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Configuration from env vars
CLIENT_ID = os.environ.get("AZ_SCOUT_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AZ_SCOUT_CLIENT_SECRET", "")
TENANT_ID = os.environ.get("AZ_SCOUT_TENANT_ID", "")

ARM_SCOPE = "https://management.azure.com/.default"


def is_obo_enabled() -> bool:
    """Return True if OBO auth is configured via environment variables."""
    return bool(CLIENT_ID and CLIENT_SECRET)


# Per-tenant MSAL ConfidentialClientApplication cache
_msal_apps: dict[str, Any] = {}
_msal_lock = threading.Lock()

# OBO token cache: cache_key → (token_str, expires_on)
_obo_cache: dict[str, tuple[str, float]] = {}
_obo_lock = threading.Lock()
_OBO_REFRESH_MARGIN = 120  # refresh 2 min before expiry


def _extract_tid(token: str) -> str | None:
    """Extract the tenant ID (tid) from a JWT token without full validation."""
    import base64
    import json as json_mod

    try:
        # JWT has 3 parts: header.payload.signature
        payload = token.split(".")[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        claims = json_mod.loads(decoded)
        return claims.get("tid")  # type: ignore[no-any-return]
    except Exception:
        return None


def _get_msal_app(tenant_id: str | None = None) -> Any:
    """Get or create a per-tenant MSAL ConfidentialClientApplication."""
    import msal as msal_lib

    target = tenant_id or TENANT_ID or "organizations"
    with _msal_lock:
        if target not in _msal_apps:
            _msal_apps[target] = msal_lib.ConfidentialClientApplication(
                CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{target}",
                client_credential=CLIENT_SECRET,
            )
        return _msal_apps[target]


def obo_exchange(
    user_token: str,
    *,
    tenant_id: str | None = None,
) -> dict[str, str]:
    """Exchange a user's access token for an ARM-scoped token via OBO.

    Uses the ``msal`` library's ``acquire_token_on_behalf_of`` which properly
    handles CP1 capability declaration, claims challenges, and the full OBO
    protocol (including token caching and retry logic).

    Parameters
    ----------
    user_token:
        The Bearer token from the user's frontend session (Token A).
    tenant_id:
        Target tenant for the ARM token.  Defaults to the home tenant.

    Returns
    -------
    dict with ``Authorization`` and ``Content-Type`` headers.

    Raises
    ------
    OboTokenError
        If the OBO exchange fails (consent missing, token invalid, etc.).
    """
    # Use a hash of the full token + tenant as cache key.
    # JWT headers are identical across users, so we must hash the entire
    # token (including payload + signature) to avoid cache collisions.
    cache_key = hashlib.sha256(f"{user_token}:{tenant_id or ''}".encode()).hexdigest()[:24]

    with _obo_lock:
        cached = _obo_cache.get(cache_key)
        if cached:
            token_str, expires_on = cached
            if time.time() < expires_on - _OBO_REFRESH_MARGIN:
                return {
                    "Authorization": f"Bearer {token_str}",
                    "Content-Type": "application/json",
                }

    # When no explicit tenant is provided, extract the user's home tenant
    # from the JWT token so OBO exchanges against the correct tenant.
    # This avoids AADSTS90072 for users from non-home tenants.
    effective_tenant = tenant_id
    if not effective_tenant:
        effective_tenant = _extract_tid(user_token)

    target = effective_tenant or TENANT_ID or "organizations"
    logger.debug("OBO exchange via MSAL (tenant=%s)", target)

    app = _get_msal_app(effective_tenant)
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_token,
        scopes=[ARM_SCOPE],
    )

    if "access_token" in result:
        access_token: str = result["access_token"]
        expires_in: int = result.get("expires_in", 3600)
        with _obo_lock:
            _obo_cache[cache_key] = (access_token, time.time() + expires_in)
        logger.debug("OBO token acquired (tenant=%s, expires_in=%ds)", target, expires_in)
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    # OBO failed — extract error details
    error_code = result.get("error", "unknown")
    error_desc = result.get("error_description", "")
    claims_value = result.get("claims", "")

    logger.warning("OBO exchange failed (%s): %s", error_code, error_desc[:200])
    logger.debug("OBO error result keys: %s", list(result.keys()))

    # Consent errors
    if "AADSTS65001" in error_desc:
        consent_url = (
            f"https://login.microsoftonline.com/{target}/adminconsent?client_id={CLIENT_ID}"
        )
        raise OboTokenError(
            f"Admin consent required in tenant {target}. "
            f"Ask a tenant admin to visit: {consent_url}",
            error_code=error_code,
        )

    # MFA / step-up authentication — return claims challenge to frontend
    if "AADSTS50076" in error_desc or "AADSTS50079" in error_desc:
        if claims_value:
            logger.debug("MSAL returned claims challenge: %s", claims_value[:200])
            raise OboTokenError(
                f"MFA required for tenant {target}",
                error_code="claims_challenge",
                claims=claims_value,
            )
        # No claims available — OBO cannot relay the MFA requirement.
        # Tell the frontend to acquire an ARM token directly.
        logger.debug("No claims in MSAL error response; requesting direct ARM auth")
        raise OboTokenError(
            f"MFA required for tenant {target} (direct ARM auth needed)",
            error_code="mfa_direct_auth",
        )

    raise OboTokenError(
        f"OBO exchange failed: {error_code} — {error_desc[:200]}",
        error_code=error_code,
    )


class OboTokenError(Exception):
    """Raised when the OBO token exchange fails."""

    def __init__(self, message: str, *, error_code: str = "", claims: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.claims = claims
