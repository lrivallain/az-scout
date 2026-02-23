"""On-Behalf-Of (OBO) credential exchange for Azure Resource Manager."""

import logging

from azure.identity import DefaultAzureCredential, OnBehalfOfCredential

from az_scout.auth.settings import settings

logger = logging.getLogger(__name__)

ARM_SCOPE = "https://management.azure.com/.default"


def get_arm_credential(
    user_token: str | None = None,
) -> DefaultAzureCredential | OnBehalfOfCredential:
    """Return a credential scoped to Azure Resource Manager.

    In **entra** mode the user's bearer token is exchanged via the OBO flow
    so that downstream ARM calls run with the signed-in user's identity.

    In **mock** mode a ``DefaultAzureCredential`` is returned (the
    developer must be authenticated via ``az login`` or equivalent).
    """
    if settings.auth_mode == "entra":
        if not user_token:
            raise ValueError("A user bearer token is required for the OBO flow in AUTH_MODE=entra.")
        if not settings.auth_client_secret:
            raise ValueError(
                "AUTH_CLIENT_SECRET is required for the OBO flow. "
                "Create a client secret in the Entra ID App Registration."
            )
        return OnBehalfOfCredential(
            tenant_id=settings.auth_tenant_id,
            client_id=settings.auth_client_id,
            client_secret=settings.auth_client_secret,
            user_assertion=user_token,
        )

    # Mock mode – fall back to developer's local credential
    return DefaultAzureCredential()
