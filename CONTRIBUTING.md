# Contributing to az-scout

Thanks for your interest in contributing! Here's how to get started.

## Development setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/lrivallain/az-scout.git
   cd az-scout
   ```

2. **Install [uv](https://docs.astral.sh/uv/)**

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. **Install dependencies (including dev tools)**

   ```bash
   uv sync --group dev
   ```

## Running the app locally

```bash
uv run az-scout web --no-open --reload --verbose
# or
uv run python -m az_scout web --no-open --reload --verbose
```

You need valid Azure credentials (`az login` or any method supported by `DefaultAzureCredential`).

## Code quality

This project uses **ruff** for linting and formatting, and **mypy** for type checking.

```bash
# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Type check
uv run mypy src/
```

A **pre-commit** configuration is provided to run these checks automatically before each commit:

```bash
uv run pre-commit install
```

## Running tests

```bash
uv run pytest
```

## Submitting a pull request

1. Fork the repo and create your branch from `main`.
2. Make your changes and add tests if applicable.
3. Ensure all checks pass: `ruff check`, `ruff format --check`, `mypy`, and `pytest`.
4. Open a pull request — the CI pipeline will run automatically.

## Release process

This project uses [CalVer](https://calver.org/) (`YYYY.MM.MICRO`) and the version is derived from git tags via `hatch-vcs`.

### 1. Update the changelog

Edit `CHANGELOG.md`: move items from **[Unreleased]** into a new version section and update the footer links.

```markdown
## [Unreleased]

## [2026.2.1] - 2026-02-14

### Added
- …

### Changed
- …

### Fixed
- …
```

### 2. Commit and tag

```bash
git add CHANGELOG.md
git commit -m "release: v20YY.M.PP"
git tag v20YY.M.PP
```

### 3. Push the tag

```bash
git push origin main --tags
```

### What happens next

Pushing the tag triggers the **Publish** workflow which will:

1. **Run the full CI** (lint + tests across Python 3.11–3.13).
2. **Validate** the tag follows CalVer format and the built package version matches.
3. **Create a GitHub Release** with auto-generated release notes.
4. **Publish to PyPI** via trusted publishing (OIDC).

## Reporting issues

Please use the [issue templates](https://github.com/lrivallain/az-scout/issues/new/choose) for bug reports and feature requests.
