"""Tests for the plugin discovery and registration system."""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter

from az_scout.plugin_api import AzScoutPlugin, ChatMode, TabDefinition
from az_scout.plugins import (
    _loaded_plugins,
    _plugin_chat_modes,
    _plugin_mcp_tool_names,
    _plugin_route_prefixes,
    _unregister_all,
    discover_plugins,
    get_plugin_chat_modes,
    get_plugin_metadata,
    register_plugins,
    reload_plugins,
)


@pytest.fixture(autouse=True)
def _isolate_plugin_packages():
    """Prevent tests from picking up real packages or internal plugins."""
    with (
        patch("az_scout.plugins._ensure_plugin_packages_on_path"),
        patch("az_scout.plugins._discover_plugin_packages_entry_points", return_value=[]),
        patch("az_scout.plugins.discover_internal_plugins", return_value=[]),
    ):
        yield


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
    _plugin_mcp_tool_names.clear()
    _plugin_route_prefixes.clear()
    yield
    _loaded_plugins.clear()
    _plugin_chat_modes.clear()
    _plugin_mcp_tool_names.clear()
    _plugin_route_prefixes.clear()


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

        # Internal plugins (topology) + the mock entry point
        external = [p for p in plugins if p.name == "test-full"]
        assert len(external) == 1
        assert external[0].name == "test-full"

    def test_skips_non_protocol_object(self):
        mock_ep = MagicMock()
        mock_ep.name = "bad-plugin"
        mock_ep.load.return_value = NotAPlugin()

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            plugins = discover_plugins()

        # Only internal plugins remain
        assert all(p.name != "bad-plugin" for p in plugins)

    def test_handles_load_exception(self):
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("missing dep")

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            plugins = discover_plugins()

        assert all(p.name != "broken" for p in plugins)

    def test_no_entry_points(self):
        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[]):
            plugins = discover_plugins()

        # With internal plugins mocked out, no plugins are returned
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
        mock_ep.dist.name = "test-full-dist"

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
        assert "homepage" in meta[0]

    def test_metadata_includes_homepage_from_dist(self):
        """Homepage is extracted from pip distribution Project-URL metadata."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full
        mock_ep.dist.name = "test-full-dist"

        mock_mcp = MagicMock()
        mock_dist = MagicMock()
        mock_dist.metadata.get_all.return_value = [
            "Homepage, https://example.com/my-plugin",
            "Issues, https://example.com/my-plugin/issues",
        ]

        from az_scout.app import app

        with (
            patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]),
            patch("az_scout.plugins.importlib.metadata.distribution", return_value=mock_dist),
        ):
            register_plugins(app, mock_mcp)
            meta = get_plugin_metadata()

        assert meta[0]["homepage"] == "https://example.com/my-plugin"

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
        from az_scout.internal_plugins.planner.chat_mode import PLANNER_CHAT_MODE
        from az_scout.services.ai_chat import _build_system_prompt

        with patch(
            "az_scout.plugins.get_plugin_chat_modes",
            return_value={"planner": PLANNER_CHAT_MODE},
        ):
            prompt = _build_system_prompt(mode="planner")
        assert prompt.startswith(PLANNER_CHAT_MODE.system_prompt[:50])

    def test_discussion_mode_still_works(self):
        from az_scout.services.ai_chat import SYSTEM_PROMPT, _build_system_prompt

        prompt = _build_system_prompt(mode="discussion")
        assert prompt.startswith(SYSTEM_PROMPT[:50])


# ---------------------------------------------------------------------------
# Hot-reload: _unregister_all
# ---------------------------------------------------------------------------


class TestUnregisterAll:
    """Tests for _unregister_all() — cleans up routes, MCP tools, and chat modes."""

    def test_removes_plugin_routes(self):
        """After unregister, plugin route prefixes are removed from app.routes."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        # Plugin route should be present
        paths = [getattr(r, "path", "") for r in app.routes]
        assert any(p.startswith("/plugins/test-full") for p in paths)

        _unregister_all(app, mock_mcp)

        # Plugin routes should be gone
        paths_after = [getattr(r, "path", "") for r in app.routes]
        assert not any(p.startswith("/plugins/test-full") for p in paths_after)

    def test_removes_mcp_tools(self):
        """MCP server.remove_tool() is called for each registered tool."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        _unregister_all(app, mock_mcp)

        mock_mcp.remove_tool.assert_called_once_with("my_tool")

    def test_clears_chat_modes(self):
        """Plugin chat modes are cleared after unregister."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        assert "test-mode" in _plugin_chat_modes

        _unregister_all(app, mock_mcp)

        assert len(_plugin_chat_modes) == 0
        assert len(_loaded_plugins) == 0

    def test_clears_tracking_registries(self):
        """All internal tracking registries are cleared after unregister."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        assert len(_plugin_mcp_tool_names) == 1
        assert len(_plugin_route_prefixes) >= 1

        _unregister_all(app, mock_mcp)

        assert len(_plugin_mcp_tool_names) == 0
        assert len(_plugin_route_prefixes) == 0

    def test_tolerates_missing_mcp_tool(self):
        """If remove_tool raises, _unregister_all does not crash."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()
        mock_mcp.remove_tool.side_effect = Exception("not found")

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        # Should not raise
        _unregister_all(app, mock_mcp)

        assert len(_loaded_plugins) == 0


# ---------------------------------------------------------------------------
# Hot-reload: _flush_plugin_modules
# ---------------------------------------------------------------------------


class TestFlushPluginModules:
    """Tests for _flush_plugin_modules() — removes stale modules from sys.modules."""

    def test_removes_modules_under_packages_dir(self):
        """Modules with __file__ under _PACKAGES_DIR are flushed."""
        from az_scout.plugin_manager import _PACKAGES_DIR
        from az_scout.plugins import _flush_plugin_modules

        fake_mod = MagicMock()
        fake_mod.__file__ = str(_PACKAGES_DIR / "az_scout_myplugin" / "__init__.py")
        sys.modules["az_scout_myplugin"] = fake_mod

        _PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _flush_plugin_modules()
        finally:
            sys.modules.pop("az_scout_myplugin", None)

        assert "az_scout_myplugin" not in sys.modules

    def test_keeps_non_plugin_modules(self):
        """Modules outside _PACKAGES_DIR are not removed."""
        from az_scout.plugin_manager import _PACKAGES_DIR
        from az_scout.plugins import _flush_plugin_modules

        original_count = len(sys.modules)

        _PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        _flush_plugin_modules()

        # Core modules should still be present
        assert "sys" in sys.modules
        assert "os" in sys.modules
        assert len(sys.modules) == original_count

    def test_noop_when_packages_dir_missing(self):
        """No crash when _PACKAGES_DIR doesn't exist."""
        from az_scout.plugin_manager import _PACKAGES_DIR
        from az_scout.plugins import _flush_plugin_modules

        # Ensure dir does not exist
        if _PACKAGES_DIR.exists():
            import shutil

            shutil.rmtree(_PACKAGES_DIR)

        _flush_plugin_modules()  # should not raise


# ---------------------------------------------------------------------------
# Hot-reload: reload_plugins
# ---------------------------------------------------------------------------


class TestReloadPlugins:
    """Tests for reload_plugins() — the main hot-reload entry point."""

    def test_reload_replaces_plugins(self):
        """After reload, the loaded plugins reflect the current entry points."""
        full = FullPlugin()
        mock_ep = MagicMock()
        mock_ep.name = "test-full"
        mock_ep.load.return_value = full

        mock_mcp = MagicMock()

        from az_scout.app import app

        with patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[mock_ep]):
            register_plugins(app, mock_mcp)

        assert any(p.name == "test-full" for p in _loaded_plugins)

        # Reload with no plugins – patch discover_plugins directly to avoid
        # importlib cache invalidation issues from _flush_plugin_modules().
        with patch("az_scout.plugins.discover_plugins", return_value=[]):
            reload_plugins(app, mock_mcp)

        assert not any(p.name == "test-full" for p in _loaded_plugins)
        assert "test-mode" not in _plugin_chat_modes

    def test_reload_picks_up_new_plugin(self):
        """A newly installed plugin is discovered after reload."""
        mock_mcp = MagicMock()

        from az_scout.app import app

        # Start with no plugins
        with patch("az_scout.plugins.discover_plugins", return_value=[]):
            register_plugins(app, mock_mcp)

        assert not any(p.name == "test-full" for p in _loaded_plugins)

        # Install a plugin, then reload
        full = FullPlugin()

        with patch("az_scout.plugins.discover_plugins", return_value=[full]):
            result = reload_plugins(app, mock_mcp)

        assert any(p.name == "test-full" for p in result)
        assert "test-mode" in _plugin_chat_modes

    def test_reload_calls_unregister_and_flush(self):
        """reload_plugins delegates to _unregister_all and _flush_plugin_modules."""
        mock_mcp = MagicMock()

        from az_scout.app import app

        with (
            patch("az_scout.plugins._unregister_all") as mock_unreg,
            patch("az_scout.plugins._flush_plugin_modules") as mock_flush,
            patch("az_scout.plugins.importlib.metadata.entry_points", return_value=[]),
        ):
            reload_plugins(app, mock_mcp)

        mock_unreg.assert_called_once_with(app, mock_mcp)
        mock_flush.assert_called_once()


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
        mock_ep.dist.name = "test-full-dist"

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
