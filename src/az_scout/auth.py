"""FastAPI dependency and context for user authentication.

Provides two mechanisms:

1. **Explicit**: ``get_user_token(request)``
   — used by discovery routes that pass tokens explicitly.

2. **Implicit (request context)**: ``set_request_auth()`` / ``get_request_auth()``
   — set automatically by ``AuthContextMiddleware`` so that deeply-nested
   ``_get_headers()`` calls can read the user token without every
   intermediate function signature needing a ``user_token`` parameter.

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

# Module-level fallback for code running on raw ThreadPoolExecutor threads
# (e.g. plugins that spawn their own thread pools).
_global_user_token: str | None = None


def set_request_auth(user_token: str | None) -> contextvars.Token[str | None]:
    """Store auth info for the current request. Returns token for cleanup."""
    global _global_user_token  # noqa: PLW0603
    # Use sentinel when middleware runs but there's no token — distinguishes
    # "unauthenticated web request" from "CLI mode" (where default is None).
    store_value = user_token if user_token else _NO_TOKEN
    _global_user_token = store_value
    return _user_token_var.set(store_value)


def clear_request_auth(token: contextvars.Token[str | None]) -> None:
    """Remove auth info after request completes."""
    global _global_user_token  # noqa: PLW0603
    _global_user_token = None
    _user_token_var.reset(token)


def get_request_auth() -> str | None:
    """Read auth info for the current request.

    Tries the context var first (works with asyncio.to_thread).
    Falls back to the module global (works with raw ThreadPoolExecutor).
    Returns the sentinel _NO_TOKEN if the middleware ran but no token was provided.
    """
    token = _user_token_var.get()
    if token is not None:
        return token
    # Fallback: module global (for raw thread pool workers)
    return _global_user_token


def get_user_token(request: Request) -> str | None:
    """Extract user token from Authorization header or session cookie."""
    # 1. Authorization header (MCP / direct API clients)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    # 2. Session cookie (web browser via server-side login)
    from az_scout.routes.auth import get_session_token

    return get_session_token(request)
