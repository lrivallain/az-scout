# Copilot Instructions for az-scout-example

## Project overview

This is an **az-scout plugin** — a Python package that extends [az-scout](https://github.com/lrivallain/az-scout) with custom API routes, MCP tools, UI tabs, and chat modes. Plugins are auto-discovered via the `az_scout.plugins` entry-point group.

## Tech stack

- **Backend:** Python 3.11+, FastAPI (APIRouter), az-scout plugin API
- **Frontend:** Vanilla JavaScript (no framework, no npm), CSS custom properties
- **Packaging:** hatchling + hatch-vcs, CalVer (`YYYY.MM.MICRO`), src-layout
- **Tools:** uv (package manager), ruff (lint + format), mypy, pytest

## Project structure

```
src/az_scout_example/
├── __init__.py          # Plugin class + module-level `plugin` instance
├── routes.py            # FastAPI APIRouter (mounted at /plugins/example/)
├── tools.py             # MCP tool functions (exposed on the az-scout MCP server)
└── static/
    ├── css/
    │   └── example.css      # Plugin styles (auto-loaded via css_entry)
    ├── html/
    │   └── example-tab.html # HTML fragment (fetched by JS at runtime)
    └── js/
        └── example-tab.js   # Tab UI logic (auto-loaded via js_entry)
```

## Plugin API

The plugin class in `__init__.py` implements the `AzScoutPlugin` protocol:

| Method | Returns | Purpose |
|---|---|---|
| `get_router()` | `APIRouter \| None` | API routes mounted at `/plugins/{name}/` |
| `get_mcp_tools()` | `list[Callable] \| None` | Functions registered as MCP tools |
| `get_static_dir()` | `Path \| None` | Static assets served at `/plugins/{name}/static/` |
| `get_tabs()` | `list[TabDefinition] \| None` | UI tabs injected into the main app |
| `get_chat_modes()` | `list[ChatMode] \| None` | Custom AI chat modes |

The entry point in `pyproject.toml` connects the plugin to az-scout:

```toml
[project.entry-points."az_scout.plugins"]
example = "az_scout_example:plugin"
```

## Code conventions

- **Python:** All functions must have type annotations. Follow ruff rules: `E, F, I, W, UP, B, SIM`. Line length is 100.
- **JavaScript:** Vanilla JS only — no npm, no bundler, no frameworks. Use `const`/`let` (never `var`). Functions and variables use `camelCase`.
- **CSS:** Use CSS custom properties for theming. Support both light and dark modes using `[data-theme="dark"]` selectors. The main app's CSS variables are available to plugins.

## Frontend patterns

- The plugin tab container is `#plugin-tab-{name}`. Load HTML fragments into it.
- Watch `#tenant-select` and `#region-select` via `MutationObserver` / change events to react to user context changes.
- Fetch subscriptions from `/api/subscriptions?tenant_id=…` when tenant changes.
- Plugin static assets are at `/plugins/{name}/static/…`.

## MCP tool patterns

- MCP tools are plain Python functions with type annotations and a docstring.
- The docstring becomes the tool description in the MCP server and AI chat.
- Tools are automatically available in the AI chat assistant after plugin registration.
- Keep tool functions stateless — use parameters, not global state.

## Testing patterns

- Test API routes using FastAPI's `TestClient`.
- Mock az-scout internals with `unittest.mock.patch` when needed.
- Run with: `uv run pytest`

## Quality checks

Before committing, ensure all checks pass:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## CI/CD

- **CI** (`.github/workflows/ci.yml`): Runs lint and tests on push/PR to `main`. Also callable via `workflow_call` for reuse.
- **Publish** (`.github/workflows/publish.yml`): Triggered on version tags (`v*`). Runs CI gate → builds package → creates GitHub Release → publishes to PyPI via trusted publishing (OIDC). Requires a `pypi` environment in repo settings.

## Versioning

- Version is derived from git tags via `hatch-vcs` — never hardcode a version.
- `_version.py` is auto-generated and excluded from linting.
- Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.
