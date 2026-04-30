---
description: Add a new FastAPI route to az-scout — handler, Pydantic models, registration, tests, docs.
---

Add a new HTTP route. The user provides the path, method, and purpose (or asks you to design them).

## 1. Decide where the route lives

| Audience | Location |
|----------|----------|
| Core API (e.g. `/api/...`, `/auth/...`) | `src/az_scout/routes/<area>.py` |
| Internal plugin | `src/az_scout/internal_plugins/<name>/routes.py` |
| External plugin | the plugin's own `routes.py` exposed via `get_router()` |

Plugin routes are mounted at `/plugins/<plugin-name>/` — keep paths inside the router relative.

## 2. Define Pydantic models

Add request and response models to `src/az_scout/models/<area>.py` (or the plugin's `models.py`).

- Use explicit field types and `Field(description=...)` for any non-obvious field.
- Reuse existing models when shapes overlap (`SkuRecommendation`, `DeploymentSignals`, …).

## 3. Implement the handler

- Keep handlers **thin**: parse inputs → call a function in `services/` or `azure_api/` → return the model.
- Type-annotate every parameter and the return.
- Use FastAPI dependencies (`Depends(...)`) for cross-cutting concerns (auth, tenant ID).
- For Azure access, use `azure_api/` helpers exclusively.
- Return per-subscription errors **inside** the response payload, not as 500s.

## 4. Register the router

- **Core:** add `app.include_router(<router>)` in the appropriate place (`app.py` or routes aggregator).
- **Plugin:** the existing plugin loader mounts `get_router()` automatically — no extra wiring needed.

## 5. Tests

Add to `tests/test_routes.py` (or the plugin's tests):

- Use the `client` fixture from `tests/conftest.py`.
- Mock ARM calls (never live Azure).
- Cover: happy path, missing required param, error from upstream, edge case unique to the route.
- Assert the response **schema**, not just the status code.

## 6. Documentation

- **`docs/api.md`** — add the route to the relevant section with: method, path, params, response shape, example.
- If user-facing, mention it in `docs/features.md` or the relevant feature page.

## 7. Changelog

Add under `## Unreleased` → `### Added`:

```
- **API**: New `<METHOD> /api/...` route — <one-line purpose>.
```

## 8. Verify

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest tests/test_routes.py -q
```
