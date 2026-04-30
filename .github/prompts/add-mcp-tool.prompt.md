---
description: Add a new MCP tool to az-scout — implement the function, register it on the server, document it, and test it.
---

Add a new MCP tool. The user provides the tool name, purpose, and inputs (or asks you to design them).

## 1. Decide where the tool lives

| Audience | Location |
|----------|----------|
| Core (always available) | `src/az_scout/mcp_server.py` (or a helper module imported there) |
| Tied to a specific feature already in `internal_plugins/<name>/` | `src/az_scout/internal_plugins/<name>/tools.py` |
| Tied to a sibling plugin | the plugin's own `tools.py` exposed via `get_mcp_tools()` |

Always discuss with the user if it's not obvious — adding to `internal_plugins/planner` vs core is a design decision.

## 2. Implement the function

- Plain `def` (or `async def` if it awaits I/O).
- All parameters use `Annotated[<type>, Field(description="…")]`.
- Optional params have sane defaults and are documented as optional in the docstring.
- The docstring is the **tool description** the LLM sees — write it as instructions, not prose:
  - First paragraph: what it returns.
  - Bullet list: when to use it, important flags, edge cases.
  - Mention any flag that gates expensive work (pricing, spot scores).
- Returns a JSON-serializable dict or a `json.dumps(...)` string. Match the style of neighboring tools in the same file.
- All Azure ARM access goes through `az_scout.azure_api` helpers.

## 3. Register the tool

- **Core tool:** add to the list passed to `FastMCP` in `src/az_scout/mcp_server.py`.
- **Plugin tool:** include in the plugin's `get_mcp_tools()` return list.

Do **not** decorate with `@mcp.tool` — registration is centralized.

## 4. Tests

Add to `tests/test_mcp_server.py` (or the plugin's test module):

- Mock the underlying ARM call.
- Assert the tool returns the expected shape.
- Assert at least one filter / option behaves as documented.

## 5. Documentation

- **`README.md`** — add a row to the MCP tools table (`## MCP Server` section).
- **`docs/ai/mcp.md`** — add a section with: name, purpose, parameters table, example request/response.
- If the tool changes a chat experience, update the relevant chat-mode docs.

## 6. Changelog

Add under `## Unreleased` → `### Added`:

```
- **MCP**: New `<tool_name>` tool — <one-line purpose>.
```

## 7. Verify

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest tests/test_mcp_server.py -q
```

Then optionally smoke-test by listing tools:

```bash
uv run az-scout mcp --stdio   # then send tools/list from your MCP client
```
