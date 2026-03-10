# Plugin Scaffold Reference

A ready-to-use starter template is available at [`docs/plugin-scaffold/`](https://github.com/az-scout/az-scout/tree/main/docs/plugin-scaffold) in the repository. Copy it and customise.

---

## Structure

```
az-scout-myplugin/
├── pyproject.toml
├── README.md
├── LICENSE.txt
└── src/
    └── az_scout_myplugin/
        ├── __init__.py          # Plugin class + module-level instance
        ├── _log.py              # Logger setup
        ├── routes.py            # FastAPI router
        ├── tools.py             # MCP tool functions
        └── static/
            ├── css/
            │   └── example.css
            ├── html/
            │   └── example-tab.html
            └── js/
                └── example-tab.js
```

---

## Plugin entry point (`__init__.py`)

```python
"""az-scout example plugin."""

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any

from az_scout.plugin_api import ChatMode, TabDefinition, get_plugin_logger
from fastapi import APIRouter

logger = get_plugin_logger("example")
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
        from az_scout_example.routes import router
        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        from az_scout_example.tools import example_tool
        return [example_tool]

    def get_static_dir(self) -> Path | None:
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
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
        return None

    def get_system_prompt_addendum(self) -> str | None:
        return None


plugin = ExamplePlugin()
```

---

## `pyproject.toml`

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "az-scout-myplugin"
dynamic = ["version"]
description = "My az-scout plugin"
requires-python = ">=3.11"
dependencies = ["az-scout", "fastapi"]

[project.entry-points."az_scout.plugins"]
my_plugin = "az_scout_myplugin:plugin"

[tool.hatch.build.targets.wheel]
packages = ["src/az_scout_myplugin"]

[tool.hatch.version]
source = "vcs"
raw-options.fallback_version = "0.1.0"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
```

---

## Naming conventions

| Item | Convention | Example |
|------|-----------|---------|
| Package name | `az-scout-{name}` | `az-scout-cost-view` |
| Module name | `az_scout_{name}` | `az_scout_cost_view` |
| Entry point key | `{name}` | `cost_view` |
| Plugin `name` attribute | `{name}` | `"cost-view"` |

---

## Get the scaffold

```bash
# From any environment with az-scout installed
az-scout create-plugin

# Move into your generated plugin folder
cd /path/to/generated/az-scout-myplugin

# Install in dev mode
uv pip install -e .
az-scout
```

Optional repo-dev fallback:

```bash
python3 tools/plugin-scaffold/create_plugin.py
```

The command prompts for the plugin display name, slug, package/module names,
destination directory, and GitHub repository metadata, then generates a
ready-to-edit plugin project.

### Manual fallback

```bash
# If you prefer to do the renaming yourself
cp -r docs/plugin-scaffold az-scout-myplugin
cd az-scout-myplugin

# Rename files and entry points to match your plugin name
# Then install in dev mode
uv pip install -e .
az-scout  # your plugin is active
```
