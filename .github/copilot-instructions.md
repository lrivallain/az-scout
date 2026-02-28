# Copilot Instructions for az-scout

## Project overview

`az-scout` is a Python web tool that visualizes Azure Availability Zone logical-to-physical mappings across subscriptions. It uses a FastAPI backend and an MCP server, both calling shared Azure ARM REST API helpers, and a frontend with D3.js for graph rendering and vanilla JavaScript.

## Tech stack

- **Backend:** Python 3.11+, FastAPI 0.115+, uvicorn (ASGI server), click (CLI), azure-identity (DefaultAzureCredential), requests
- **MCP:** mcp[cli] (FastMCP), stdio and Streamable HTTP transports
- **Frontend:** Vanilla JavaScript (no framework), D3.js v7, CSS custom properties (dark/light themes)
- **Packaging:** hatchling + hatch-vcs, CalVer (`YYYY.MM.MICRO`), src-layout
- **Tools:** uv (package manager), ruff (lint + format), mypy (strict), pytest, pre-commit

## Project structure

```
src/az_scout/
├── azure_api.py      # Shared Azure ARM logic (auth, pagination, data functions)
├── app.py            # FastAPI routes, CLI entry point (thin wrappers over azure_api)
├── mcp_server.py     # MCP server exposing tools (thin wrappers over azure_api)
├── templates/
│   └── index.html    # Single-page Jinja2 template
└── static/
    ├── js/app.js     # All frontend logic (D3 graph, table, filters, theme)
    ├── css/style.css  # Styles with CSS variables for light/dark mode
    └── img/           # SVG icons (favicon, filter icons)
tests/
├── test_routes.py    # pytest tests for FastAPI routes (mocked Azure API)
└── test_mcp_server.py # pytest tests for MCP tools
```

## Code conventions

- **Python:** All functions must have type annotations (`disallow_untyped_defs = true`). Use `from __future__ import annotations` is not required (3.11+). Follow ruff rules: `E, F, I, W, UP, B, SIM`. Line length is 100.
- **JavaScript:** Vanilla JS only — no npm, no bundler, no frameworks. Use `const`/`let` (never `var`). Functions and variables use `camelCase`.
- **CSS:** Use CSS custom properties (defined in `:root`) for theming. Both light and dark themes must be maintained. Dark mode uses `[data-theme="dark"]` and `@media (prefers-color-scheme: dark)` selectors.
- **HTML:** Minimal Jinja2 templating. Static assets referenced via `url_for('static', ...)`.

## Azure API patterns

- Auth uses `DefaultAzureCredential` with optional `tenant_id` parameter.
- All ARM calls go through `requests.get()` with `Authorization: Bearer <token>` header.
- API base URL: `https://management.azure.com`.
- Handle pagination (`nextLink`) for list endpoints.
- Per-subscription errors should be included in the response (not fail the whole request).

## MCP tools reference

The MCP server (`mcp_server.py`) exposes these tools. When calling them, use the **exact parameter names** listed below.

| Tool | Parameters | Description |
|---|---|---|
| `list_tenants` | *(none)* | List Azure AD tenants with auth status |
| `list_subscriptions` | `tenant_id?` | List enabled subscriptions |
| `list_regions` | `subscription_id?`, `tenant_id?` | List AZ-enabled regions |
| `get_zone_mappings` | `region`, `subscription_ids`, `tenant_id?` | Logical-to-physical zone mappings |
| `get_sku_availability` | `region`, `subscription_id`, `tenant_id?`, `resource_type?`, `name?`, `family?`, `min_vcpus?`, `max_vcpus?`, `min_memory_gb?`, `max_memory_gb?` | SKU availability per zone |

### `get_sku_availability` filter parameters

Use these optional filters to reduce output size (important in conversational contexts):

- **`name`** *(str)* – case-insensitive substring match on SKU name (e.g. `"D2s"` matches `Standard_D2s_v3`)
- **`family`** *(str)* – case-insensitive substring match on SKU family (e.g. `"DSv3"` matches `standardDSv3Family`)
- **`min_vcpus`** / **`max_vcpus`** *(int)* – vCPU count range (inclusive)
- **`min_memory_gb`** / **`max_memory_gb`** *(float)* – memory in GB range (inclusive)

When no filters are provided, all SKUs for the resource type are returned.

## Testing patterns

- Tests use FastAPI's `TestClient` (backed by httpx).
- Azure API calls are mocked with `unittest.mock.patch` on `requests.get` and `DefaultAzureCredential`.
- Tests are grouped by endpoint in pytest test classes.
- Run with: `uv run pytest`

## Quality checks

Before committing, ensure all checks pass:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

Pre-commit hooks run these automatically on `git commit`.

## Plugin development

az-scout supports plugins — pip-installable Python packages that extend the app with API routes, MCP tools, UI tabs, static assets, and AI chat modes.

### Plugin architecture

- Plugins are discovered at startup via `importlib.metadata.entry_points(group="az_scout.plugins")`.
- Each plugin must expose a module-level object satisfying the `AzScoutPlugin` protocol from `az_scout.plugin_api`.
- Registration is automatic — no manual configuration needed.
- A ready-to-use scaffold is at `docs/plugin-scaffold/`.

### Plugin protocol

```python
from az_scout.plugin_api import AzScoutPlugin, TabDefinition, ChatMode

class MyPlugin:
    name = "my-plugin"       # unique identifier
    version = "0.1.0"

    def get_router(self) -> APIRouter | None: ...       # FastAPI routes → /plugins/{name}/
    def get_mcp_tools(self) -> list[Callable] | None: ... # MCP tool functions
    def get_static_dir(self) -> Path | None: ...        # served at /plugins/{name}/static/
    def get_tabs(self) -> list[TabDefinition] | None: ... # UI tabs in main app
    def get_chat_modes(self) -> list[ChatMode] | None: ... # AI chat modes

plugin = MyPlugin()  # module-level instance referenced by entry point
```

All methods are optional — return `None` to skip a layer.

### Entry point registration

```toml
[project.entry-points."az_scout.plugins"]
my_plugin = "az_scout_myplugin:plugin"
```

### Plugin conventions

- **Package layout:** Use src-layout (`src/az_scout_myplugin/`) with hatchling build backend.
- **Naming:** Package name `az-scout-{name}`, module `az_scout_{name}`.
- **Dependencies:** Declare `az-scout` and `fastapi` as dependencies in `pyproject.toml`.
- **Type annotations:** Follow the same mypy strict rules as the main project (`disallow_untyped_defs = true`).
- **Linting:** Use `ruff` with the same rules: `E, F, I, W, UP, B, SIM`, line length 100.
- **Lazy imports:** Use deferred imports inside methods (e.g. `from az_scout_myplugin.routes import router`) to avoid circular imports at plugin discovery time.
- **Static dir:** Define `_STATIC_DIR = Path(__file__).parent / "static"` at module level.

### API routes

- Routes are mounted under `/plugins/{name}/` — define endpoints with relative paths (e.g. `@router.get("/hello")` → `/plugins/my-plugin/hello`).
- Routes receive context (tenant, region, subscription) as query parameters from the frontend.
- Use `async def` for route handlers.

### MCP tools

- MCP tool functions are plain Python functions with type annotations and docstrings.
- The docstring is the tool description shown to LLMs — keep it concise and helpful.
- Functions are registered on the MCP server automatically at startup.

### UI tabs

- Tabs use `TabDefinition(id, label, icon, js_entry, css_entry?)`.
- `icon` uses Bootstrap Icon classes (e.g. `"bi bi-puzzle"`).
- `js_entry` / `css_entry` are relative paths inside the plugin's static dir.
- Plugin tabs appear after built-in tabs (AZ Topology, Deployment Planner, Strategy Advisor).
- Plugin JS targets `#plugin-tab-{id}` as its container.
- URL hash `#{tab-id}` activates the plugin tab (deep-linking support).

### Frontend integration

Plugin JS runs after `app.js` and can use these globals:

| Global | Description |
|---|---|
| `apiFetch(url)` | GET with JSON parsing + error handling |
| `apiPost(url, body)` | POST helper |
| `tenantQS(prefix)` | Returns `?tenantId=…` or `""` |
| `subscriptions` | `[{id, name}]` array |
| `regions` | `[{name, displayName}]` array |

React to context changes:

```javascript
// Tenant change
document.getElementById("tenant-select")
    .addEventListener("change", () => { /* reload */ });

// Region change (hidden input, use MutationObserver)
const regionEl = document.getElementById("region-select");
let lastRegion = regionEl.value;
new MutationObserver(() => {
    if (regionEl.value !== lastRegion) {
        lastRegion = regionEl.value;
        // reload
    }
}).observe(regionEl, { attributes: true, attributeFilter: ["value"] });
```

Use the **HTML fragments pattern** — keep markup in `.html` files under `static/html/` and fetch at runtime instead of building HTML strings in JS.

### Chat modes

- `ChatMode(id, label, system_prompt, welcome_message)` adds extra buttons in the chat mode toggle.
- `system_prompt` is sent to the LLM — craft it for the plugin's domain.
- `welcome_message` is markdown displayed when the mode is activated.

### Testing plugins

- Test plugins independently with `pytest` and `httpx`.
- Mock `discover_plugins()` to inject test plugin instances.
- Use `register_plugins(app, mcp_server)` with a test FastAPI app.
- Follow the same testing patterns as the main project (mocked Azure API, `TestClient`).

### Plugin `pyproject.toml` template

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "az-scout-myplugin"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["az-scout", "fastapi"]

[project.entry-points."az_scout.plugins"]
my_plugin = "az_scout_myplugin:plugin"

[tool.hatch.build.targets.wheel]
packages = ["src/az_scout_myplugin"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
```

## Versioning

- Version is derived from git tags via `hatch-vcs` — never hardcode a version.
- `_version.py` is auto-generated and excluded from linting.
- Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.
- Update `CHANGELOG.md` before tagging a release.

## Design constraints & architectural rules

### Do

- Reuse `azure_api.py` for all Azure ARM interactions.
- Keep FastAPI routes as thin wrappers.
- Keep MCP tools as thin wrappers over `azure_api`.
- Include full type annotations on all public functions.
- Preserve dark/light theme compatibility.

### Do NOT

- Do NOT call Azure ARM APIs directly from `app.py` or `mcp_server.py`.
- Do NOT duplicate Azure API logic outside `azure_api.py`.
- Do NOT introduce frontend frameworks, npm, or build tooling.
- Do NOT introduce global mutable state.
- Do NOT perform heavy imports at module import time.
- Do NOT bypass plugin auto-discovery.
- Do NOT add synchronous blocking calls inside large subscription loops.
- Do NOT change API response shapes without updating tests.

## Backend design principles

- All business logic lives in `azure_api.py`.
- `app.py` and `mcp_server.py` contain no business logic.
- Per-subscription failures must not break global execution.
- Functions must be deterministic and side-effect free unless explicitly documented.
- No hidden state between requests.

### Response contract consistency

- API responses must be stable and predictable.
- If an error occurs for a specific subscription, return:

```json
{
  "subscription_id": "00000000-0000-0000-0000-000000000000",
  "error": {
    "code": "AuthorizationFailed",
    "message": "User is not authorized to perform this action."
  }
}
```

- Never raise an unhandled exception for per-subscription failures.

## Performance constraints

This tool may operate across dozens or hundreds of subscriptions. When generating code:

- Avoid O(n²) loops over subscriptions.
- Avoid re-authenticating inside loops.
- Avoid fetching full SKU catalogs when filters are provided.
- Respect ARM pagination (`nextLink`) efficiently.
- Do not load large datasets into memory unnecessarily.
- Future scalability should remain possible without architectural rewrite.

## Plugin isolation rules

Plugins must:

- Be fully self-contained.
- Not mutate global application state.
- Not assume ordering of plugin registration.
- Not introduce circular imports.
- Not import heavy modules at import time.
- Use lazy imports inside methods when possible.
- Respect the core app’s authentication and context model.
- Never override built-in routes.

## Testing enforcement

When modifying backend logic:

- Update pytest coverage.
- Maintain mocking of `requests.get` and `DefaultAzureCredential`.
- Never require live Azure calls in unit tests.
- Response schemas must remain backward compatible unless versioned.

Breaking changes require:

- Test updates
- `CHANGELOG` update
- New CalVer tag

## Code generation expectations (for Copilot)

When generating code:

- Always include type annotations.
- Prefer explicit return types.
- Prefer small pure functions.
- Prefer clarity over cleverness.
- Avoid metaprogramming.
- Avoid dynamic attribute access unless required.
- Avoid magic constants — define named constants.
