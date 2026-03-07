---
description: Prepare a code change for shipping — run quality checks, update changelog and docs, commit, and open a PR.

tools:
  - run_in_terminal
  - get_changed_files
  - get_errors
  - create_and_run_task
  - mcp_github_create_branch
  - mcp_github_push_files
  - mcp_github_create_pull_request
---

Prepare the current working-tree changes for shipping by executing each step below.
Stop and report clearly if any step fails.

## 1. Gather context

- Use `get_changed_files` to list all staged & unstaged changes.
- Read `CHANGELOG.md` and `.github/pull_request_template.md` for current format.
- Identify any related GitHub issue numbers from branch names, commit messages, or file content.

## 2. Quality checks

Run the project's quality gate (lint, format, type-check, tests) via the workspace task:

```
run_task: "Dev: run all checks"
```

Alternatively run each step with `run_in_terminal`:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

If any check fails, fix the issue in the source files and re-run until all pass.

## 3. Changelog

- Update `CHANGELOG.md` under the `## Unreleased` section.
- Follow the existing format: group entries under `### Added`, `### Changed`, `### Fixed`, or `### Removed`.
- Each entry should start with a bold feature/area label and a concise description.

## 4. Documentation

- If the change affects public APIs, CLI commands, plugin contracts, or user-facing features, update the relevant files in `docs/`, `README.md`, or scaffold docs under `docs/plugin-scaffold/`.
- If a new MCP tool was added, update the tool table in `README.md` and `docs/ai/mcp.md`.

## 5. Commit

Compose a commit message following conventional-commit style:

```
<type>(<scope>): <concise summary>

- bullet list of notable changes
- reference issues with #<number>
```

Where `<type>` is one of: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

Create a feature branch (`git checkout -b <branch-name>`) if not already on one, stage all relevant files, and commit.

## 6. Push and open PR

- Push the branch to `origin`.
- Use `mcp_github_create_pull_request` to open a PR against `main`.
- Fill the PR body using the template from `.github/pull_request_template.md`:
  - **Description:** summarize what the PR does.
  - **Related issue:** link issues with `Closes #<number>` when applicable.
  - **Type of change:** check the matching boxes.
  - **Checklist:** mark items that have been verified.
- If breaking changes are included, highlight them in both the commit message and the PR description.
