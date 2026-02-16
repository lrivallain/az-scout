# Copilot Instructions for az-mapping

## Project overview

`az-mapping` is a Python web tool that visualizes Azure Availability Zone logical-to-physical mappings across subscriptions. It uses a FastAPI backend and an MCP server, both calling shared Azure ARM REST API helpers, and a frontend with D3.js for graph rendering and vanilla JavaScript.

## Tech stack

- **Backend:** Python 3.11+, FastAPI 0.115+, uvicorn (ASGI server), click (CLI), azure-identity (DefaultAzureCredential), requests
- **MCP:** mcp[cli] (FastMCP), stdio and SSE transports
- **Frontend:** Vanilla JavaScript (no framework), D3.js v7, CSS custom properties (dark/light themes)
- **Packaging:** hatchling + hatch-vcs, CalVer (`YYYY.MM.MICRO`), src-layout
- **Tools:** uv (package manager), ruff (lint + format), mypy (strict), pytest, pre-commit

## Project structure

```
src/az_mapping/
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

## Versioning

- Version is derived from git tags via `hatch-vcs` — never hardcode a version.
- `_version.py` is auto-generated and excluded from linting.
- Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.
- Update `CHANGELOG.md` before tagging a release.
