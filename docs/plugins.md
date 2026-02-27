# Plugin Development Guide

az-scout supports plugins — pip-installable Python packages that extend the application with custom API routes, MCP tools, UI tabs, static assets, and AI chat modes.

## How it works

1. A plugin registers a `az_scout.plugins` entry point in its `pyproject.toml`.
2. At startup, az-scout discovers all installed plugins via `importlib.metadata.entry_points`.
3. Each plugin object must satisfy the `AzScoutPlugin` protocol (see below).
4. Routes, tools, tabs, and chat modes are wired automatically — no configuration needed.

## Quick start

```bash
# Copy the scaffold
cp -r docs/plugin-scaffold az-scout-myplugin
cd az-scout-myplugin

# Edit pyproject.toml (name, entry point)
# Implement your plugin in src/az_scout_myplugin/__init__.py

# Install in dev mode alongside az-scout
uv pip install -e .

# Restart az-scout — your plugin is active
az-scout
```

## Plugin protocol

Every plugin must expose an object with these attributes:

```python
class AzScoutPlugin(Protocol):
    name: str       # unique plugin identifier
    version: str    # semver or calver string

    def get_router(self) -> APIRouter | None: ...
    def get_mcp_tools(self) -> list[Callable] | None: ...
    def get_static_dir(self) -> Path | None: ...
    def get_tabs(self) -> list[TabDefinition] | None: ...
    def get_chat_modes(self) -> list[ChatMode] | None: ...
```

All methods are optional — return `None` to skip a layer.

## Extension points

| Layer | Method | What it does |
|---|---|---|
| **API routes** | `get_router()` | Returns a FastAPI `APIRouter`, mounted at `/plugins/{name}/` |
| **MCP tools** | `get_mcp_tools()` | List of functions registered as MCP tools on the server |
| **UI tabs** | `get_tabs()` | `TabDefinition` list — rendered as Bootstrap tabs in the main UI |
| **Static assets** | `get_static_dir()` | `Path` to a directory, served at `/plugins/{name}/static/` |
| **Chat modes** | `get_chat_modes()` | `ChatMode` list — added to the chat panel mode toggle |

### TabDefinition

```python
@dataclass
class TabDefinition:
    id: str                    # e.g. "cost-analysis"
    label: str                 # e.g. "Cost Analysis"
    icon: str                  # Bootstrap icon class, e.g. "bi bi-cash-coin"
    js_entry: str              # relative path to JS file in the plugin's static dir
    css_entry: str | None = None  # optional CSS file, auto-loaded in <head>
```

### ChatMode

```python
@dataclass
class ChatMode:
    id: str                 # e.g. "cost-advisor"
    label: str              # e.g. "Cost Advisor"
    system_prompt: str      # system prompt sent to the LLM
    welcome_message: str    # markdown shown when the mode is activated
```

## Entry point registration

In your plugin's `pyproject.toml`:

```toml
[project.entry-points."az_scout.plugins"]
my_plugin = "az_scout_myplugin:plugin"
```

The `plugin` object at module level must satisfy `AzScoutPlugin`.

## Plugin scaffold

A ready-to-use starter template is available at [`docs/plugin-scaffold/`](plugin-scaffold/). Copy it and customise.

## UI integration

- Plugin tabs appear after the built-in tabs (AZ Topology, Deployment Planner, Strategy Advisor).
- Plugin JS files are loaded after `app.js` — they can access all existing globals.
- Plugin JS should target `#plugin-tab-{id}` as the container for its content.
- Plugin chat modes appear as extra buttons in the chat mode toggle.
- URL hash is updated when a tab is selected — plugin tabs use `#<tab-id>` (e.g. `#example`).
  Opening a URL with that hash will activate the plugin tab automatically.

### Accessing main app state

Plugin scripts run after `app.js` and can use these globals and DOM elements:

| Global / Element | Type | Description |
|---|---|---|
| `apiFetch(url)` | `function` | GET helper with JSON parsing + error handling |
| `apiPost(url, body)` | `function` | POST helper |
| `tenantQS(prefix)` | `function` | Returns `?tenantId=…` or `""` for the selected tenant |
| `subscriptions` | `Array` | `[{id, name}]` — subscriptions for the current tenant |
| `regions` | `Array` | `[{name, displayName}]` — AZ-enabled regions |
| `#tenant-select` | `<select>` | Current tenant — listen for `"change"` events |
| `#region-select` | `<input hidden>` | Current region — observe with `MutationObserver` |

#### Reacting to tenant / region changes

```javascript
// Listen for tenant changes
document.getElementById("tenant-select")
    .addEventListener("change", () => { /* reload plugin data */ });

// Region is a hidden input, observe value changes
const regionEl = document.getElementById("region-select");
let lastRegion = regionEl.value;
new MutationObserver(() => {
    if (regionEl.value !== lastRegion) {
        lastRegion = regionEl.value;
        // reload plugin data
    }
}).observe(regionEl, { attributes: true, attributeFilter: ["value"] });
```

#### Fetching subscriptions

Plugins can call the same API the main app uses:

```javascript
const subs = await apiFetch("/api/subscriptions" + tenantQS("?"));
// subs = [{id: "…", name: "My Sub"}, …]
```

### Static assets

All files in `get_static_dir()` are served at `/plugins/{name}/static/`.

| Asset type | How it works |
|---|---|
| **CSS** | Set `css_entry` on `TabDefinition` → auto-injected as a `<link>` tag in `<head>` |
| **JS** | Set `js_entry` on `TabDefinition` → auto-loaded as a `<script>` before `</body>` |
| **HTML** | Place HTML fragments in the static dir, fetch them from JS at runtime |
| **Images** | Reference directly: `/plugins/{name}/static/img/logo.svg` |

#### HTML fragments pattern

Instead of building HTML strings in JavaScript, keep markup in a separate `.html` file
and fetch it when the tab activates:

```javascript
async function initTab() {
  const pane = document.getElementById('plugin-tab-example');
  const resp = await fetch('/plugins/example/static/html/example-tab.html');
  pane.innerHTML = await resp.text();
  // bind event listeners after injection
}
```

## Testing

Plugins can be tested independently. The main app provides:

- `discover_plugins()` — can be mocked to inject test plugins.
- `register_plugins(app, mcp_server)` — accepts any FastAPI app and MCP server.
- `get_plugin_metadata()` — returns serialisable metadata for template context.

```python
from az_scout.plugins import register_plugins

def test_my_plugin():
    plugin = MyPlugin()
    # ... assert plugin satisfies AzScoutPlugin protocol
    assert isinstance(plugin, AzScoutPlugin)
```
