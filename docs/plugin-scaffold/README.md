# az-scout-example

A minimal az-scout plugin scaffold. Copy this directory and customise.

## Features

- **UI tab** with subscription selector that reacts to the main app's tenant & region
- **API route** that receives tenant, region, and subscription context
- **MCP tool** exposed on the MCP server
- **Static assets** — CSS auto-loaded, HTML fragment fetched at runtime
- **URL hash routing** — `#example` selects the plugin tab

## Setup

```bash
# Clone in /tmp to export scaffold without git history
git clone https://github.com/lrivallain/az-scout.git /tmp/az-scout
cp -r /tmp/az-scout/docs/plugin-scaffold ./az-scout-myplugin
cd ./az-scout-myplugin

# Update pyproject.toml: name, entry point, package name
# Rename src/az_scout_example/ to match your package

uv pip install -e .
az-scout  # plugin is auto-discovered
```

## Structure

```
az-scout-example/
├── .github/
│   ├── copilot-instructions.md  # Copilot context for this plugin
│   └── workflows/
│       └── ci.yml               # CI pipeline (lint + test, Python 3.11–3.13)
├── pyproject.toml
├── README.md
└── src/
    └── az_scout_example/
        ├── __init__.py          # Plugin class + module-level `plugin` instance
        ├── routes.py            # FastAPI APIRouter (optional)
        ├── tools.py             # MCP tool functions (optional)
        └── static/
            ├── css/
            │   └── example.css      # Plugin styles (auto-loaded via css_entry)
            ├── html/
            │   └── example-tab.html # HTML fragment (fetched by JS at runtime)
            └── js/
                └── example-tab.js   # Tab UI logic (auto-loaded via js_entry)
```

## How it works

1. The plugin JS loads the HTML fragment into `#plugin-tab-example`.
2. It watches `#tenant-select` and `#region-select` for changes.
3. When both are set, it fetches subscriptions from `/api/subscriptions`.
4. The user picks a subscription and clicks the button.
5. The plugin calls `GET /plugins/example/hello?subscription_name=…&tenant=…&region=…`.

## Quality checks

The scaffold includes GitHub Actions workflows in `.github/workflows/`:

- **`ci.yml`** — Runs lint (ruff + mypy) and tests (pytest) on Python 3.11–3.13, triggered on push/PR to `main`.
- **`publish.yml`** — Builds, creates a GitHub Release, and publishes to PyPI via trusted publishing (OIDC). Triggered on version tags (`v*`). Requires a `pypi` environment configured in your repo settings with OIDC trusted publishing.

Run the same checks locally:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

To publish a release:

```bash
git tag v2026.2.0
git push origin v2026.2.0
```

## Copilot support

The `.github/copilot-instructions.md` file provides context to GitHub Copilot about
the plugin structure, conventions, and az-scout plugin API. It helps Copilot generate
code that follows the project patterns.
