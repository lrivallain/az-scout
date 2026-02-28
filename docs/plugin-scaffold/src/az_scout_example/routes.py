"""Example API routes for the plugin."""

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
    """
    parts = ["Hello from the example plugin!"]
    if tenant:
        parts.append(f"Tenant: {tenant}")
    if region:
        parts.append(f"Region: {region}")
    if subscription_name:
        parts.append(f"Subscription: {subscription_name} ({subscription_id})")
    return {"message": " | ".join(parts)}
