"""Non-streaming chat completion with tool calling.

Provides ``ai_complete()`` — a single-shot completion helper that runs the full
tool-calling loop server-side and returns the final response as a dict.  Designed
for plugin routes that need inline AI recommendations outside the chat panel.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
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
from az_scout.services.ai_chat._tools import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

# Reuse the same limits as the streaming path
_MAX_TOOL_ROUNDS = 10
_MAX_RETRIES = 3
_DEFAULT_RETRY_WAIT = 10

# In-memory TTL cache for completion results
_CACHE_TTL = 300  # 5 minutes
_CACHE_MAX_SIZE = 128
_cache: dict[str, tuple[float, CompletionResult]] = {}


def _cache_key(
    prompt: str,
    system_prompt: str | None,
    tenant_id: str | None,
    region: str | None,
    subscription_id: str | None,
    tools: bool,
) -> str:
    """Build a deterministic cache key from all input parameters."""
    raw = json.dumps(
        [prompt, system_prompt, tenant_id, region, subscription_id, tools],
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str, ttl: int = _CACHE_TTL) -> CompletionResult | None:
    """Return a cached result if it exists and hasn't expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, result = entry
    if time.monotonic() - ts > ttl:
        del _cache[key]
        return None
    return result


def _cache_put(key: str, result: CompletionResult) -> None:
    """Store a result in the cache, evicting oldest entries if over max size."""
    # Evict expired entries first
    now = time.monotonic()
    expired = [k for k, (ts, _) in _cache.items() if now - ts > _CACHE_TTL]
    for k in expired:
        del _cache[k]
    # Evict oldest if still over limit
    while len(_cache) >= _CACHE_MAX_SIZE:
        oldest_key = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest_key]
    _cache[key] = (now, result)


@dataclass
class CompletionResult:
    """Result of a non-streaming AI completion."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


async def ai_complete(
    prompt: str,
    *,
    system_prompt: str | None = None,
    tenant_id: str | None = None,
    region: str | None = None,
    subscription_id: str | None = None,
    tools: bool = True,
    cache_ttl: int = _CACHE_TTL,
) -> CompletionResult:
    """Run a single-shot AI completion with optional tool calling.

    Parameters
    ----------
    prompt:
        The user message to send.
    system_prompt:
        Custom system prompt.  When *None*, no system message is included
        (the caller is expected to provide domain-specific instructions).
    tenant_id, region, subscription_id:
        Azure context — auto-injected into tool call arguments.
    tools:
        Whether to enable tool calling.  Set *False* for pure text completion.
    cache_ttl:
        Cache time-to-live in seconds.  Defaults to 300 (5 min).
        Set to ``0`` to disable caching for this call.

    Returns
    -------
    CompletionResult:
        The final assistant text and a list of tool calls that were executed.
    """
    import httpx

    key = _cache_key(prompt, system_prompt, tenant_id, region, subscription_id, tools)
    if cache_ttl > 0:
        cached = _cache_get(key, ttl=cache_ttl)
        if cached is not None:
            logger.debug("ai_complete cache hit for key %s…", key[:12])
            return cached

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    url = (
        f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{AZURE_OPENAI_DEPLOYMENT}/chat/completions"
        f"?api-version={AZURE_OPENAI_API_VERSION}"
    )
    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_API_KEY,
    }

    tool_log: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        for _round in range(_MAX_TOOL_ROUNDS):
            body: dict[str, Any] = {"messages": messages}
            if tools and TOOL_DEFINITIONS:
                body["tools"] = TOOL_DEFINITIONS
                body["tool_choice"] = "auto"

            # Retry loop for 429 rate-limit errors
            resp_data: dict[str, Any] | None = None
            for _attempt in range(_MAX_RETRIES):
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 429:
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
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                elif resp.status_code != 200:
                    resp.raise_for_status()
                else:
                    resp_data = resp.json()
                    break

            if resp_data is None:
                return CompletionResult(content="Error: failed to get response from AI model.")

            choices = resp_data.get("choices", [])
            if not choices:
                return CompletionResult(content="")

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            # If no tool calls, return the final content
            if finish_reason != "tool_calls" or not message.get("tool_calls"):
                result = CompletionResult(
                    content=message.get("content", ""),
                    tool_calls=tool_log,
                )
                if result.content and cache_ttl > 0:
                    _cache_put(key, result)
                return result

            # Execute tool calls
            messages.append(message)

            for tc in message["tool_calls"]:
                tool_name = tc["function"]["name"]
                try:
                    raw_args = tc["function"].get("arguments", "")
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}

                # Auto-inject context parameters
                if tenant_id and "tenant_id" in _get_tool_params(tool_name):
                    args.setdefault("tenant_id", tenant_id)
                if region and "region" in _get_tool_params(tool_name):
                    args.setdefault("region", region)
                if subscription_id:
                    if "subscription_id" in _get_tool_params(tool_name):
                        args.setdefault("subscription_id", subscription_id)
                    if "subscription_ids" in _get_tool_params(tool_name):
                        args.setdefault("subscription_ids", [subscription_id])

                tool_result = _execute_tool(tool_name, args)
                tool_content = _truncate_tool_result(tool_result)

                tool_log.append(
                    {
                        "name": tool_name,
                        "arguments": args,
                        "result_length": len(tool_result),
                    }
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_content,
                    }
                )

    # Exhausted rounds
    result = CompletionResult(content="", tool_calls=tool_log)
    return result
