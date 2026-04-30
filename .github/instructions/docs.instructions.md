---
description: "mkdocs Material conventions for az-scout docs. USE WHEN editing docs/ or mkdocs.yml."
applyTo: "docs/**,mkdocs.yml,tools/docs_hooks/**"
---

# Documentation conventions

## Stack

- **mkdocs Material** with light/dark palette toggle.
- Markdown extensions: `admonition`, `pymdownx.details`, `pymdownx.superfences`
  (incl. mermaid), `pymdownx.tabbed`, `pymdownx.snippets`, `attr_list`, `tables`.
- Hooks in `tools/docs_hooks/pre_post_build.py`.
- Site name: **Azure Scout**. Repo: `az-scout/az-scout`. Edit URI enabled.

## File layout

```
docs/
├── index.md             # landing page
├── getting-started.md
├── features.md
├── api.md               # HTTP API reference
├── architecture.md
├── scoring.md           # Deployment Confidence Score docs
├── _changelog.md        # injected from CHANGELOG.md via the build hook
├── ai/                  # AI chat + MCP docs
├── deployment/          # Azure deployment guides
├── plugin-scaffold/     # plugin author guide
├── plugins/             # per-plugin pages
├── _includes/           # shared snippets (use pymdownx.snippets)
└── assets/, css/        # images and overrides
```

## Style

- Use sentence-case for headings (e.g. "Getting started", not "Getting Started").
- Code blocks always have a language tag (`bash`, `python`, `json`, `yaml`, `mermaid`, `text`).
- Use admonitions for callouts:
  ```markdown
  !!! note
      Short note.

  !!! warning "Heads up"
      Important caveat.
  ```
- Diagrams: prefer mermaid in `pymdownx.superfences` blocks.
- Cross-link with relative paths: `[Scoring](../scoring.md)`.
- Snippets via `--8<-- "_includes/<file>.md"` (pymdownx.snippets, base path `docs/`).

## After a feature change

If the change affects user-visible behavior, update — in this order:

1. `README.md` (quick start + feature list).
2. The relevant page under `docs/` (often `features.md`, `api.md`, or a per-feature page).
3. `docs/_changelog.md` is **auto-generated** — only edit `CHANGELOG.md`.
4. `docs/ai/mcp.md` for any new MCP tool.
5. `docs/plugin-scaffold/` for any plugin contract change (and bump `PLUGIN_API_VERSION`).

## Verify

```bash
uv run mkdocs build --strict           # fails on broken links / missing files
uv run mkdocs serve -a 127.0.0.1:8000  # local preview
```

## Don't

- Don't hand-edit `docs/_changelog.md` — it's regenerated from `CHANGELOG.md`.
- Don't link to `site/` — that's the build output.
- Don't add docs-only npm dependencies; use mkdocs plugins instead.
- Don't embed secrets in example commands — use `<placeholder>` syntax.
