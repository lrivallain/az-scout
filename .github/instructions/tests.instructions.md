---
description: "pytest conventions, fixtures, mocking ARM, Playwright e2e. USE WHEN editing tests/ or adding new tests."
applyTo: "tests/**"
---

# Test conventions

## Layout

- Unit tests in `tests/test_*.py` — one file per module under test.
- E2E tests in `tests/e2e/` — Playwright-driven, run last automatically (see `pytest_collection_modifyitems` in `conftest.py`).
- Fixtures in `tests/conftest.py` (top-level) or `tests/fixtures/` (data files).

## Always use the canonical fixtures

From `tests/conftest.py`:

| Fixture | Scope | Purpose |
|---|---|---|
| `client` | function | `TestClient(app, raise_server_exceptions=False)` — exception handlers run as in prod |
| `_mock_credential` | autouse | Stubs `DefaultAzureCredential` so no real Azure calls happen |
| `_sync_arm_requests_mock` | autouse | Aliases `_arm.requests` with the package-level `requests` so mocks flow through |
| `_clear_usage_cache` | autouse | Clears the compute-usages cache between tests |

Don't redefine these locally — extend or compose them.

## Mocking Azure

- Patch `az_scout.azure_api.requests.get` (or `.post`) — this is the re-export that `_arm` reads via the autouse alias fixture.
- Build response objects with the helper pattern: `MagicMock(status_code=200, json=lambda: {...})`.
- Never make a real ARM call from a test — even read-only ones. Use a fixture in `tests/fixtures/`.

## Style

- One behavior per test. Test names: `test_<unit>_<scenario>_<expected>`.
- Assert response **schema**, not just `status_code == 200`.
- For per-subscription error paths, assert the error appears inside the response body (not as 500).
- Use `pytest.mark.parametrize` to cover input variants instead of duplicating tests.

## Plugin tests

- Use `discover_plugins` mocking patterns in `tests/test_plugins.py` to inject fake plugins.
- Test the plugin protocol surface, not internal implementation.

## Running

```bash
uv run pytest                            # all tests
uv run pytest tests/test_routes.py -q    # one file
uv run pytest -k "test_compute" -q       # by name
uv run pytest tests/e2e/ -q              # E2E only (requires Playwright browsers)
```

## Don't

- Don't use `time.sleep()` — use `pytest`'s `monkeypatch` for time, or a deterministic loop.
- Don't write tests that depend on environment variables unless you patch them with `monkeypatch.setenv`.
- Don't disable the autouse fixtures.
- Don't `@pytest.mark.skip` an existing test without a written reason in the PR body.
