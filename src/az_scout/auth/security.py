"""Authentication dependency – Entra ID or mock bypass."""

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Request

from az_scout.auth.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock user (AUTH_MODE=mock)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeUser:
    """Stub user returned when AUTH_MODE=mock."""

    oid: str = "local-dev-user"
    tid: str = "local-dev-tenant"
    name: str = "Local Developer"
    claims: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Branch on auth mode
# ---------------------------------------------------------------------------

if settings.auth_mode == "entra":
    from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer

    azure_scheme = SingleTenantAzureAuthorizationCodeBearer(
        app_client_id=settings.azure_client_id,
        tenant_id=settings.azure_tenant_id,
        scopes={settings.azure_api_scope: "Access API"},
    )

    async def get_current_user(
        user: Annotated[Any, Depends(azure_scheme)],
    ) -> dict:  # type: ignore[assignment]
        """Validate the Entra ID token and return user claims."""
        return user  # type: ignore[no-any-return,return-value]

else:
    logger.warning("AUTH_MODE=mock — authentication is DISABLED. Do NOT use this in production.")

    azure_scheme = None  # type: ignore[assignment]

    async def get_current_user(request: Request) -> dict:  # type: ignore[misc]
        """Return a fake user (mock mode)."""
        return FakeUser().__dict__


# ---------------------------------------------------------------------------
# Extract raw bearer token from request (for OBO flow)
# ---------------------------------------------------------------------------


def get_bearer_token(request: Request) -> str | None:
    """Return the raw ``Authorization: Bearer <token>`` value, or ``None``."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return None
