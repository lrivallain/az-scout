"""Server-side OAuth 2.0 authorization code flow for OBO authentication.

Replaces the client-side MSAL.js flow with standard server-side redirects:

1. ``GET /auth/login``     → redirects to Microsoft login
2. ``GET /auth/callback``  → exchanges code for tokens, creates session
3. ``GET /auth/logout``    → clears session, redirects to /
4. ``GET /api/auth/me``    → returns current user info (for navbar)
5. ``GET /api/auth/config``→ returns {enabled: bool}

Session tokens are stored server-side in memory, keyed by a random session ID.
The session ID is sent to the browser as an HTTP-only signed cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_redirect_uri(request: Request) -> str:
    """Build the /auth/callback redirect URI, respecting reverse proxy headers."""
    raw_url = str(request.url_for("auth_callback"))
    # Behind a reverse proxy (cloudflare, ACA), the internal URL is http://
    # but the external-facing URL must be https://
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and raw_url.startswith("http://"):
        raw_url = "https://" + raw_url[7:]
    logger.debug("Redirect URI: %s", raw_url)
    return raw_url


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

# In-memory session store: session_id → session data
_sessions: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 7200  # 2 hours
_COOKIE_NAME = "az_scout_sid"

# CSRF nonces for OAuth state parameter: nonce → {tenant, expires_at}
_auth_nonces: dict[str, dict[str, Any]] = {}


def _sign_session_id(session_id: str, secret: str) -> str:
    """Create a signed cookie value: session_id.signature."""
    sig = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{sig}"


def _verify_session_id(cookie_value: str, secret: str) -> str | None:
    """Verify and extract session_id from a signed cookie value."""
    if "." not in cookie_value:
        return None
    session_id, sig = cookie_value.rsplit(".", 1)
    expected = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return session_id
    return None


def _cleanup_expired() -> None:
    """Remove expired sessions."""
    now = time.time()
    expired = [k for k, v in _sessions.items() if v.get("expires_at", 0) < now]
    for k in expired:
        del _sessions[k]


def get_session(request: Request) -> dict[str, Any] | None:
    """Get the current user's session from the cookie, or None."""
    from az_scout.azure_api._obo import CLIENT_SECRET

    cookie = request.cookies.get(_COOKIE_NAME)
    if not cookie or not CLIENT_SECRET:
        return None
    session_id = _verify_session_id(cookie, CLIENT_SECRET)
    if not session_id:
        return None
    session = _sessions.get(session_id)
    if not session:
        return None
    if session.get("expires_at", 0) < time.time():
        del _sessions[session_id]
        return None
    return session


def get_session_token(request: Request) -> str | None:
    """Get the user's access token from the session, refreshing if needed."""
    session = get_session(request)
    if not session:
        return None

    # Try to get a fresh token via MSAL's token cache
    cache_data = session.get("token_cache")
    if not cache_data:
        return session.get("access_token")

    import msal

    from az_scout.azure_api._obo import CLIENT_ID, CLIENT_SECRET

    cache = msal.SerializableTokenCache()
    cache.deserialize(cache_data)

    authority = f"https://login.microsoftonline.com/{session.get('tenant_id', 'organizations')}"
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=authority,
        client_credential=CLIENT_SECRET,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if not accounts:
        return session.get("access_token")

    # acquire_token_silent handles refresh token exchange automatically
    result = app.acquire_token_silent(
        scopes=[f"api://{CLIENT_ID}/access_as_user"],
        account=accounts[0],
    )

    if result and "access_token" in result:
        # Update the cache in the session
        if cache.has_state_changed:
            session["token_cache"] = cache.serialize()
            session["access_token"] = result["access_token"]
        token: str = result["access_token"]
        return token

    # Token refresh failed — return cached token (may be expired)
    cached_token: str | None = session.get("access_token")
    return cached_token


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/auth/login", include_in_schema=False)
async def login_page(
    request: Request,
    tenant: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Show the sign-in page."""
    from az_scout.azure_api._obo import is_obo_enabled

    if not is_obo_enabled():
        return RedirectResponse("/")  # type: ignore[return-value]

    from fastapi.templating import Jinja2Templates

    from az_scout.azure_api._obo import TENANT_ID

    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "tenant": tenant or "", "home_tenant": TENANT_ID},
    )


@router.get("/auth/login/start", include_in_schema=False)
async def login_start(request: Request, tenant: str | None = None) -> RedirectResponse:
    """Redirect to Microsoft login."""
    import msal

    from az_scout.azure_api._obo import CLIENT_ID, CLIENT_SECRET, is_obo_enabled

    if not is_obo_enabled():
        return RedirectResponse("/")

    authority_tenant = tenant or "organizations"
    authority = f"https://login.microsoftonline.com/{authority_tenant}"

    redirect_uri = _build_redirect_uri(request)

    # CSRF protection: generate a random nonce for the OAuth state parameter
    nonce = secrets.token_urlsafe(24)
    _auth_nonces[nonce] = {"tenant": tenant or "", "expires_at": time.time() + 600}

    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=authority,
        client_credential=CLIENT_SECRET,
    )

    auth_url = app.get_authorization_request_url(
        scopes=[f"api://{CLIENT_ID}/access_as_user"],
        redirect_uri=redirect_uri,
        prompt="select_account",
        state=nonce,
    )

    return RedirectResponse(auth_url)


@router.get("/auth/callback", include_in_schema=False)
async def auth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse:
    """Exchange authorization code for tokens and create session."""
    if error:
        logger.warning("Auth callback error: %s — %s", error, error_description[:200])
        return RedirectResponse("/auth/login?error=auth_failed")

    if not code:
        return RedirectResponse("/auth/login")

    # CSRF validation: verify the state nonce
    nonce_data = _auth_nonces.pop(state, None) if state else None
    if not nonce_data or nonce_data.get("expires_at", 0) < time.time():
        logger.warning("Invalid or expired OAuth state nonce")
        return RedirectResponse("/auth/login?error=invalid_state")
    # Clean up expired nonces
    now = time.time()
    expired = [k for k, v in _auth_nonces.items() if v.get("expires_at", 0) < now]
    for k in expired:
        del _auth_nonces[k]

    import msal

    from az_scout.azure_api._obo import CLIENT_ID, CLIENT_SECRET

    # Use the same authority as the login request (stored in the nonce)
    login_tenant = nonce_data.get("tenant", "") or "organizations"
    authority = f"https://login.microsoftonline.com/{login_tenant}"
    redirect_uri = _build_redirect_uri(request)

    cache = msal.SerializableTokenCache()
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=authority,
        client_credential=CLIENT_SECRET,
        token_cache=cache,
    )

    result = app.acquire_token_by_authorization_code(
        code,
        scopes=[f"api://{CLIENT_ID}/access_as_user"],
        redirect_uri=redirect_uri,
    )

    if "access_token" not in result:
        error_desc = result.get("error_description", result.get("error", "unknown"))
        logger.warning("Token exchange failed: %s", error_desc[:200])
        return RedirectResponse("/auth/login?error=token_exchange_failed")

    # Extract user info from id_token_claims
    claims = result.get("id_token_claims", {})
    user_name = claims.get("name", "")
    user_email = claims.get("preferred_username", "")
    home_tenant = claims.get("tid", "")
    roles = claims.get("roles", [])

    # Admin role is only valid from the home tenant
    from az_scout.azure_api._obo import TENANT_ID

    is_admin = "Admin" in roles and home_tenant == TENANT_ID
    logger.info(
        "User signed in: %s (%s) tenant=%s roles=%s is_admin=%s (home=%s)",
        user_name,
        user_email,
        home_tenant,
        roles,
        is_admin,
        TENANT_ID,
    )

    # Create session
    _cleanup_expired()
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "access_token": result["access_token"],
        "token_cache": cache.serialize(),
        "user_name": user_name,
        "user_email": user_email,
        "tenant_id": home_tenant,
        "is_admin": is_admin,
        "roles": roles,
        "expires_at": time.time() + _SESSION_TTL,
    }

    # Set signed session cookie
    signed = _sign_session_id(session_id, CLIENT_SECRET)
    response = RedirectResponse("/")
    response.set_cookie(
        _COOKIE_NAME,
        signed,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/auth/logout", include_in_schema=False)
async def logout(request: Request) -> RedirectResponse:
    """Clear session and redirect to home."""
    from az_scout.azure_api._obo import CLIENT_SECRET

    cookie = request.cookies.get(_COOKIE_NAME)
    if cookie and CLIENT_SECRET:
        session_id = _verify_session_id(cookie, CLIENT_SECRET)
        if session_id:
            _sessions.pop(session_id, None)

    response = RedirectResponse("/")
    response.delete_cookie(_COOKIE_NAME)
    return response


@router.get("/api/auth/me", tags=["Auth"], summary="Get current user info")
async def auth_me(request: Request) -> JSONResponse:
    """Return the current user's info from the session."""
    session = get_session(request)
    if not session:
        return JSONResponse({"authenticated": False})

    return JSONResponse(
        {
            "authenticated": True,
            "name": session.get("user_name", ""),
            "email": session.get("user_email", ""),
            "tenantId": session.get("tenant_id", ""),
            "isAdmin": session.get("is_admin", False),
        }
    )


@router.get("/api/auth/config", tags=["Auth"], summary="Get auth configuration")
async def auth_config() -> JSONResponse:
    """Return whether OBO auth is enabled."""
    from az_scout.azure_api._obo import is_obo_enabled

    return JSONResponse({"enabled": is_obo_enabled()})
