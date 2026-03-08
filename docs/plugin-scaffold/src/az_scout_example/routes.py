"""Example API routes for the plugin."""

from az_scout.plugin_api import PluginValidationError
from fastapi import APIRouter

router = APIRouter()


@router.get("/hello")
async def hello(
    subscription_name: str = "",
    subscription_id: str = "",
    tenant: str = "",
    region: str = "",
) -> dict[str, str]:
    """Example endpoint — available at /plugins/example/hello.

    Receives the current tenant, region and subscription context
    from the plugin's frontend.

    Error handling:
        Raise ``PluginValidationError`` for invalid input (HTTP 422)
        or ``PluginUpstreamError`` for upstream API failures (HTTP 502).
        The core app catches these and returns a consistent JSON response.
    """
    if not region:
        raise PluginValidationError("Region is required")

    parts = ["Hello from the example plugin!"]
    if tenant:
        parts.append(f"Tenant: {tenant}")
    parts.append(f"Region: {region}")
    if subscription_name:
        parts.append(f"Subscription: {subscription_name} ({subscription_id})")
    return {"message": " | ".join(parts)}
