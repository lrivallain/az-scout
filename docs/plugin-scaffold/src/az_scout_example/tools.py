"""Example MCP tools for the plugin.

To make authenticated Azure ARM API calls, use the public helpers::

    from az_scout.azure_api import arm_get, arm_paginate, get_headers

    # GET with auth + retry + 429/5xx handling
    data = arm_get(url, tenant_id=tenant_id)

    # Paginated GET (follows nextLink automatically)
    items = arm_paginate(url, tenant_id=tenant_id)

    # Raw headers for non-ARM endpoints
    headers = get_headers(tenant_id)
"""


def example_tool(name: str) -> str:
    """Greet someone by name. This tool is exposed via the MCP server."""
    return f"Hello, {name}! This is the example plugin tool."
