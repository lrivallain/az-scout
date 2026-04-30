---
description: "az-scout plugin protocol, discovery, manager. USE WHEN editing the core plugin contract — plugin_api.py, plugins.py, plugin_manager/, or related docs."
applyTo: "src/az_scout/plugin_api.py,src/az_scout/plugins.py,src/az_scout/plugin_manager/**,docs/plugin-scaffold/**"
---

# Plugin API (core side)

Audience: **core maintainers** changing the plugin contract.
For plugin authors implementing a plugin, see `plugin-author.instructions.md`.

## Compatibility

- `PLUGIN_API_VERSION` lives in `plugin_api.py`. **Bump it** for any breaking change to the protocol, dataclasses, or shared helpers.
- The plugin manager guard refuses to load plugins declaring an incompatible major version — keep the guard in sync.
- Document every bump in `CHANGELOG.md` under `### Changed` with a migration note.


## Plugin protocol

```python
from az_scout.plugin_api import AzScoutPlugin, TabDefinition, ChatMode, NavbarAction

class MyPlugin:
    name = "my-plugin"       # unique identifier
    version = "0.1.0"

    def get_router(self) -> APIRouter | None: ...
    def get_mcp_tools(self) -> list[Callable] | None: ...
    def get_static_dir(self) -> Path | None: ...
    def get_tabs(self) -> list[TabDefinition] | None: ...
    def get_chat_modes(self) -> list[ChatMode] | None: ...
    def get_navbar_actions(self) -> list[NavbarAction] | None: ...
```

All methods optional — return `None` to skip.

## Conventions

- **Package layout:** src-layout (`src/az_scout_myplugin/`) with hatchling
- **Naming:** Package `az-scout-plugin-{name}`, module `az_scout_{name}`
- **Entry point:** `[project.entry-points."az_scout.plugins"]`
- **Lazy imports:** Inside methods to avoid circular imports at discovery time
- **Static dir:** `Path(__file__).parent / "static"` at module level

## AI completion helpers

```python
from az_scout.plugin_api import is_ai_enabled, plugin_ai_complete

if is_ai_enabled():
    result = await plugin_ai_complete(
        "Analyse this data...",
        system_prompt="You are an expert.",
        region="eastus",
        cache_ttl=600,  # 10 min cache, 0 to bypass
    )
    # result = {"content": "...", "tool_calls": [...]}
```

JS: `if (aiEnabled) { const r = await aiComplete("...", {cacheTtl: 600}); }`

## Isolation rules

- Fully self-contained — no global state mutation
- No circular imports — use lazy imports
- No heavy imports at module import time
- Respect core authentication and context model
- Never override built-in routes

## Testing

- Use `pytest` + `httpx` with `TestClient`
- Mock `discover_plugins()` to inject test plugin instances
- Mock Azure API calls — never require live Azure in tests
