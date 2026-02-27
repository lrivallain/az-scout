"""Tests for the plugin discovery and registration system."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter

from az_scout.plugin_api import AzScoutPlugin, ChatMode, TabDefinition
from az_scout.plugins import (
    _loaded_plugins,
    _plugin_chat_modes,
    discover_plugins,
    get_plugin_chat_modes,
    get_plugin_metadata,
    register_plugins,
)

# ---------------------------------------------------------------------------
# Helpers – concrete plugin implementations for testing
# ---------------------------------------------------------------------------


class FullPlugin:
    """A test plugin that provides all extension layers."""

    name = "test-full"
    version = "1.0.0"

    def get_router(self) -> APIRouter | None:
        router = APIRouter()

        @router.get("/hello")
        def hello() -> dict[str, str]:
            return {"msg": "hello from plugin"}

        return router

    def get_mcp_tools(self) -> list[Any] | None:
        def my_tool() -> str:
            """A test MCP tool."""
            return "tool result"

        return [my_tool]

    def get_static_dir(self) -> Path | None:
        # Use the tests directory itself as a stand-in for static assets
        return Path(__file__).parent

    def get_tabs(self) -> list[TabDefinition] | None:
        return [
            TabDefinition(
                id="test-tab",
                label="Test Tab",
                icon="bi bi-gear",
                js_entry="js/test.js",
                css_entry="css/test.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        return [
            ChatMode(
                id="test-mode",
                label="Test Mode",
                system_prompt="You are a test assistant.",
                welcome_message="Welcome to test mode!",
            )
        ]


class MinimalPlugin:
    """A test plugin that provides nothing (all methods return None)."""

    name = "test-minimal"
    version = "0.1.0"

    def get_router(self) -> APIRouter | None:
        return None

    def get_mcp_tools(self) -> list[Any] | None:
        return None

    def get_static_dir(self) -> Path | None:
        return None

    def get_tabs(self) -> list[TabDefinition] | None:
        return None

    def get_chat_modes(self) -> list[ChatMode] | None:
        return None


class NotAPlugin:
    """An object that does NOT satisfy the AzScoutPlugin protocol."""

    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_plugin_registries():
    """Reset module-level plugin registries between tests."""
    _loaded_plugins.clear()
    _plugin_chat_modes.clear()
    yield
    _loaded_plugins.clear()
    _plugin_chat_modes.clear()


# ---------------------------------------------------------------------------
# Protocol checks
# ---------------------------------------------------------------------------


class TestProtocol:
    """Tests for the AzScoutPlugin protocol."""

    def test_full_plugin_satisfies_protocol(self):
        assert isinstance(FullPlugin(), AzScoutPlugin)

    def test_minimal_plugin_satisfies_protocol(self):
        assert isinstance(MinimalPlugin(), AzScoutPlugin)

    def test_non_plugin_does_not_satisfy_protocol(self):
        assert not isinstance(NotAPlugin(), AzScoutPlugin)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscoverPlugins:
    """Tests for discover_plugins()."""

    def test_discovers_valid_plugin(self):
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            plugins = discover_plugins()

        assert len(plugins) == 1
        assert plugins[0].name == "test-full"

    def test_skips_non_protocol_object(self):
        mock_ep = MagicMock()
        mock_ep.name = "bad-plugin"
        mock_ep.load.return_value = NotAPlugin()

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            plugins = discover_plugins()

        assert len(plugins) == 0

    def test_handles_load_exception(self):
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("missing dep")

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            plugins = discover_plugins()

        assert len(plugins) == 0

    def test_no_entry_points(self):
        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[]):
            plugins = discover_plugins()

        assert len(plugins) == 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterPlugins:
    """Tests for register_plugins()."""

    def test_register_full_plugin(self):
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            result = register_plugins(app, mock_mcp)

        assert len(result) == 1
        assert result[0].name == "test-full"

        # Verify MCP tool was registered
        mock_mcp.tool.assert_called_once()

        # Verify chat modes were registered
        modes = get_plugin_chat_modes()
        assert "test-mode" in modes
        assert modes["test-mode"].label == "Test Mode"

    def test_register_minimal_plugin_no_errors(self):
        minimal = MinimalPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-minimal"
        mock_ep.load.return_value = minimal

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            result = register_plugins(app, mock_mcp)

        assert len(result) == 1
        mock_mcp.tool.assert_not_called()


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestPluginMetadata:
    """Tests for get_plugin_metadata()."""

    def test_metadata_from_full_plugin(self):
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        meta = get_plugin_metadata()
        assert len(meta) == 1
        assert meta[0]["name"] == "test-full"
        assert meta[0]["version"] == "1.0.0"
        assert len(meta[0]["tabs"]) == 1
        assert meta[0]["tabs"][0]["id"] == "test-tab"
        assert meta[0]["tabs"][0]["css_entry"] == "css/test.css"
        assert len(meta[0]["chat_modes"]) == 1
        assert meta[0]["chat_modes"][0]["id"] == "test-mode"

    def test_metadata_empty_when_no_plugins(self):
        meta = get_plugin_metadata()
        assert meta == []


# ---------------------------------------------------------------------------
# Chat mode integration in ai_chat
# ---------------------------------------------------------------------------


class TestPluginChatModeIntegration:
    """Test that plugin chat modes are resolved in _build_system_prompt."""

    def test_plugin_mode_uses_plugin_system_prompt(self):
        # Register a plugin chat mode
        _plugin_chat_modes["custom-mode"] = ChatMode(
            id="custom-mode",
            label="Custom",
            system_prompt="You are a custom plugin assistant.",
            welcome_message="Hello from plugin!",
        )

        from az_scout.services.ai_chat import _build_system_prompt

        prompt = _build_system_prompt(mode="custom-mode")
        assert "You are a custom plugin assistant." in prompt

    def test_unknown_mode_falls_back_to_discussion(self):
        from az_scout.services.ai_chat import SYSTEM_PROMPT, _build_system_prompt

        prompt = _build_system_prompt(mode="nonexistent-mode")
        assert prompt.startswith(SYSTEM_PROMPT[:50])

    def test_planner_mode_still_works(self):
        from az_scout.services.ai_chat import PLANNER_SYSTEM_PROMPT, _build_system_prompt

        prompt = _build_system_prompt(mode="planner")
        assert prompt.startswith(PLANNER_SYSTEM_PROMPT[:50])

    def test_discussion_mode_still_works(self):
        from az_scout.services.ai_chat import SYSTEM_PROMPT, _build_system_prompt

        prompt = _build_system_prompt(mode="discussion")
        assert prompt.startswith(SYSTEM_PROMPT[:50])


# ---------------------------------------------------------------------------
# Index route includes plugin metadata
# ---------------------------------------------------------------------------


class TestIndexWithPlugins:
    """Test that the index route passes plugin context to the template."""

    def test_index_with_plugin(self, client):
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            from az_scout.app import app
            from az_scout.mcp_server import mcp as _mcp_server

            register_plugins(app, _mcp_server)

        resp = client.get("/")
        assert resp.status_code == 200
        # Plugin tab should appear in the rendered HTML
        assert b"Test Tab" in resp.content
        assert b"test-tab" in resp.content

    def test_index_without_plugins(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Azure Scout" in resp.content
