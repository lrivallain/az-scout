"""Tests for the AI chat service – planner mode, mode switching, MCP tool conversion."""

import json
from unittest.mock import patch

from az_scout.services.ai_chat import (
    PLANNER_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    TOOL_DEFINITIONS,
    _build_openai_tools,
    _build_system_prompt,
    _mcp_schema_to_openai,
    _post_process_tool_result,
    _truncate_tool_result,
)

# ---------------------------------------------------------------------------
# is_chat_enabled
# ---------------------------------------------------------------------------


class TestIsChatEnabled:
    """Tests for the is_chat_enabled function."""

    def test_disabled_when_env_vars_missing(self):
        with patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_DEPLOYMENT": "",
            },
        ):
            # Re-import to pick up patched env
            from importlib import reload

            from az_scout.services import ai_chat

            reload(ai_chat)
            assert ai_chat.is_chat_enabled() is False


# ---------------------------------------------------------------------------
# _build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    """Tests for system prompt construction with mode parameter."""

    def test_discussion_mode_uses_discussion_prompt(self):
        prompt = _build_system_prompt(mode="discussion")
        assert "Azure Scout Assistant" in prompt
        assert "Deployment Planner" not in prompt

    def test_planner_mode_uses_planner_prompt(self):
        prompt = _build_system_prompt(mode="planner")
        assert "Azure Scout Planner" in prompt
        assert "directive" in prompt.lower()

    def test_default_mode_is_discussion(self):
        prompt = _build_system_prompt()
        assert "Azure Scout Assistant" in prompt

    def test_tenant_context_appended(self):
        prompt = _build_system_prompt(tenant_id="tid-123", mode="discussion")
        assert "tid-123" in prompt

    def test_region_context_appended(self):
        prompt = _build_system_prompt(region="eastus", mode="planner")
        assert "eastus" in prompt

    def test_planner_prompt_contains_planning_paths(self):
        """The planner prompt should describe the three independent planning paths."""
        assert "Path A" in PLANNER_SYSTEM_PROMPT
        assert "Path B" in PLANNER_SYSTEM_PROMPT
        assert "Path C" in PLANNER_SYSTEM_PROMPT
        assert "region" in PLANNER_SYSTEM_PROMPT
        assert "SKU" in PLANNER_SYSTEM_PROMPT
        assert "zone" in PLANNER_SYSTEM_PROMPT
        assert "virtual machine" in PLANNER_SYSTEM_PROMPT
        assert "Spot" in PLANNER_SYSTEM_PROMPT

    def test_assistant_prompt_contains_guidelines(self):
        assert "Interactive choices" in SYSTEM_PROMPT
        assert "Subscription resolution" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# POST /api/chat  – mode parameter
# ---------------------------------------------------------------------------


class TestChatEndpointMode:
    """Tests for the /api/chat endpoint with mode parameter."""

    def test_chat_returns_503_when_not_configured(self, client):
        """Chat should return 503 when Azure OpenAI is not configured."""
        resp = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "mode": "discussion",
            },
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json()["error"]

    def test_chat_planner_mode_returns_503_when_not_configured(self, client):
        """Planner mode should also return 503 when not configured."""
        resp = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "help me plan"}],
                "mode": "planner",
            },
        )
        assert resp.status_code == 503

    def test_chat_accepts_mode_field(self, client):
        """The endpoint should accept the mode field without erroring (422)."""
        resp = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "test"}],
                "mode": "planner",
                "tenant_id": "tid-1",
                "region": "eastus",
            },
        )
        # 503 because OpenAI isn't configured, but NOT 422 (validation error)
        assert resp.status_code == 503

    def test_chat_default_mode_is_discussion(self, client):
        """When mode is omitted, it should default to discussion."""
        resp = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "test"}]},
        )
        assert resp.status_code == 503  # not 422


# ---------------------------------------------------------------------------
# _truncate_tool_result
# ---------------------------------------------------------------------------


class TestTruncateToolResult:
    """Tests for tool result truncation."""

    def test_short_result_unchanged(self):
        result = '{"key": "value"}'
        assert _truncate_tool_result(result) == result

    def test_large_array_truncated(self):
        import json

        items = [
            {
                "name": f"Standard_D{i}s_v5",
                "vCPUs": i,
                "zones": ["1", "2", "3"],
                "capabilities": {"MemoryGB": str(i * 4), "PremiumIO": "True"},
            }
            for i in range(500)
        ]
        result = json.dumps(items, indent=2)
        truncated = _truncate_tool_result(result)
        assert len(truncated) < len(result)
        assert "omitted" in truncated
        assert "total: 500" in truncated

    def test_large_string_truncated(self):
        result = "x" * 50_000
        truncated = _truncate_tool_result(result)
        assert len(truncated) <= 30_100  # budget + "(truncated)"
        assert "truncated" in truncated

    def test_small_array_unchanged(self):
        items = [{"name": "Standard_D2s_v5"}]
        result = json.dumps(items, indent=2)
        assert _truncate_tool_result(result) == result


# ---------------------------------------------------------------------------
# MCP → OpenAI tool conversion
# ---------------------------------------------------------------------------


class TestMcpToOpenaiConversion:
    """Tests for the MCP tool → OpenAI function-calling format converter."""

    def test_all_mcp_tools_present(self):
        """All MCP tools should appear in the generated TOOL_DEFINITIONS."""
        from az_scout.mcp_server import mcp as mcp_server

        mcp_names = {t.name for t in mcp_server._tool_manager.list_tools()}
        generated_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        # All MCP tools must be present (chat-only tools are extra)
        assert mcp_names.issubset(generated_names)

    def test_chat_only_tools_present(self):
        """switch_region and switch_tenant should be in TOOL_DEFINITIONS."""
        names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        assert "switch_region" in names
        assert "switch_tenant" in names

    def test_total_tool_count(self):
        """Should have 10 MCP tools + 2 chat-only = 12 total."""
        assert len(TOOL_DEFINITIONS) == 12

    def test_schema_strips_titles(self):
        """The converter should strip Pydantic 'title' fields from parameters."""
        schema = _mcp_schema_to_openai(
            {
                "properties": {
                    "region": {"title": "Region", "type": "string"},
                },
                "required": ["region"],
            }
        )
        assert "title" not in schema["properties"]["region"]
        assert schema["properties"]["region"]["type"] == "string"

    def test_schema_converts_nullable_anyof(self):
        """anyOf [{type: str}, {type: null}] should become {type: str}."""
        schema = _mcp_schema_to_openai(
            {
                "properties": {
                    "tenant_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Tenant Id",
                    },
                },
                "required": [],
            }
        )
        prop = schema["properties"]["tenant_id"]
        assert prop["type"] == "string"
        assert "anyOf" not in prop
        assert "default" not in prop  # None defaults are stripped

    def test_schema_preserves_defaults(self):
        """Non-null defaults should be preserved."""
        schema = _mcp_schema_to_openai(
            {
                "properties": {
                    "resource_type": {
                        "default": "virtualMachines",
                        "title": "Resource Type",
                        "type": "string",
                    },
                    "include_prices": {
                        "default": False,
                        "title": "Include Prices",
                        "type": "boolean",
                    },
                },
                "required": [],
            }
        )
        assert schema["properties"]["resource_type"]["default"] == "virtualMachines"
        assert schema["properties"]["include_prices"]["default"] is False

    def test_schema_preserves_array_items(self):
        """Array parameters should keep their items schema."""
        schema = _mcp_schema_to_openai(
            {
                "properties": {
                    "vm_sizes": {
                        "items": {"type": "string"},
                        "title": "Vm Sizes",
                        "type": "array",
                    },
                },
                "required": ["vm_sizes"],
            }
        )
        prop = schema["properties"]["vm_sizes"]
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}

    def test_mcp_descriptions_used_directly(self):
        """Tool descriptions should come from MCP docstrings (no overrides)."""
        tools = _build_openai_tools()
        sku_tool = next(t for t in tools if t["function"]["name"] == "get_sku_availability")
        desc = sku_tool["function"]["description"]
        # The MCP docstring mentions include_prices and confidence scores
        assert "include_prices" in desc
        assert "confidence" in desc.lower()

    def test_mcp_param_descriptions_present(self):
        """MCP parameter Field descriptions should flow into OpenAI schemas."""
        tools = _build_openai_tools()
        sku_tool = next(t for t in tools if t["function"]["name"] == "get_sku_availability")
        name_desc = sku_tool["function"]["parameters"]["properties"]["name"]["description"]
        # Should have the fuzzy matching guidance from Field(description=...)
        assert "FX48" in name_desc or "fuzzy" in name_desc.lower()
        # include_prices should have a description too
        prices_desc = sku_tool["function"]["parameters"]["properties"]["include_prices"]
        assert "description" in prices_desc

    def test_every_tool_has_valid_structure(self):
        """All tools should have the correct structure for OpenAI function calling."""
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            params = fn["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params


# ---------------------------------------------------------------------------
# _post_process_tool_result
# ---------------------------------------------------------------------------


class TestPostProcessToolResult:
    """Tests for chat-specific post-processing of MCP tool results."""

    def test_sku_availability_sorted_by_price(self):
        """get_sku_availability with include_prices should sort by PAYGO ascending."""
        skus = [
            {"name": "expensive", "pricing": {"paygo": 10.0}},
            {"name": "cheap", "pricing": {"paygo": 1.0}},
            {"name": "no_price", "pricing": {"paygo": None}},
        ]
        result = _post_process_tool_result(
            "get_sku_availability", {"include_prices": True}, json.dumps(skus)
        )
        parsed = json.loads(result)
        assert parsed[0]["name"] == "cheap"
        assert parsed[1]["name"] == "expensive"
        assert parsed[2]["name"] == "no_price"

    def test_sku_availability_no_sort_without_prices(self):
        """get_sku_availability without include_prices should not re-sort."""
        skus = [{"name": "B"}, {"name": "A"}]
        result = _post_process_tool_result("get_sku_availability", {}, json.dumps(skus))
        parsed = json.loads(result)
        assert parsed[0]["name"] == "B"  # preserved original order

    def test_pricing_detail_hint_on_null_prices(self):
        """get_sku_pricing_detail should add a hint when all prices are null."""
        data = {
            "skuName": "M128",
            "paygo": None,
            "spot": None,
            "ri_1y": None,
            "ri_3y": None,
            "sp_1y": None,
            "sp_3y": None,
        }
        result = _post_process_tool_result(
            "get_sku_pricing_detail",
            {"sku_name": "M128"},
            json.dumps(data),
        )
        parsed = json.loads(result)
        assert "hint" in parsed
        assert "get_sku_availability" in parsed["hint"]

    def test_pricing_detail_no_hint_with_prices(self):
        """get_sku_pricing_detail should NOT add a hint when prices exist."""
        data = {
            "skuName": "Standard_D2s_v5",
            "paygo": 0.1,
            "spot": 0.05,
            "ri_1y": None,
            "ri_3y": None,
            "sp_1y": None,
            "sp_3y": None,
        }
        result = _post_process_tool_result(
            "get_sku_pricing_detail",
            {"sku_name": "Standard_D2s_v5"},
            json.dumps(data),
        )
        parsed = json.loads(result)
        assert "hint" not in parsed

    def test_passthrough_for_other_tools(self):
        """Other tools should return the result unchanged."""
        original = '{"tenants": [{"id": "abc"}]}'
        result = _post_process_tool_result("list_tenants", {}, original)
        assert result == original
