# Copilot review checklist for az-scout

Apply these rules when reviewing pull requests in this repository.
Group findings as **Blocking**, **Should fix**, or **Nice to have**.

## Blocking — must be fixed before merge

- Direct `requests`, `httpx`, or `azure-mgmt-*` calls outside `src/az_scout/azure_api/`.
  All ARM access must go through `arm_get`, `arm_post`, `arm_paginate`, or `get_headers`.
- New Python functions (or methods) without complete type annotations, including return types.
- New JS code using `var`, jQuery, npm imports, or any framework.
- `innerHTML` / attribute interpolation of dynamic values without `escapeHtml()`.
- Missing or stale entry in `CHANGELOG.md` under `## Unreleased` for any user-visible change.
- Hardcoded version strings (the version comes from `hatch-vcs` / git tags).
- New global mutable module-level state.
- Heavy imports at module top level inside plugin code.
- Tests deleted or `@pytest.mark.skip`'d without an explicit justification in the PR body.
- Plugin behavior change without a `PLUGIN_API_VERSION` bump when the contract is affected.
- Secret-like values (tokens, client secrets, keys) committed in code, fixtures, or logs.
- New env vars without documentation in README and `docs/getting-started.md`.

## Should fix — strongly recommended

- New MCP tool added without updating the README MCP table and `docs/ai/mcp.md`.
- New FastAPI route without a Pydantic response model or without a corresponding test.
- CSS additions that lack `[data-theme="dark"]` overrides for new colors.
- Per-subscription Azure errors raised instead of returned in the response payload.
- Missing docstring on new MCP tool functions or public service functions.
- Magic numbers / strings that should be named constants.
- O(n²) loops over subscriptions, regions, or SKUs.
- Re-authenticating inside loops (the token cache is per-tenant — pull credentials once).

## Nice to have

- Opportunities to reuse shared frontend components from `static/js/components/`.
- Renaming opportunities for clarity (small functions, descriptive variables).
- Comments explaining non-obvious branches (rare — prefer small clear functions).

## Style baseline

- ruff rules `E, F, I, W, UP, B, SIM`, line length 100.
- mypy strict mode (`disallow_untyped_defs = true`).
- biome lint clean for JS/TS-ish files.
- Conventional Commits in PR title (e.g. `feat(scope): summary`).

## Out of scope — do not flag

- Auto-generated `_version.py`.
- Files under `site/`, `__pycache__/`, `.venv/`, `node_modules/`.
- Pre-existing TODOs not touched by the PR.
