# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Calendar Versioning](https://calver.org/) (`YYYY.MM.MICRO`).


## [Unreleased]

## [2026.2.3] - 2026-02-16

### Added

- **MCP server** – expose zone mappings and SKU availability as MCP tools for AI agents.
  - `list_tenants` – discover Azure AD tenants and auth status.
  - `list_subscriptions` – list enabled subscriptions.
  - `list_regions` – list AZ-enabled regions.
  - `get_zone_mappings` – query logical-to-physical zone mappings.
  - `get_sku_availability` – query VM SKU availability per zone with filtering
    (by name, family, vCPU range, memory range).
  - Supports stdio and SSE transports via `az-scout mcp` subcommand.
- New `azure_api` module – shared Azure ARM logic used by both the web app and MCP server.
- **Colored logging** – reuses uvicorn's `DefaultFormatter` for consistent colored output.
- **`--reload` CLI flag** – auto-reload on code changes for development (uses uvicorn's watcher).
- OpenAPI documentation available at `/docs` (Swagger UI) and `/redoc`.

### Changed

- **Migrated from Flask to FastAPI** – async routes, built-in request validation,
  automatic OpenAPI schema generation.
- **Unified CLI** – `az-scout web` and `az-scout mcp` subcommands replace the
  separate entry points. Running `az-scout` without a subcommand defaults to
  `web` for backward compatibility. `--verbose` is available on both subcommands;
  `--reload` is specific to `web`.
- Tenant authentication checks now suppress noisy Azure CLI subprocess stderr output
  using an OS-level fd redirect (`_suppress_stderr` context manager).
- Azure SDK logger silenced to `CRITICAL` during auth probes to avoid misleading
  `AADSTS*` error messages for tenants the user is not authenticated to.

### Fixed

- Thread-safety issue where concurrent tenant auth checks could race on stderr
  redirection – fd redirect is now applied once around the entire thread pool batch.

## [2026.2.2] - 2026-02-16

### Added

- **SKU availability table** – view VM SKU availability per physical zone with filtering and CSV export.
- Subscription selector dropdown when multiple subscriptions are selected (for SKU loading).
- Automatic retry with exponential backoff for slow Azure SKU API calls.

### Changed

- SKU table headers now show both logical zone and physical zone (e.g., "Zone 1" / "eastus-az1").
- Improved dark mode contrast for success/warning indicators.
- SKU list auto-resets when region changes or mappings are reloaded.

### Fixed

- Azure API timeout errors now retry automatically (3 attempts, up to 60s per call).

## [2026.2.1] - 2026-02-14

### Added

- **Dark mode** with system preference detection, manual toggle (sun/moon button), and localStorage persistence.
- **Searchable region combobox** with keyboard navigation, auto-select on single match, and click-outside-to-close.
- **Multi-tenant support** with `/api/tenants` endpoint; default tenant auto-detected from JWT token (`tid` claim).
- Favicon (Azure-themed shield).
- Pre-commit configuration (ruff, mypy, trailing-whitespace, end-of-file-fixer, check-yaml/toml).

## [2026.2.0] - 2026-02-13

### Added

- Interactive web UI with Flask backend and D3.js frontend.
- Region selector – auto-loads AZ-enabled regions.
- Subscription picker – searchable, multi-select with select/clear all.
- Graph view – bipartite diagram (Logical Zone → Physical Zone), colour-coded per subscription.
- Interactive hover highlighting (by subscription, logical zone, or physical zone).
- Table view – comparison table with consistency indicators.
- Export – download graph as PNG or table as CSV.
- Collapsible sidebar for the filter panel.
- URL parameter sync – filters are reflected in the URL and restored on reload.
- CLI entry point (`az-scout` / `uvx az-scout`) with `--host`, `--port`, and `--no-open` options.
- Fault-proof automatic browser opening on startup.
- GitHub Actions workflow for publishing to PyPI via trusted publishing.
- GitHub Actions CI workflow (ruff lint + pytest across Python 3.11–3.13).
- Issue templates (bug report, feature request) and PR template.

[Unreleased]: https://github.com/lrivallain/az-scout/compare/v2026.2.3...HEAD
[2026.2.3]: https://github.com/lrivallain/az-scout/compare/v2026.2.2...v2026.2.3
[2026.2.2]: https://github.com/lrivallain/az-scout/compare/v2026.2.1...v2026.2.2
[2026.2.1]: https://github.com/lrivallain/az-scout/compare/v2026.2.0...v2026.2.1
[2026.2.0]: https://github.com/lrivallain/az-scout/releases/tag/v2026.2.0
