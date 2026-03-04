"""Streaming chat completion with tool calling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from az_scout.services.ai_chat._config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
)
from az_scout.services.ai_chat._dispatch import (
    _execute_tool,
    _get_tool_params,
    _truncate_tool_result,
)
from az_scout.services.ai_chat._prompts import _build_system_prompt
from az_scout.services.ai_chat._tools import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

# Maximum tool-calling rounds to prevent infinite loops
_MAX_TOOL_ROUNDS = 10

# Retry config for Azure OpenAI 429 rate-limit errors
_MAX_RETRIES = 3
_DEFAULT_RETRY_WAIT = 10  # seconds when no Retry-After header

# Maximum characters sent to the frontend UI for tool result inspection.
# Larger than the summary (200 chars) but smaller than the LLM context budget.
_MAX_TOOL_UI_CHARS = 10_000


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
    - ``{"type": "tool_result", "name": "...", "arguments": "...", "content": "..."}``
      – tool result with I/O data for UI inspection
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

                # Send result to the UI for tool inspection
                ui_content = (
                    result[:_MAX_TOOL_UI_CHARS] + "\n… (truncated)"
                    if len(result) > _MAX_TOOL_UI_CHARS
                    else result
                )
                yield _sse(
                    {
                        "type": "tool_result",
                        "name": tool_name,
                        "arguments": json.dumps(args),
                        "content": ui_content,
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
