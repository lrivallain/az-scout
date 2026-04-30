---
description: Bump PLUGIN_API_VERSION — update guard, document the change, draft a migration note, and audit known consumers.
---

Bump the plugin API version. The user describes the breaking or notable change.

## 1. Decide patch / minor / major

- **Major** — breaking: protocol method signature change, removed dataclass field, renamed export, changed call semantics.
- **Minor** — additive: new optional field on a dataclass, new helper function, new optional protocol method.
- **Patch** — internal: doc-only change, internal refactor with no contract impact (often **no bump needed**).

If unsure, ask the user — overestimating breaking changes is safer than underestimating.

## 2. Update the constant

Edit `src/az_scout/plugin_api.py`:

```python
PLUGIN_API_VERSION = "<new-version>"
```

## 3. Update the guard

In `src/az_scout/plugin_manager/` (or `plugins.py`), confirm the guard:

- Refuses plugins declaring an incompatible major version.
- Logs a clear, actionable error including: plugin name, plugin's declared version, core's `PLUGIN_API_VERSION`, link to migration notes.

Add or update a test in `tests/test_plugin_manager.py` covering:

- Compatible plugin loads.
- Incompatible-major plugin is rejected with the expected error.

## 4. Migration notes

Add a section to `docs/plugin-scaffold/CHANGELOG.md` (create if missing) and to the main `CHANGELOG.md`:

```
## [vYYYY.MM.MICRO] - YYYY-MM-DD

### Changed
- **Plugin API**: bumped to `<new-version>`. <one-line summary of impact>.

### Migration
- Old: `<example>`
- New: `<example>`
- Action plugin authors must take: <…>
```

## 5. Audit known consumers

For each plugin in this workspace and the published catalog
(`https://github.com/az-scout/plugin-catalog`), check:

- `src/az_scout/internal_plugins/topology/`
- `src/az_scout/internal_plugins/planner/`
- Sibling repos: `az-scout-plugin-*` (avs-sku, odcr-coverage, aks-placement-advisor, …)

For each one, list whether it needs a code change and link to the file/line.

## 6. Verify

```bash
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest tests/test_plugin_manager.py tests/test_plugins.py -q
```

## 7. Open follow-up issues

For each external plugin needing changes, open a tracking issue in its repo
via `mcp_github_issue_write` with the migration snippet and a link to this PR.
