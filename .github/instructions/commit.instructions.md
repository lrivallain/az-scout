---
description: "Conventional Commits format for az-scout. USE WHEN composing commit messages."
applyTo: "**/COMMIT_EDITMSG"
---

# Commit message conventions

Use [Conventional Commits](https://www.conventionalcommits.org/).

## Format

```
<type>(<scope>): <concise summary>

- bullet list of notable changes (wrap at ~80 chars)
- reference issues with #<number> in the body, never in the subject
```

## Types

| Type | Use for |
|---|---|
| `feat` | A new user-visible capability |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `refactor` | Internal change with no behavior change |
| `test` | Adding or refactoring tests only |
| `chore` | Tooling, dependencies, repo plumbing |
| `release` | Version bump commits (used by `tag-release.prompt.md`) |
| `ci` | CI workflow changes |
| `perf` | Performance improvement |

## Scopes (preferred — use when relevant)

`azure-api`, `auth`, `obo`, `plugins`, `plugin-api`, `planner`, `topology`,
`ui`, `chat`, `mcp`, `scoring`, `cli`, `docs`, `ci`, `deps`, `release`.

For sibling plugin repos, scope is usually the area inside the plugin
(`api`, `ui`, `tools`, `chat-mode`, `tests`).

## Rules

- Subject in **imperative mood**, no trailing period, lowercase after the colon.
- Subject ≤ 72 characters.
- Body explains the **why**, not the **what** (the diff already shows the what).
- Use `BREAKING CHANGE:` footer for incompatible changes — required when bumping `PLUGIN_API_VERSION` major.
- Reference issues with `Closes #N` / `Refs #N` in the body or footer, never in the subject.

## Examples

```
feat(scoring): add knockout for zero available zones

Adds a hard knockout in compute_deployment_confidence when
zones_available_count is 0, forcing label="Blocked".
Refs #42
```

```
fix(ui): escape SKU name in detail modal title

Closes #145
```

```
release: v2026.4.1
```

```
chore(deps): bump cryptography from 43.0.1 to 43.0.3
```
