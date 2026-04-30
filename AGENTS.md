# AGENTS.md

Guidance for any AI coding agent (GitHub Copilot, Claude, Codex, Aider, Cursor, …) working in this repo.

## Where the rules live

The authoritative agent guidance is structured under `.github/`:

- [`.github/copilot-instructions.md`](.github/copilot-instructions.md) — project-wide invariants (always loaded by Copilot).
- [`.github/instructions/`](.github/instructions/) — domain-scoped rules with `applyTo` patterns:
  - `azure-api.instructions.md` — ARM helpers, auth, pagination
  - `frontend.instructions.md` — vanilla JS, CSS theming, XSS rules
  - `obo-auth.instructions.md` — OBO flow, sessions, admin RBAC
  - `plugin-api.instructions.md` — core-side plugin contract
  - `plugin-author.instructions.md` — plugin authoring conventions
  - `tests.instructions.md`, `scoring.instructions.md`, `mcp-tools.instructions.md`,
    `cli.instructions.md`, `docs.instructions.md`, `commit.instructions.md`
- [`.github/prompts/`](.github/prompts/) — repeatable workflows (slash-prompts in Copilot Chat).
- [`.github/skills/`](.github/skills/) — multi-step domain recipes.
- [`.github/chatmodes/`](.github/chatmodes/) — persona-scoped tool sets.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — human contributor guide.

Non-Copilot agents that don't auto-load `applyTo` should read `copilot-instructions.md` plus any
`instructions/*.md` files matching the files they're editing.

## Quality gate (must pass before commit)

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

The workspace task `Dev: run all checks` runs the same sequence.
Pre-commit hooks enforce these on `git commit`.

## Versioning

- CalVer: `vYYYY.MM.MICRO` (e.g. `v2026.4.1`).
- Version is derived from git tags via `hatch-vcs` — **never hardcode**.
- Update `CHANGELOG.md` under `## Unreleased` before every PR; release tags only on `main`.

## Branch protection

- `main` is protected — direct pushes are rejected. Use a feature branch + PR.
- Tag pushes happen **only after** the release PR is merged.

## Hard constraints

- Reuse `azure_api/` helpers for **every** ARM call — never use `requests.get/post` directly.
- Vanilla JS only on the frontend — no npm, no bundler, no framework imports.
- Type-annotate every Python function (mypy `disallow_untyped_defs = true`).
- Always escape with `escapeHtml()` before any `innerHTML` or attribute interpolation.
- Maintain dark-theme parity for any CSS change.

## Where to find runnable examples

- `src/az_scout/internal_plugins/topology/` — tab + MCP tool, D3 graph
- `src/az_scout/internal_plugins/planner/` — multi-tool chat mode, deployment planning
- `tests/conftest.py` — canonical test setup (mocked credential, ARM mock wiring)
