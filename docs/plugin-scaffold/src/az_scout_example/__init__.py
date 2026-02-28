"""az-scout example plugin.

This is a minimal plugin scaffold. Customise the class below
to add your own routes, MCP tools, UI tabs, and chat modes.
"""

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from az_scout.plugin_api import ChatMode, TabDefinition
from fastapi import APIRouter

_STATIC_DIR = Path(__file__).parent / "static"

try:
    __version__ = _pkg_version("az-scout-example")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"


class ExamplePlugin:
    """Example az-scout plugin."""

    name = "example"
    version = __version__

    def get_router(self) -> APIRouter | None:
        """Return API routes, or None to skip."""
        from az_scout_example.routes import router

        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        """Return MCP tool functions, or None to skip."""
        from az_scout_example.tools import example_tool

        return [example_tool]

    def get_static_dir(self) -> Path | None:
        """Return path to static assets directory, or None to skip."""
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        """Return UI tab definitions, or None to skip."""
        return [
            TabDefinition(
                id="example",
                label="Example",
                icon="bi bi-puzzle",
                js_entry="js/example-tab.js",
                css_entry="css/example.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        """Return chat mode definitions, or None to skip."""
        # Uncomment to add a custom chat mode:
        # return [
        #     ChatMode(
        #         id="example-advisor",
        #         label="Example",
        #         system_prompt="You are an example assistant.",
        #         welcome_message="Welcome to the example chat mode!",
        #     )
        # ]
        return None


# Module-level instance — referenced by the entry point
plugin = ExamplePlugin()
