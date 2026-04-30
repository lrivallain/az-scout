---
description: "MCP tool authoring conventions for az-scout core and plugins. USE WHEN editing MCP tool functions or mcp_server.py."
applyTo: "src/az_scout/mcp_server.py,**/tools.py"
---

# MCP tool conventions

## Function shape

- Plain `def` (or `async def` when awaiting I/O). Never decorate with `@mcp.tool` —
  registration is centralized in `mcp_server.py` (core) or each plugin's `get_mcp_tools()`.
- Every parameter uses `Annotated[<type>, Field(description="…")]` from Pydantic.
- Optional parameters have explicit defaults and are described as optional in the docstring.
- Return a JSON-serializable `dict` or `json.dumps(...)` string — match the style of
  surrounding tools in the same module.

## Docstring is the LLM-facing description

The first paragraph **is** what the LLM uses to decide when to call the tool. Treat it as instructions:

1. **First sentence** — what the tool returns and for which scope.
2. **Second paragraph** — when to use it; key flags and what they gate.
3. **Bullet list** — important edge cases, status enums, or units.

Example pattern (from `internal_plugins/planner/tools.py`):

```python
def get_sku_availability(
    region: Annotated[str, Field(description="Azure region name (e.g. eastus).")],
    subscription_id: Annotated[str, Field(description="Subscription ID to query.")],
    include_prices: Annotated[bool, Field(description="Include retail pricing. Defaults to false.")] = False,
) -> str:
    """Get VM SKU availability per zone for a region and subscription.

    Returns SKUs with zone availability, restrictions, capabilities, quotas,
    and a deployment confidence score (0–100).

    Set `include_prices=True` to also get retail pricing — without this flag,
    NO pricing data is returned.

    Zone status per SKU:
    - **available**: SKU can be deployed
    - **restricted**: listed but cannot be deployed
    - **unavailable**: not offered in that zone
    """
```

## Cost-gated flags

If a parameter triggers an expensive call (pricing, spot scores, full catalog),
default it to `False` and **say so** in both the docstring and the field description.
The LLM should opt in deliberately.

## Filters reduce conversational cost

When returning lists (SKUs, regions, …), expose substring / range filters so the LLM
can narrow output before printing. Document them so it picks the right one.

## Always

- Use `azure_api/` helpers — never `requests` directly.
- Type-annotate every parameter and the return.
- Keep the function in the right file: core tools in `mcp_server.py` or a helper imported there;
  feature-scoped tools in the owning plugin's `tools.py`.
- For new tools, update `README.md` MCP table and `docs/ai/mcp.md`, and add a test
  in `tests/test_mcp_server.py` (or the plugin's test module).

## Never

- Never make a tool that mutates Azure resources unless explicitly designed for it
  (and even then: dry-run by default, behind a `confirm=True` flag).
- Never include credentials, tokens, or session data in the response.
- Never raise unhandled exceptions for per-subscription failures — return them in the payload.
