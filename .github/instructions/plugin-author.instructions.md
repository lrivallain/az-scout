---
description: "az-scout plugin authoring conventions for sibling repos and internal_plugins. USE WHEN editing any az_scout_* package, internal_plugins/, or scaffold docs."
applyTo: "**/az_scout_*/**/*.py,**/internal_plugins/**/*.py,docs/plugin-scaffold/**"
---

# Plugin author conventions

Audience: **plugin authors** implementing the protocol.
For changes to the protocol itself, see `plugin-api.instructions.md`.

## Plugin protocol

Your plugin class must satisfy the `AzScoutPlugin` protocol from `az_scout.plugin_api`:

```python
class MyPlugin:
    name = "my-plugin"       # unique identifier (kebab-case)
    version = "0.1.0"

    def get_router(self) -> APIRouter | None: ...
    def get_mcp_tools(self) -> list[Callable] | None: ...
    def get_static_dir(self) -> Path | None: ...
    def get_tabs(self) -> list[TabDefinition] | None: ...
    def get_chat_modes(self) -> list[ChatMode] | None: ...
    def get_navbar_actions(self) -> list[NavbarAction] | None: ...

plugin = MyPlugin()  # module-level instance
```

## Key imports from az_scout

```python
from az_scout.plugin_api import (
    AzScoutPlugin, TabDefinition, ChatMode, NavbarAction,
    get_plugin_logger, PluginError, PluginValidationError, PluginUpstreamError,
    is_ai_enabled, plugin_ai_complete,
)
from az_scout.azure_api import arm_get, arm_post, arm_paginate, get_headers
```

## Conventions

- **Lazy imports** inside protocol methods (avoid circular imports at discovery)
- **Routes** mounted at `/plugins/{name}/` — use relative paths in the router
- **MCP tools** are plain functions with type annotations + descriptive docstrings
- **Static dir**: `Path(__file__).parent / "static"`
- **Type annotations** on all functions (`disallow_untyped_defs = true`)
- **Line length**: 100, ruff rules: `E, F, I, W, UP, B, SIM`
- **No global mutable state** — plugins must be fully self-contained

## AI completion (optional)

```python
if is_ai_enabled():
    result = await plugin_ai_complete(
        "Analyse this data...",
        system_prompt="You are a domain expert.",
        region="eastus",
        cache_ttl=600,  # seconds, 0 to bypass cache
    )
    content = result["content"]  # markdown text
    tools = result["tool_calls"]  # list of tool call metadata
```

## JS globals available to plugin scripts

`apiFetch`, `apiPost`, `aiComplete`, `aiEnabled`, `renderMarkdown`,
`tenantQS`, `escapeHtml`, `subscriptions`, `regions`

## Create a new plugin

If you need to create a new plugin, use the following command to scaffold a new plugin directory with template files:

```bash
az-scout create-plugin
```

Follow the prompts to enter the plugin name and description. This will create a new directory with a basic plugin structure and you can start implementing your plugin logic there.
