"""MCP → OpenAI tool conversion and tool registry."""

from __future__ import annotations

from typing import Any

from az_scout.mcp_server import mcp as _mcp_server

# ---------------------------------------------------------------------------
# Lazily built registry of MCP tools keyed by name
# ---------------------------------------------------------------------------

_mcp_tool_registry: dict[str, Any] | None = None


def _get_mcp_tools() -> dict[str, Any]:
    """Return a dict of MCP tool name → Tool object, built lazily."""
    global _mcp_tool_registry  # noqa: PLW0603
    if _mcp_tool_registry is None:
        _mcp_tool_registry = {t.name: t for t in _mcp_server._tool_manager.list_tools()}
    return _mcp_tool_registry


def _mcp_schema_to_openai(params: dict[str, Any]) -> dict[str, Any]:
    """Convert a FastMCP parameter JSON Schema to OpenAI function-calling format.

    Strips ``title`` fields and converts ``anyOf`` nullable unions to simple types.
    """
    properties: dict[str, Any] = {}
    for key, schema in params.get("properties", {}).items():
        prop: dict[str, Any] = {}
        if "anyOf" in schema:
            # Extract the non-null type from anyOf: [{type: X}, {type: null}]
            real_types = [t for t in schema["anyOf"] if t.get("type") != "null"]
            if real_types:
                prop["type"] = real_types[0]["type"]
                if "items" in real_types[0]:
                    prop["items"] = real_types[0]["items"]
        else:
            if "type" in schema:
                prop["type"] = schema["type"]
        if "default" in schema and schema["default"] is not None:
            prop["default"] = schema["default"]
        if "items" in schema:
            prop["items"] = schema["items"]
        if "description" in schema:
            prop["description"] = schema["description"]
        properties[key] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": params.get("required", []),
    }


# ---------------------------------------------------------------------------
# Chat-only tool definitions (these tools exist only in the chat context,
# not in the MCP server — they control the web UI, not Azure APIs).
# ---------------------------------------------------------------------------

_CHAT_ONLY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "switch_region",
            "description": (
                "Switch the active Azure region in the UI. This updates the region "
                "selector and refreshes downstream views (topology, SKU table, planner). "
                "Use the region name (e.g. eastus, francecentral), not display name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "Azure region name (e.g. eastus, westeurope).",
                    },
                },
                "required": ["region"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_tenant",
            "description": (
                "Switch the active Azure tenant in the UI. This updates the tenant "
                "selector and resets downstream state (subscriptions, regions, etc.). "
                "Call list_tenants first to find valid tenant IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant_id": {
                        "type": "string",
                        "description": "The tenant ID (UUID) to switch to.",
                    },
                },
                "required": ["tenant_id"],
            },
        },
    },
]


def _build_openai_tools() -> list[dict[str, Any]]:
    """Build OpenAI function-calling tool definitions from MCP tools + chat-only tools."""
    tools: list[dict[str, Any]] = []

    for name, mcp_tool in _get_mcp_tools().items():
        description = mcp_tool.description
        params = _mcp_schema_to_openai(mcp_tool.parameters)

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": params,
                },
            }
        )

    tools.extend(_CHAT_ONLY_TOOLS)
    return tools


TOOL_DEFINITIONS: list[dict[str, Any]] = _build_openai_tools()


def refresh_tool_definitions() -> None:
    """Rebuild TOOL_DEFINITIONS after plugins have registered MCP tools.

    Called by :func:`az_scout.plugins.register_plugins` so that plugin tools
    become available to the AI chat assistant.
    """
    global _mcp_tool_registry  # noqa: PLW0603
    _mcp_tool_registry = None  # invalidate cache so _get_mcp_tools() re-reads
    TOOL_DEFINITIONS.clear()
    TOOL_DEFINITIONS.extend(_build_openai_tools())
