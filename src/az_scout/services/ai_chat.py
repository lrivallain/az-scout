"""AI Chat service – Azure OpenAI with tool-calling over az-scout functions.

Provides a streaming chat endpoint that uses Azure OpenAI to answer questions
about Azure infrastructure, calling az-scout tool functions when needed.

Requires environment variables:
    AZURE_OPENAI_ENDPOINT    – e.g. https://my-resource.openai.azure.com
    AZURE_OPENAI_API_KEY     – API key for the Azure OpenAI resource
    AZURE_OPENAI_DEPLOYMENT  – deployment name (e.g. gpt-4o)
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from az_scout import azure_api
from az_scout.services.capacity_confidence import compute_capacity_confidence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")


def is_chat_enabled() -> bool:
    """Return True if all required Azure OpenAI env vars are set."""
    return bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are **Azure Scout Assistant**, an AI assistant embedded in the Azure Scout \
web tool. You help users explore Azure infrastructure: tenants, subscriptions, \
regions, availability zone mappings, VM SKU availability, pricing, and deployment \
planning.

You have access to live Azure data through tool functions. Use them to answer \
questions accurately. When a user asks about SKUs, zones, pricing, or capacity, \
call the appropriate tool rather than guessing.

Guidelines:
- Be concise and factual. Use tables (Markdown) for structured data.
- When listing many SKUs, summarise the key ones rather than dumping everything.
- If a tool call fails, explain the error and suggest next steps.
- You can combine multiple tool calls to answer complex questions.
- Prices are per hour, Linux, from the Azure Retail Prices API.
- Confidence scores range 0–100 (High ≥80, Medium ≥60, Low ≥40, Very Low <40).
- **Interactive choices:** When asking the user to choose (e.g. subscription, region, \
SKU), present each option as `[[option text]]` on its own bullet line. The UI renders \
these as clickable chips. Example:
  - [[subscription-1-name (xxxxxxxx…)]]
  - [[subscription-2-name (yyyyyyyy…)]]
- **Subscription resolution:** Tools require subscription **IDs** (UUIDs), not display \
names. When the user provides a subscription **name**, first call `list_subscriptions` \
to find the matching subscription ID, then use that ID in subsequent tool calls.
- **Tenant context:** The user's currently selected tenant ID is provided as context. \
All your tool calls automatically use this tenant unless you specify a different one. \
If the user asks to switch tenant, call `list_tenants` to find available tenants, then \
call `switch_tenant` with the desired tenant ID. This will update the UI tenant selector \
automatically. Future tool calls will use the new tenant.
- **Region context:** When the user mentions a region or asks to switch region, call \
`switch_region` with the region **name** (e.g. `eastus`, `francecentral`). This updates \
the region selector in the UI. You can call `list_regions` first to find valid names.
"""


def _build_system_prompt(
    tenant_id: str | None = None,
    region: str | None = None,
) -> str:
    """Build the system prompt, optionally including tenant and region context."""
    prompt = SYSTEM_PROMPT
    if tenant_id:
        prompt += (
            f"\n\nCurrent tenant context: The user has selected tenant ID "
            f"`{tenant_id}` in the UI. Tool calls will automatically use this "
            f"tenant unless you override with a different `tenant_id` argument."
        )
    else:
        prompt += (
            "\n\nNo tenant is currently selected. If the user needs to query a "
            "specific tenant, advise them to select one from the tenant dropdown, "
            "or ask them which tenant they want to use."
        )
    if region:
        prompt += (
            f"\n\nCurrent region context: The user has selected region "
            f"`{region}` in the UI. Tool calls that accept a `region` parameter "
            f"will automatically use this region unless you specify a different one."
        )
    return prompt


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_tenants",
            "description": (
                "List Azure AD tenants accessible by the current credential, "
                "with authentication status and default tenant ID."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_subscriptions",
            "description": "List enabled Azure subscriptions, sorted alphabetically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant_id": {
                        "type": "string",
                        "description": "Optional tenant ID to scope the query.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_regions",
            "description": "List Azure regions that support Availability Zones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_id": {
                        "type": "string",
                        "description": "Subscription ID. Auto-discovered if omitted.",
                    },
                    "tenant_id": {
                        "type": "string",
                        "description": "Optional tenant ID.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_zone_mappings",
            "description": (
                "Get logical-to-physical Availability Zone mappings for a region. "
                "Shows how each subscription maps logical zone numbers to physical zones."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "Azure region name (e.g. eastus).",
                    },
                    "subscription_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of subscription IDs to query.",
                    },
                    "tenant_id": {
                        "type": "string",
                        "description": "Optional tenant ID.",
                    },
                },
                "required": ["region", "subscription_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sku_availability",
            "description": (
                "Get VM SKU availability per zone for a region and subscription. "
                "Returns SKUs with zone availability, restrictions, capabilities, "
                "quotas, and optionally pricing and confidence scores. "
                "Use filter parameters to reduce output size."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "Azure region name (e.g. eastus).",
                    },
                    "subscription_id": {
                        "type": "string",
                        "description": "Subscription ID to query.",
                    },
                    "tenant_id": {"type": "string", "description": "Optional tenant ID."},
                    "resource_type": {
                        "type": "string",
                        "description": "ARM resource type (default: virtualMachines).",
                        "default": "virtualMachines",
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Substring filter on SKU name (case-insensitive). "
                            "E.g. 'D2s' matches Standard_D2s_v3."
                        ),
                    },
                    "family": {
                        "type": "string",
                        "description": "Substring filter on SKU family (case-insensitive).",
                    },
                    "min_vcpus": {
                        "type": "integer",
                        "description": "Minimum vCPU count (inclusive).",
                    },
                    "max_vcpus": {
                        "type": "integer",
                        "description": "Maximum vCPU count (inclusive).",
                    },
                    "min_memory_gb": {
                        "type": "number",
                        "description": "Minimum memory in GB (inclusive).",
                    },
                    "max_memory_gb": {
                        "type": "number",
                        "description": "Maximum memory in GB (inclusive).",
                    },
                    "include_prices": {
                        "type": "boolean",
                        "description": "Include retail pricing (default: false).",
                        "default": False,
                    },
                    "currency_code": {
                        "type": "string",
                        "description": "Currency code for prices (default: USD).",
                        "default": "USD",
                    },
                },
                "required": ["region", "subscription_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spot_scores",
            "description": (
                "Get Spot Placement Scores for VM sizes in a region. "
                "Returns High / Medium / Low score per VM size."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Azure region name."},
                    "subscription_id": {"type": "string", "description": "Subscription ID."},
                    "vm_sizes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of VM size names.",
                    },
                    "instance_count": {
                        "type": "integer",
                        "description": "Number of instances (default: 1).",
                        "default": 1,
                    },
                    "tenant_id": {"type": "string", "description": "Optional tenant ID."},
                },
                "required": ["region", "subscription_id", "vm_sizes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sku_pricing_detail",
            "description": (
                "Get detailed Linux pricing for a single VM SKU. "
                "Returns PAYGO, Spot, Reserved (1Y/3Y), and Savings Plan (1Y/3Y) prices per hour."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Azure region name."},
                    "sku_name": {
                        "type": "string",
                        "description": "ARM SKU name (e.g. Standard_D2s_v5).",
                    },
                    "currency_code": {
                        "type": "string",
                        "description": "Currency code (default: USD).",
                        "default": "USD",
                    },
                    "subscription_id": {
                        "type": "string",
                        "description": "Optional subscription ID for VM profile data.",
                    },
                    "tenant_id": {"type": "string", "description": "Optional tenant ID."},
                },
                "required": ["region", "sku_name"],
            },
        },
    },
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

# ---------------------------------------------------------------------------
# Tool execution dispatcher
# ---------------------------------------------------------------------------


def _get_tool_params(tool_name: str) -> set[str]:
    """Return the set of parameter names for a given tool."""
    for tool in TOOL_DEFINITIONS:
        if tool["function"]["name"] == tool_name:
            return set(tool["function"]["parameters"].get("properties", {}).keys())
    return set()


def _execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by name and return the JSON result string."""
    try:
        if name == "list_tenants":
            return json.dumps(azure_api.list_tenants(), indent=2)

        elif name == "list_subscriptions":
            return json.dumps(
                azure_api.list_subscriptions(arguments.get("tenant_id")),
                indent=2,
            )

        elif name == "list_regions":
            return json.dumps(
                azure_api.list_regions(
                    arguments.get("subscription_id"),
                    arguments.get("tenant_id"),
                ),
                indent=2,
            )

        elif name == "get_zone_mappings":
            return json.dumps(
                azure_api.get_mappings(
                    arguments["region"],
                    arguments["subscription_ids"],
                    arguments.get("tenant_id"),
                ),
                indent=2,
            )

        elif name == "get_sku_availability":
            skus = azure_api.get_skus(
                arguments["region"],
                arguments["subscription_id"],
                arguments.get("tenant_id"),
                arguments.get("resource_type", "virtualMachines"),
                name=arguments.get("name"),
                family=arguments.get("family"),
                min_vcpus=arguments.get("min_vcpus"),
                max_vcpus=arguments.get("max_vcpus"),
                min_memory_gb=arguments.get("min_memory_gb"),
                max_memory_gb=arguments.get("max_memory_gb"),
            )
            azure_api.enrich_skus_with_quotas(
                skus,
                arguments["region"],
                arguments["subscription_id"],
                arguments.get("tenant_id"),
            )
            include_prices = arguments.get("include_prices", False)
            currency = arguments.get("currency_code", "USD")
            if include_prices:
                azure_api.enrich_skus_with_prices(skus, arguments["region"], currency)

            # Compute confidence scores
            for sku in skus:
                caps = sku.get("capabilities", {})
                quota = sku.get("quota", {})
                pricing = sku.get("pricing", {})
                try:
                    vcpus = int(caps.get("vCPUs", 0))
                except (TypeError, ValueError):
                    vcpus = None
                remaining = quota.get("remaining")
                sku["confidence"] = compute_capacity_confidence(
                    vcpus=vcpus,
                    zones_supported_count=len(sku.get("zones", [])),
                    restrictions_present=len(sku.get("restrictions", [])) > 0,
                    quota_remaining_vcpu=remaining,
                    paygo_price=pricing.get("paygo") if pricing else None,
                    spot_price=pricing.get("spot") if pricing else None,
                )
            return json.dumps(skus, indent=2)

        elif name == "get_spot_scores":
            return json.dumps(
                azure_api.get_spot_placement_scores(
                    arguments["region"],
                    arguments["subscription_id"],
                    arguments["vm_sizes"],
                    arguments.get("instance_count", 1),
                    arguments.get("tenant_id"),
                ),
                indent=2,
            )

        elif name == "switch_region":
            # Validated client-side – just acknowledge
            return json.dumps(
                {
                    "status": "ok",
                    "region": arguments["region"],
                    "message": f"Switched active region to {arguments['region']}.",
                }
            )

        elif name == "switch_tenant":
            # Validated client-side – just acknowledge
            return json.dumps(
                {
                    "status": "ok",
                    "tenant_id": arguments["tenant_id"],
                    "message": f"Switched active tenant to {arguments['tenant_id']}.",
                }
            )

        elif name == "get_sku_pricing_detail":
            result = azure_api.get_sku_pricing_detail(
                arguments["region"],
                arguments["sku_name"],
                arguments.get("currency_code", "USD"),
            )
            sub_id = arguments.get("subscription_id")
            if sub_id:
                profile = azure_api.get_sku_profile(
                    arguments["region"],
                    sub_id,
                    arguments["sku_name"],
                    arguments.get("tenant_id"),
                )
                if profile is not None:
                    result["profile"] = profile
            return json.dumps(result, indent=2)

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as exc:
        logger.exception("Tool execution failed: %s", name)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Chat completion with streaming + tool calling
# ---------------------------------------------------------------------------

# Maximum tool-calling rounds to prevent infinite loops
_MAX_TOOL_ROUNDS = 10


async def chat_stream(
    messages: list[dict[str, Any]],
    *,
    tenant_id: str | None = None,
    region: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream chat completions from Azure OpenAI with tool-calling support.

    Yields SSE-formatted lines: ``data: {...}\\n\\n``

    Each data payload is one of:
    - ``{"type": "delta", "content": "..."}``  – streamed text chunk
    - ``{"type": "tool_call", "name": "...", "arguments": "..."}``  – tool invocation info
    - ``{"type": "tool_result", "name": "...", "summary": "..."}``  – tool result summary
    - ``{"type": "error", "content": "..."}``  – error
    - ``{"type": "done"}``  – stream finished
    """
    import httpx

    full_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(tenant_id, region)},
        *messages,
    ]

    url = (
        f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{AZURE_OPENAI_DEPLOYMENT}/chat/completions"
        f"?api-version={AZURE_OPENAI_API_VERSION}"
    )
    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_API_KEY,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        for _round in range(_MAX_TOOL_ROUNDS):
            body = {
                "messages": full_messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "stream": True,
            }

            try:
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers=headers,
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        yield _sse({"type": "error", "content": error_body.decode()})
                        yield _sse({"type": "done"})
                        return

                    # Accumulate streamed response
                    content_parts: list[str] = []
                    tool_calls: dict[int, dict[str, str]] = {}
                    finish_reason: str | None = None

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason") or finish_reason

                        # Text content
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                            yield _sse({"type": "delta", "content": delta["content"]})

                        # Tool calls (streamed incrementally)
                        for tc in delta.get("tool_calls", []):
                            idx = tc["index"]
                            if idx not in tool_calls:
                                tool_calls[idx] = {
                                    "id": tc.get("id", ""),
                                    "name": tc.get("function", {}).get("name", ""),
                                    "arguments": "",
                                }
                            if tc.get("id"):
                                tool_calls[idx]["id"] = tc["id"]
                            if tc.get("function", {}).get("name"):
                                tool_calls[idx]["name"] = tc["function"]["name"]
                            if tc.get("function", {}).get("arguments"):
                                tool_calls[idx]["arguments"] += tc["function"]["arguments"]

            except httpx.HTTPError as exc:
                yield _sse({"type": "error", "content": f"HTTP error: {exc}"})
                yield _sse({"type": "done"})
                return

            # If no tool calls, we're done
            if finish_reason != "tool_calls" or not tool_calls:
                yield _sse({"type": "done"})
                return

            # Execute tool calls and continue the conversation
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            full_content = "".join(content_parts)
            if full_content:
                assistant_msg["content"] = full_content
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls.values()
            ]
            full_messages.append(assistant_msg)

            for tc in tool_calls.values():
                tool_name = tc["name"]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}

                yield _sse(
                    {
                        "type": "tool_call",
                        "name": tool_name,
                        "arguments": json.dumps(args),
                    }
                )

                # Auto-inject tenant_id and region if not explicitly specified
                if tenant_id and "tenant_id" in _get_tool_params(tool_name):
                    args.setdefault("tenant_id", tenant_id)
                if region and "region" in _get_tool_params(tool_name):
                    args.setdefault("region", region)

                # Emit UI actions for switch tools before executing
                if tool_name == "switch_tenant":
                    yield _sse(
                        {
                            "type": "ui_action",
                            "action": "switch_tenant",
                            "tenant_id": args["tenant_id"],
                        }
                    )
                    # Update tenant_id for subsequent tool calls in this stream
                    tenant_id = args["tenant_id"]
                elif tool_name == "switch_region":
                    yield _sse(
                        {
                            "type": "ui_action",
                            "action": "switch_region",
                            "region": args["region"],
                        }
                    )
                    # Update region for subsequent tool calls in this stream
                    region = args["region"]

                result = _execute_tool(tool_name, args)

                # Send a brief summary to the UI (truncate large results)
                summary = result[:200] + "…" if len(result) > 200 else result
                yield _sse(
                    {
                        "type": "tool_result",
                        "name": tool_name,
                        "summary": summary,
                    }
                )

                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )

        # If we exhausted rounds, signal done
        yield _sse({"type": "done"})


def _sse(data: dict[str, Any]) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"
