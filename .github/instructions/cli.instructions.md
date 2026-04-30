---
description: "Click CLI conventions, chat REPL, and entry-point wiring for az-scout. USE WHEN editing src/az_scout/cli.py."
applyTo: "src/az_scout/cli.py,src/az_scout/__main__.py"
---

# CLI conventions

## Subcommand structure

`az-scout` is a Click group with three subcommands:

| Command | Purpose |
|---|---|
| `az-scout web`  | Run FastAPI + uvicorn (default when no subcommand is given) |
| `az-scout mcp`  | Run the MCP server (stdio or Streamable HTTP via `--http`) |
| `az-scout chat` | Inline AI chat REPL (one-shot or interactive) |

The default subcommand is `web` (see `cli()` invoking `web` when `invoked_subcommand is None`).

## Always

- Use Click `@click.option` / `@click.argument` with `show_default=True` and a clear `help`.
- Type-annotate every command parameter and return type.
- Keep heavy imports **inside** the command function, not at module top, so
  `az-scout --help` stays fast.
- Verbose flag (`-v` / `--verbose`) flips logging level — re-use `az_scout.logging_config`.
- Consistent option naming: `--host`, `--port`, `--reload`, `--no-open`, `--proxy-headers`.

## Never

- Never block the event loop in `web` mode — use uvicorn's worker model.
- Never print secrets to stdout (tokens, OBO secrets, session cookies).
- Never call Azure ARM directly from CLI code — go through `azure_api/` helpers
  (CLI mode falls back to `DefaultAzureCredential` since there's no middleware).

## When adding a new subcommand

1. Add a `@cli.command()` decorated function with type-annotated options.
2. Register it via the existing group; no entry-point change needed.
3. Update `README.md` "Quick start" or "Usage" section.
4. Update `docs/getting-started.md`.
5. If the subcommand exposes new MCP tools or routes, follow `add-mcp-tool.prompt.md`
   or `add-route.prompt.md`.

## Verify

```bash
uv run az-scout --help                  # group help
uv run az-scout <subcommand> --help     # per-command help
uv run pytest tests/test_cli_chat.py -q
```
