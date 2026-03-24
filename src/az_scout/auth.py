"""FastAPI dependency and context for user authentication.

Provides two mechanisms:

1. **Explicit**: ``get_user_token(request)`` / ``is_direct_arm(request)``
   — used by discovery routes that pass tokens explicitly.

2. **Implicit (request context)**: ``set_request_auth()`` / ``get_request_auth()``
   — set automatically by ``AuthContextMiddleware`` so that deeply-nested
   ``_get_headers()`` calls can read the user token without every
   intermediate function signature needing ``user_token`` / ``direct_arm``
   parameters.

Uses both a module-level global (for sync-blocking plugin code on worker
threads spawned by ThreadPoolExecutor) and a contextvars.ContextVar (for
``asyncio.to_thread`` which copies context to worker threads).
"""

from __future__ import annotations

import contextvars

from fastapi import Request

# Sentinel value meaning "middleware has run but no token was provided".
_NO_TOKEN = "__no_token__"

# ContextVar — copied by asyncio.to_thread into worker threads.
# Default is None (CLI mode). Middleware sets to token string or _NO_TOKEN.
_user_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_user_token_var", default=None
)
_direct_arm_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_direct_arm_var", default=False
)

# Module-level fallback for code running on raw ThreadPoolExecutor threads
# (e.g. plugins that spawn their own thread pools).
_global_user_token: str | None = None
_global_direct_arm: bool = False


def set_request_auth(
    user_token: str | None, direct_arm: bool
) -> tuple[contextvars.Token[str | None], contextvars.Token[bool]]:
    """Store auth info for the current request. Returns tokens for cleanup."""
    global _global_user_token, _global_direct_arm  # noqa: PLW0603
    # Use sentinel when middleware runs but there's no token — distinguishes
    # "unauthenticated web request" from "CLI mode" (where default is None).
    store_value = user_token if user_token else _NO_TOKEN
    _global_user_token = store_value
    _global_direct_arm = direct_arm
    tok = _user_token_var.set(store_value)
    drm = _direct_arm_var.set(direct_arm)
    return tok, drm


def clear_request_auth(
    tokens: tuple[contextvars.Token[str | None], contextvars.Token[bool]],
) -> None:
    """Remove auth info after request completes."""
    global _global_user_token, _global_direct_arm  # noqa: PLW0603
    _global_user_token = None
    _global_direct_arm = False
    _user_token_var.reset(tokens[0])
    _direct_arm_var.reset(tokens[1])


def get_request_auth() -> tuple[str | None, bool]:
    """Read auth info for the current request.

    Tries the context var first (works with asyncio.to_thread).
    Falls back to the module global (works with raw ThreadPoolExecutor).
    Returns the sentinel _NO_TOKEN if the middleware ran but no token was provided.
    """
    token = _user_token_var.get()
    if token is not None:
        return token, _direct_arm_var.get()
    # Fallback: module global (for raw thread pool workers)
    return _global_user_token, _global_direct_arm


def get_user_token(request: Request) -> str | None:
    """Extract user token from Authorization header or session cookie."""
    # 1. Authorization header (MCP / direct API clients)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    # 2. Session cookie (web browser via server-side login)
    from az_scout.routes.auth import get_session_token

    return get_session_token(request)


def is_direct_arm(request: Request) -> bool:
    """Return True if the request carries a direct ARM token (MFA fallback)."""
    return request.headers.get("X-Direct-ARM") == "true"
