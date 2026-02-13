# Copilot Instructions for az-mapping

## Project overview

`az-mapping` is a Python web tool that visualizes Azure Availability Zone logical-to-physical mappings across subscriptions. It uses a Flask backend calling Azure ARM REST APIs, and a frontend with D3.js for graph rendering and vanilla JavaScript.

## Tech stack

- **Backend:** Python 3.11+, Flask 3.0+, click (CLI), azure-identity (DefaultAzureCredential), requests
- **Frontend:** Vanilla JavaScript (no framework), D3.js v7, CSS custom properties (dark/light themes)
- **Packaging:** hatchling + hatch-vcs, CalVer (`YYYY.MM.MICRO`), src-layout
- **Tools:** uv (package manager), ruff (lint + format), mypy (strict), pytest, pre-commit

## Project structure

```
src/az_mapping/
├── app.py            # Flask routes, CLI entry point, Azure API calls
├── templates/
│   └── index.html    # Single-page Jinja2 template
└── static/
    ├── js/app.js     # All frontend logic (D3 graph, table, filters, theme)
    ├── css/style.css  # Styles with CSS variables for light/dark mode
    └── img/           # SVG icons (favicon, filter icons)
tests/
└── test_routes.py    # pytest tests with mocked Azure API responses
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

## Testing patterns

- Tests use Flask's test client (`app.test_client()`).
- Azure API calls are mocked with `unittest.mock.patch` on `requests.get` and `DefaultAzureCredential`.
- Tests are grouped by endpoint in `unittest.TestCase` subclasses.
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
