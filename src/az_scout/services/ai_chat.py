"""AI Chat service – Azure OpenAI with tool-calling over az-scout functions.

Provides a streaming chat endpoint that uses Azure OpenAI to answer questions
about Azure infrastructure, calling az-scout tool functions when needed.

Requires environment variables:
    AZURE_OPENAI_ENDPOINT    – e.g. https://my-resource.openai.azure.com
    AZURE_OPENAI_API_KEY     – API key for the Azure OpenAI resource
    AZURE_OPENAI_DEPLOYMENT  – deployment name (e.g. gpt-4o)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from typing import Any

from az_scout.mcp_server import mcp as _mcp_server

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
- **Logical vs physical zones:** Azure Availability Zone numbers (1, 2, 3) are \
**logical** — they map to different physical datacenters in each subscription. \
Tools like `get_sku_availability` return **logical** zone numbers for a given \
subscription. When discussing zone recommendations, **always** call \
`get_zone_mappings` to also show the physical zone mapping and present both \
(e.g. "logical zone 2 → physical zone 1"). This avoids confusion since the same \
logical number points to different physical zones across subscriptions.
- **Capacity reservation:** When the conversation concludes on a specific VM \
SKU recommendation, proactively suggest **Azure Capacity Reservations** as a \
way to secure guaranteed capacity in the target region and zone. Mention that \
capacity reservations ensure VM allocation even during high-demand periods, and \
that they can be created for a specific SKU, region, and zone combination. Note \
that capacity reservations are billed at the PAYG rate whether or not VMs are \
deployed — but this cost is offset when VMs are running. Recommend capacity \
reservations especially when: the workload is critical, the SKU shows \
restrictions in some zones, or the confidence score is below High (< 80).
"""

# ---------------------------------------------------------------------------
# Planner system prompt  (directive, guided flow)
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """\
You are **Azure Scout Planner**, a deployment planning assistant embedded in the \
Azure Scout web tool. You help users answer **one specific planning question** \
about **VM-based workloads** — not an end-to-end deployment plan.

You are **directive**: you drive the conversation, ask structured questions, and \
make concrete recommendations based on live Azure data and documentation. Do NOT \
wait passively — after each user answer, immediately proceed.

## Scope

You focus exclusively on **virtual machine** workloads (IaaS VMs, Azure Batch, \
Spot VMs, scale sets, etc.). If the user asks about PaaS services, politely \
redirect to the Discussion mode.

## Planning paths

The user will pick **one** of these goals. Focus on that goal only.

### Path A – Find the best **region** for a VM workload
Decision criteria:
- **Capacity**: Is the desired SKU family available in the region? How many \
  zones offer it? Any restrictions?
- **Headroom for scale**: Are there enough zones with unrestricted capacity \
  to handle future growth?
- **Geo constraints**: Data residency, sovereignty, compliance requirements \
  (e.g. EU-only, specific country).
- **Latency**: Proximity to end users or dependent services.
- **SKU availability**: Confirm the target SKU (or compatible alternatives) \
  exists in the candidate regions.

Steps:
1. Ask the workload type, desired SKU family or hardware specs, and any geo / \
   latency / compliance constraints.
2. Call `list_regions` to enumerate AZ-enabled regions.
3. For the top candidate regions, call `get_sku_availability` with appropriate \
   filters — this is the **primary source** for verifying capacity, zone count, \
   and restrictions per region.
4. Use your knowledge of Azure VM families to recommend the right family for \
   the workload (e.g. M-series for SAP, NC/ND-series for ML, HB-series for HPC).
5. Present a comparison table (3–5 regions) with SKU availability, zone count, \
   restrictions, and reasoning.

### Path B – Find the right **VM SKU** in a given region
Decision criteria:
- **Hardware requirements**: vCPU count, memory, GPU, accelerated networking, \
  disk throughput, temp storage — query live data first via `get_sku_availability` \
  (returns full capabilities per SKU) and `get_sku_pricing_detail` (includes a \
  `profile` with all ARM capabilities when a `subscription_id` is provided). \
  Use your built-in knowledge of Azure VM families for workload-specific guidance \
  (e.g. M-series for SAP, NC/ND-series for ML training, HB-series for HPC).

**ARM SKU naming convention**: Azure ARM names follow the pattern \
  `Standard_<Family><vCPUs><features>_v<gen>` (e.g. `Standard_FX48mds_v2`). \
  When a user says "FX48-v2" or "D4s v5", the name filter is **fuzzy** — \
  use whatever the user typed and it will match (e.g. `name: "FX48-v2"` \
  matches `Standard_FX48mds_v2`). **Important**: always call `get_sku_availability` \
  first to discover correct ARM names, then use those exact names for \
  `get_sku_pricing_detail` and `get_spot_scores`.
- **Capacity**: Zone availability, restrictions, and confidence score.
- **Price**: Hourly cost (pay-as-you-go), and whether Spot or Azure Batch \
  pricing is attractive.
- **Spot / Batch eligibility**: Whether the SKU supports Spot VMs (check \
  spot scores / eviction rates) or is available in Azure Batch pools.

Steps:
1. Ask what the VM will run, hardware needs (vCPU, memory, GPU, etc.), budget \
   sensitivity, and whether Spot or Batch is acceptable. The region should \
   already be selected or ask the user to specify one.
2. Call `get_sku_availability` with filters (vCPU range, memory range, family) \
   and **always set `include_prices: true`** — without this flag, NO pricing \
   data is returned. This is the **primary source** for SKU specs, zone data, \
   restrictions, quota, and pricing.
3. **Sort results by PAYGO price** (ascending) and present the **cheapest 5** \
   that meet the user's requirements. Do NOT cherry-pick or skip cheaper SKUs \
   in favour of more "premium" ones — always include the lowest-price options. \
   When the user asks "cheapest", you MUST scan ALL results from the tool, not \
   just the ones you previously presented.
4. For specific SKUs, call `get_sku_pricing_detail` with a `subscription_id` \
   to get the full ARM profile (all capabilities) plus Spot/Reserved/Savings \
   Plan prices. **Never call this tool with a user-friendly name like "M128" — \
   always call `get_sku_availability` first** to discover the exact ARM names.
5. If Spot is mentioned or relevant, **always** call `get_spot_scores` for the \
   short-listed SKUs — never assume a SKU lacks Spot scores without checking.
6. Present a comparison table (up to 5 SKUs) sorted by price, with specs, \
   pricing, spot eviction rate (if applicable), confidence score, and zones.
7. When a user mentions a SKU you haven't looked up yet (e.g. "M128 seems \
   cheaper"), **always call `get_sku_availability`** with a name filter \
   (e.g. `name: "M128"`) to discover and price it — never say it's unavailable \
   without first querying live data.

### Path C – Pick the best **zone** for a VM SKU
1. The user already knows which SKU and region they want. Ask if not provided.
2. Call `get_sku_availability` to check per-zone capacity and restrictions. \
   The `zones` field lists **logical** zone numbers (subscription-specific). \
   The `restrictions` field lists logical zones where deployment is restricted.
3. **Always** call `get_zone_mappings` with the same region and subscription to \
   translate logical zones to physical zones. Present **both** in a table so the \
   user can see the mapping (e.g. "logical 2 → physical 1").
4. If Spot is mentioned or relevant, **always** call `get_spot_scores` — never \
   assume a SKU lacks Spot scores without checking.
5. Recommend the best zone(s) based on availability, restrictions, quota, \
   confidence score, and spot eviction rate. In the recommendation and summary, \
   always show both logical and physical zone numbers.

## Guidelines

- Be concise and factual. Use Markdown tables for comparisons.
- At each decision point, present options as `[[option text]]` on their own bullet \
  lines. The UI renders these as clickable chips. Limit to 4–6 options.
- Use your built-in knowledge of Azure VM families and best practices to ground \
  recommendations (e.g. which families suit SAP, ML, HPC, web workloads).
- Prices are per hour, Linux, from the Azure Retail Prices API.
- Confidence scores range 0–100 (High ≥80, Medium ≥60, Low ≥40, Very Low <40).
- **Subscription resolution:** Tools require subscription **IDs** (UUIDs), not display \
  names. When the user provides a name, call `list_subscriptions` to resolve the ID.
- **Tenant context:** The user's selected tenant ID is provided as context. All tool \
  calls automatically use this tenant.
- **Region context:** When the user picks a region, call `switch_region` to update \
  the UI, then use that region in subsequent tool calls.
- **Logical vs physical zones:** Zone numbers from `get_sku_availability` are \
  **logical** (subscription-specific). **Always** call `get_zone_mappings` to \
  translate to physical zones and present both to the user (e.g. "logical 2 → \
  physical 1"). Never show only logical zone numbers without the physical mapping.
- **Spot scores:** Never assume a VM SKU lacks Spot Placement Scores. The \
  `get_spot_scores` tool works for any SKU. Always call it when discussing \
  Spot VMs rather than guessing availability.
- **Capacity reservation:** When concluding with a SKU recommendation, \
  proactively suggest **Azure Capacity Reservations** to secure guaranteed \
  capacity in the target region and zone. Capacity reservations ensure VM \
  allocation even during high-demand periods and can be created for a specific \
  SKU, region, and zone combination. They are billed at the PAYG rate whether \
  or not VMs are deployed (cost is offset when VMs run). Recommend them \
  especially when: the workload is critical, the SKU shows restrictions in \
  some zones, or the confidence score is below High (< 80).
- If the user deviates or asks a side question, answer it briefly, then steer back.
- When done, present a clear summary of the recommendation.
"""


# ---------------------------------------------------------------------------
# Plugin chat-mode system prompts
# ---------------------------------------------------------------------------

_plugin_system_prompts: dict[str, str] = {}


def register_chat_mode_prompt(global_id: str, system_prompt: str) -> None:
    """Register a plugin-contributed system prompt for a chat mode."""
    _plugin_system_prompts[global_id] = system_prompt


def _build_system_prompt(
    tenant_id: str | None = None,
    region: str | None = None,
    subscription_id: str | None = None,
    *,
    mode: str = "discussion",
) -> str:
    """Build the system prompt, optionally including tenant, region and subscription context."""
    if mode == "planner":
        prompt = PLANNER_SYSTEM_PROMPT
    elif mode in _plugin_system_prompts:
        prompt = _plugin_system_prompts[mode]
    else:
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
    if subscription_id:
        prompt += (
            f"\n\nCurrent subscription context: The user has selected subscription "
            f"ID `{subscription_id}` in the UI. Use this subscription ID for tool "
            f"calls that require a `subscription_id` parameter, unless the user "
            f"explicitly asks you to use a different one."
        )
    return prompt


# ---------------------------------------------------------------------------
# MCP → OpenAI tool conversion
# ---------------------------------------------------------------------------

# Lazily built registry of MCP tools keyed by name
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


# Chat-only tool definitions (these tools exist only in the chat context,
# not in the MCP server — they control the web UI, not Azure APIs).
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

# ---------------------------------------------------------------------------
# Tool execution dispatcher
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


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


# ---------------------------------------------------------------------------
# Chat completion with streaming + tool calling
# ---------------------------------------------------------------------------

# Maximum tool-calling rounds to prevent infinite loops
_MAX_TOOL_ROUNDS = 10

# Retry config for Azure OpenAI 429 rate-limit errors
_MAX_RETRIES = 3
_DEFAULT_RETRY_WAIT = 10  # seconds when no Retry-After header

# Maximum characters for a single tool result in the conversation context.
# Large results (e.g. get_sku_availability with 300+ SKUs) are truncated to
# keep the total prompt under the model's token limit and avoid 429 errors.
_MAX_TOOL_RESULT_CHARS = 30_000


async def chat_stream(
    messages: list[dict[str, Any]],
    *,
    tenant_id: str | None = None,
    region: str | None = None,
    subscription_id: str | None = None,
    mode: str = "discussion",
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
        {
            "role": "system",
            "content": _build_system_prompt(tenant_id, region, subscription_id, mode=mode),
        },
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
                # Retry loop for 429 rate-limit errors
                resp_ctx = None
                for _attempt in range(_MAX_RETRIES):
                    resp_ctx = client.stream(
                        "POST",
                        url,
                        json=body,
                        headers=headers,
                    )
                    resp = await resp_ctx.__aenter__()
                    if resp.status_code == 429:
                        error_body = await resp.aread()
                        await resp_ctx.__aexit__(None, None, None)
                        retry_after = _DEFAULT_RETRY_WAIT
                        if resp.headers.get("retry-after"):
                            with contextlib.suppress(TypeError, ValueError):
                                retry_after = int(resp.headers["retry-after"])
                        if _attempt < _MAX_RETRIES - 1:
                            logger.warning(
                                "Azure OpenAI 429, retrying in %ss (attempt %s/%s)",
                                retry_after,
                                _attempt + 1,
                                _MAX_RETRIES,
                            )
                            yield _sse(
                                {
                                    "type": "status",
                                    "content": (f"Rate limited — retrying in {retry_after}s…"),
                                }
                            )
                            await asyncio.sleep(retry_after)
                            continue
                        # Last attempt still 429 — surface error
                        yield _sse({"type": "error", "content": error_body.decode()})
                        yield _sse({"type": "done"})
                        return
                    elif resp.status_code != 200:
                        error_body = await resp.aread()
                        await resp_ctx.__aexit__(None, None, None)
                        yield _sse({"type": "error", "content": error_body.decode()})
                        yield _sse({"type": "done"})
                        return
                    else:
                        break

                assert resp_ctx is not None  # for type checker

                try:
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
                finally:
                    await resp_ctx.__aexit__(None, None, None)

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
                if subscription_id:
                    if "subscription_id" in _get_tool_params(tool_name):
                        args.setdefault("subscription_id", subscription_id)
                    if "subscription_ids" in _get_tool_params(tool_name):
                        args.setdefault("subscription_ids", [subscription_id])

                # In planner mode, always include pricing data
                if mode == "planner" and tool_name == "get_sku_availability":
                    args.setdefault("include_prices", True)

                # Emit UI actions for switch tools before executing
                if tool_name == "switch_tenant" and args.get("tenant_id"):
                    yield _sse(
                        {
                            "type": "ui_action",
                            "action": "switch_tenant",
                            "tenant_id": args["tenant_id"],
                        }
                    )
                    # Update tenant_id for subsequent tool calls in this stream
                    tenant_id = args["tenant_id"]
                elif tool_name == "switch_region" and args.get("region"):
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

                # Truncate large tool results to avoid blowing up the context
                tool_content = _truncate_tool_result(result)

                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_content,
                    }
                )

        # If we exhausted rounds, signal done
        yield _sse({"type": "done"})


def _sse(data: dict[str, Any]) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"
