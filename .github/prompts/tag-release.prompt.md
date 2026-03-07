---
description: Tag a new CalVer release on the main branch — finalize changelog, commit, tag, push, and verify.

tools:
  - run_in_terminal
  - get_changed_files
---

Tag a new release by executing each step below **on the `main` branch**.
Stop and report clearly if any step fails.

## 1. Verify branch

Confirm the current branch is `main` and the working tree is clean:

```bash
git branch --show-current   # must be "main"
git status --short           # must be empty
```

If not on `main`, abort with a clear message — releases are only tagged from `main`.
If the working tree is dirty, list the uncommitted changes and ask for confirmation before proceeding.

## 2. Determine the new version

This project uses [CalVer](https://calver.org/) with the format `YYYY.MM.MICRO`:

- `YYYY` — current four-digit year (e.g. `2026`)
- `MM` — current month as a single or double digit (e.g. `3` for March)
- `MICRO` — patch counter, incremented from the last tag in the same `YYYY.MM` series

Steps:

1. Get the current year and month.
2. List existing tags matching `vYYYY.MM.*` for the current year/month:
   ```bash
   git tag -l "v$(date +%Y).$(date +%-m).*" --sort=-v:refname | head -5
   ```
3. If tags exist for this month, increment `MICRO` by 1.
   If no tags exist for this month, start at `0`.
4. The new version is `YYYY.MM.MICRO` and the tag is `vYYYY.MM.MICRO`.

Present the computed version and ask for confirmation before proceeding.

## 3. Quality checks

Run the full quality gate to ensure the codebase is release-ready:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

All four commands must pass. If any fails, stop and report the failure — do not tag a broken release.

## 4. Finalize the changelog

Edit `CHANGELOG.md`:

1. Read the current `## Unreleased` section.
2. If it is empty or contains only a placeholder (e.g. "To complete."), stop and ask the user to provide changelog entries before proceeding.
3. Insert a new version heading between `## Unreleased` and the previous release:
   ```markdown
   ## Unreleased

   ## [YYYY.MM.MICRO] - YYYY-MM-DD

   ### Added
   - …
   ```
4. Move all entries from `## Unreleased` into the new version section.
5. Leave `## Unreleased` empty (no placeholder text).

## 5. Commit the changelog

```bash
git add CHANGELOG.md
git commit -m "release: vYYYY.MM.MICRO"
```

## 6. Create the tag

```bash
git tag vYYYY.MM.MICRO
```

## 7. Push

Push the commit and the tag together:

```bash
git push origin main --tags
```

## 8. Post-tag verification

Confirm the tag was pushed and the CI workflow was triggered:

```bash
git ls-remote --tags origin | grep "vYYYY.MM.MICRO"
```

Report the final version, tag name, and remind that the **Publish** workflow will:

1. Run CI (lint + tests across Python 3.11–3.13).
2. Validate the tag follows CalVer and the built package version matches.
3. Create a GitHub Release with auto-generated release notes.
4. Publish to PyPI via trusted publishing (OIDC).
