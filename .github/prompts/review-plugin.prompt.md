---
description: Review an external az-scout plugin for convention compliance, correctness, and best practices.

tools:
  - un_in_termina
  - mcp_github_get_file_contents
---

Review an external az-scout plugin for compliance with project conventions.
The user will provide the plugin path (local) or GitHub repository URL.

## 1. Gather plugin sources

If a **local path** is provided, read files directly from that directory.
If a **GitHub URL** is provided, use `mcp_github_get_file_contents` to fetch key files.

Collect these files (all are expected to exist):

- `pyproject.toml`
- `src/<module>/__init__.py` (plugin class)
- `src/<module>/routes.py` (if API routes are provided)
- `src/<module>/tools.py` (if MCP tools are provided)
- Any JS/CSS/HTML files under `static/`

## 2. Naming conventions

Verify:

| Item | Expected pattern | Example |
|------|-----------------|---------|
| Package name | `az-scout-{slug}` | `az-scout-batch-sku` |
| Module name | `az_scout_{slug}` | `az_scout_batch_sku` |
| Entry point key | `{slug}` (under `az_scout.plugins` group) | `batch_sku` |
| Plugin `name` attribute | `"{slug}"` (kebab-case) | `"batch-sku"` |
| `TabDefinition.id` | matches slug | `"batch-sku"` |

Flag any mismatch.

## 3. Packaging and build

Check `pyproject.toml` for:

- [ ] Build backend is `hatchling` (with `hatch-vcs` if using dynamic version)
- [ ] `requires-python = ">=3.11"`
- [ ] `az-scout` and `fastapi` are listed in `dependencies`
- [ ] Entry point is correctly declared under `[project.entry-points."az_scout.plugins"]`
- [ ] `[tool.hatch.build.targets.wheel]` specifies `packages = ["src/<module>"]`
- [ ] If using `hatch-vcs`: has `raw-options.fallback_version` for editable-install safety
- [ ] Ruff config: `line-length = 100`, `target-version = "py311"`, rules include `E, F, I, W, UP, B, SIM`
- [ ] Mypy config: `python_version = "3.11"`, `strict = true`

## 4. Plugin protocol compliance

Check the plugin class in `__init__.py`:

- [ ] Has `name: str` and `version: str` attributes
- [ ] `get_router()` returns `APIRouter | None` (lazy import of router module)
- [ ] `get_mcp_tools()` returns `list[Callable] | None` (lazy import of tool functions)
- [ ] `get_static_dir()` returns `Path | None` (uses `_STATIC_DIR = Path(__file__).parent / "static"`)
- [ ] `get_tabs()` returns `list[TabDefinition] | None`
- [ ] `get_chat_modes()` returns `list[ChatMode] | None`
- [ ] Optional: `get_system_prompt_addendum()` returns `str | None`
- [ ] Module-level `plugin = PluginClass()` instance exists
- [ ] All methods use **lazy imports** (no heavy imports at module top-level)
- [ ] No global mutable state

## 5. Type annotations

- [ ] All functions have type annotations (parameters and return types)
- [ ] `from __future__ import annotations` is NOT required (Python 3.11+) but acceptable
- [ ] MCP tool docstrings are present (they become tool descriptions for LLMs)

## 6. Frontend conventions (if UI tab exists)

- [ ] JS uses `const`/`let` (never `var`), `camelCase` naming
- [ ] Tab container targets `#plugin-tab-{slug}`
- [ ] Uses `azscout:*` events for context changes (not MutationObserver or direct DOM watchers)
- [ ] HTML loaded as fragments from `static/html/` (not inline strings in JS)
- [ ] CSS uses custom properties from core `:root`, supports dark/light themes
- [ ] Static assets reference paths under `/plugins/{slug}/static/`

## 7. Isolation rules

- [ ] Plugin does not mutate global application state
- [ ] Plugin does not override built-in routes
- [ ] Plugin does not import heavy modules at import time
- [ ] Plugin does not assume plugin registration ordering
- [ ] Plugin respects the core app's authentication model (uses `tenantQS`, etc.)

## 8. Run lint checks (local path only)

If the plugin is available locally, run:

```bash
cd <plugin-path>
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

Report any failures.

## 9. Report findings

Produce a structured review summary:

```markdown
## Plugin Review: <plugin-name>

### ✅ Passes
- …

### ⚠️ Warnings (non-blocking)
- …

### ❌ Issues (must fix)
- …

### Recommendations
- …
```

If a GitHub issue or PR is associated, post the review there. Otherwise, present it to the user.
