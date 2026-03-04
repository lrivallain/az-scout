"""Tool execution dispatcher for AI chat."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from az_scout.services.ai_chat._tools import TOOL_DEFINITIONS, _get_mcp_tools

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# Maximum characters for a single tool result in the conversation context.
# Large results (e.g. get_sku_availability with 300+ SKUs) are truncated to
# keep the total prompt under the model's token limit and avoid 429 errors.
_MAX_TOOL_RESULT_CHARS = 30_000


def _validate_subscription_id(value: str | None, param: str = "subscription_id") -> str | None:
    """Return an error JSON string if *value* is not a valid UUID, else None."""
    if value and not _UUID_RE.match(value):
        return json.dumps(
            {
                "error": f"'{param}' must be a subscription UUID "
                f"(e.g. 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'), "
                f"not a display name. You provided: '{value}'. "
                f"Call `list_subscriptions` first to resolve the name to an ID."
            }
        )
    return None


def _get_tool_params(tool_name: str) -> set[str]:
    """Return the set of parameter names for a given tool."""
    for tool in TOOL_DEFINITIONS:
        if tool["function"]["name"] == tool_name:
            return set(tool["function"]["parameters"].get("properties", {}).keys())
    return set()


def _truncate_tool_result(result: str) -> str:
    """Truncate a tool result string to fit within the context budget.

    For JSON arrays (e.g. SKU lists), keeps only the first N items that fit
    within ``_MAX_TOOL_RESULT_CHARS`` and appends a count of omitted items.
    For other results, a simple character truncation is applied.
    """
    if len(result) <= _MAX_TOOL_RESULT_CHARS:
        return result

    # Try smart truncation for JSON arrays
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return result[:_MAX_TOOL_RESULT_CHARS] + "\n… (truncated)"

    if isinstance(data, list) and len(data) > 1:
        # Keep items until we approach the budget
        kept: list[Any] = []
        current_len = 2  # for "[]"
        for item in data:
            item_json = json.dumps(item)
            # +3 for ", " separator and safety margin
            if current_len + len(item_json) + 3 > _MAX_TOOL_RESULT_CHARS - 200:
                break
            kept.append(item)
            current_len += len(item_json) + 2  # ", "
        omitted = len(data) - len(kept)
        truncated = json.dumps(kept, indent=2)
        if omitted > 0:
            truncated += (
                f"\n\n// {omitted} more items omitted "
                f"(total: {len(data)}). Use filters to narrow results."
            )
        return truncated

    # Non-array JSON or single item — just truncate
    return result[:_MAX_TOOL_RESULT_CHARS] + "\n… (truncated)"


def _execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by name and return the JSON result string.

    MCP-registered tools are called directly via their underlying function.
    Chat-only tools (switch_region, switch_tenant) are handled locally.
    """
    try:
        # Chat-only tools (not in MCP server — they control the web UI)
        if name == "switch_region":
            region_val = arguments.get("region")
            if not region_val:
                return json.dumps({"error": "Missing required parameter: region."})
            return json.dumps(
                {
                    "status": "ok",
                    "region": region_val,
                    "message": f"Switched active region to {region_val}.",
                }
            )

        if name == "switch_tenant":
            tid = arguments.get("tenant_id")
            if not tid:
                return json.dumps({"error": "Missing required parameter: tenant_id."})
            return json.dumps(
                {
                    "status": "ok",
                    "tenant_id": tid,
                    "message": f"Switched active tenant to {tid}.",
                }
            )

        # MCP-registered tools
        mcp_tools = _get_mcp_tools()
        tool = mcp_tools.get(name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {name}"})

        # Pre-validate subscription IDs
        if "subscription_id" in arguments:
            err = _validate_subscription_id(arguments["subscription_id"])
            if err:
                return err
        if "subscription_ids" in arguments:
            sub_ids = arguments["subscription_ids"]
            if isinstance(sub_ids, str):
                sub_ids = [sub_ids]
                arguments["subscription_ids"] = sub_ids
            for sid in sub_ids:
                err = _validate_subscription_id(sid, "subscription_ids[]")
                if err:
                    return err

        # Coerce single string to list for array parameters
        if "vm_sizes" in arguments and isinstance(arguments["vm_sizes"], str):
            arguments["vm_sizes"] = [arguments["vm_sizes"]]

        # Call the MCP tool function directly
        result = tool.fn(**arguments)

        # Apply chat-specific post-processing
        return _post_process_tool_result(name, arguments, result)

    except TypeError as exc:
        # Missing/invalid arguments for the MCP function
        logger.warning("Tool %s called with bad args: %s", name, exc)
        return json.dumps({"error": f"Invalid arguments for {name}: {exc}"})
    except Exception as exc:
        logger.exception("Tool execution failed: %s", name)
        return json.dumps({"error": str(exc)})


def _post_process_tool_result(name: str, arguments: dict[str, Any], result: str) -> str:
    """Apply chat-specific post-processing to an MCP tool result.

    - ``get_sku_availability``: sort by PAYGO price ascending so cheapest
      SKUs survive truncation.
    - ``get_sku_pricing_detail``: add a hint when all prices are null to
      guide the AI toward calling ``get_sku_availability`` first.
    """
    if name == "get_sku_availability" and arguments.get("include_prices"):
        # Sort by PAYGO price ascending so cheapest SKUs survive truncation.
        # SKUs without pricing go last.
        try:
            skus = json.loads(result)
            if isinstance(skus, list):
                skus.sort(
                    key=lambda s: (
                        s.get("pricing", {}).get("paygo") is None,
                        s.get("pricing", {}).get("paygo") or float("inf"),
                    )
                )
                return json.dumps(skus, indent=2)
        except (json.JSONDecodeError, ValueError):
            pass

    if name == "get_sku_pricing_detail":
        # Add hint when all prices are null to guide the AI
        try:
            data = json.loads(result)
            if isinstance(data, dict):
                price_keys = ("paygo", "spot", "ri_1y", "ri_3y", "sp_1y", "sp_3y")
                if all(data.get(k) is None for k in price_keys):
                    sku_name = arguments.get("sku_name", "unknown")
                    data["hint"] = (
                        f"No pricing found for '{sku_name}'. This usually means the "
                        "sku_name is not an exact ARM name. Call get_sku_availability "
                        "with a name filter (e.g. name='M128') to discover the correct "
                        "ARM SKU names (like Standard_M128s_v2), then retry."
                    )
                    return json.dumps(data, indent=2)
        except (json.JSONDecodeError, ValueError):
            pass

    return result
