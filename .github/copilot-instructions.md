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
├── app.py              # FastAPI bootstrap, lifespan, MCP mount, index + chat routes
├── logging_config.py   # Unified coloured logging (shared by core + plugins)
├── cli.py              # Click CLI (web + mcp subcommands)
├── plugin_api.py       # Public protocol + dataclasses for plugin authors
├── plugin_manager.py   # Install/validate/uninstall plugins (GitHub + PyPI)
├── plugins.py          # Plugin discovery, registration, hot-reload
├── azure_api/          # Shared Azure ARM logic (auth, pagination, data functions)
├── scoring/            # Deployment Confidence Score (shared by plugins)
├── services/           # Business logic (deployment planner, AI chat, etc.)
├── models/             # Pydantic models (deployment plan)
├── routes/             # API routes (discovery, plugin manager, auth)
├── internal_plugins/   # Built-in plugins (topology, planner)
├── templates/          # Jinja2 templates (index.html, login.html)
└── static/             # JS, CSS, images
tests/                  # pytest unit tests + e2e (Playwright)
```

## Code conventions

- **Python:** All functions must have type annotations (`disallow_untyped_defs = true`). Follow ruff rules: `E, F, I, W, UP, B, SIM`. Line length is 100.
- **JavaScript:** Vanilla JS only — no npm, no bundler, no frameworks. Use `const`/`let` (never `var`). Functions and variables use `camelCase`.
- **CSS:** Use CSS custom properties for theming. Both light and dark themes must be maintained.
- **HTML:** Minimal Jinja2 templating. Static assets referenced via `url_for('static', ...)`.

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
- Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.
- Update `CHANGELOG.md` before tagging a release.

## Design rules

### Do

- Reuse `azure_api/` for all Azure ARM interactions.
- Keep FastAPI routes and MCP tools as thin wrappers.
- Include full type annotations on all public functions.
- Preserve dark/light theme compatibility.

### Do NOT

- Do NOT call Azure ARM APIs directly from route handlers or MCP tools.
- Do NOT introduce frontend frameworks, npm, or build tooling.
- Do NOT introduce global mutable state.
- Do NOT perform heavy imports at module import time.
- Do NOT change API response shapes without updating tests.

## Code generation expectations

- Always include type annotations with explicit return types.
- Prefer small pure functions. Prefer clarity over cleverness.
- Avoid metaprogramming and dynamic attribute access.
- Avoid magic constants — define named constants.

## Contextual instructions

Domain-specific guidance is auto-loaded via `applyTo` patterns in `.github/instructions/`:

| File | Activates when editing |
|---|---|
| `azure-api.instructions.md` | `src/az_scout/azure_api/**` |
| `frontend.instructions.md` | `src/az_scout/static/**`, `src/az_scout/templates/**` |
| `obo-auth.instructions.md` | auth routes, OBO module, login template |
| `plugin-api.instructions.md` | core plugin contract (`plugin_api.py`, `plugins.py`, `plugin_manager/`) |
| `plugin-author.instructions.md` | any `az_scout_*/` package, `internal_plugins/`, scaffold docs |
| `mcp-tools.instructions.md` | `mcp_server.py`, any `tools.py` |
| `scoring.instructions.md` | `src/az_scout/scoring/**` |
| `cli.instructions.md` | `cli.py`, `__main__.py` |
| `tests.instructions.md` | `tests/**` |
| `docs.instructions.md` | `docs/**`, `mkdocs.yml` |
| `commit.instructions.md` | commit messages (`COMMIT_EDITMSG`) |

## Workflows

Reusable runbooks live in `.github/prompts/` (run with `/<name>`):

- `/ship-code-change` — quality gate → changelog → commit → PR
- `/tag-release` — CalVer release on `main`
- `/triage-issue` — investigate + post structured triage comment
- `/add-mcp-tool`, `/add-route` — scaffold new server-side surface
- `/bump-plugin-api` — version bump + guard + migration notes
- `/review-dependabot-pr` — dependency-aware smoke test + review

Personas with curated tool sets live in `.github/chatmodes/`:
`backend-engineer`, `frontend-engineer`, `plugin-reviewer`.

PR auto-review rules live in `.github/copilot-review-instructions.md`.
Cross-agent guidance lives in [`AGENTS.md`](../AGENTS.md).
