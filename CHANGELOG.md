# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Calendar Versioning](https://calver.org/) (`YYYY.MM.MICRO`).

## [Unreleased]

### Added

- Dark mode with system preference detection, manual toggle (sun/moon button), and localStorage persistence.
- Searchable region combobox with keyboard navigation, auto-select on single match, and click-outside-to-close.
- Multi-tenant support with `/api/tenants` endpoint; default tenant auto-detected from JWT token (`tid` claim).
- Concurrent auth probing per tenant – unauthenticated tenants are shown as disabled in the dropdown.
- Tenant selection synced to URL query params (`?tenant=…`).
- Entra ID icon for the tenant filter section.
- Favicon (Azure-themed shield).
- Pre-commit configuration (ruff, mypy, trailing-whitespace, end-of-file-fixer, check-yaml/toml).
- 4 new tests for the tenant endpoint (15 total).


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
- CLI entry point (`az-mapping` / `uvx az-mapping`) with `--host`, `--port`, and `--no-open` options.
- Fault-proof automatic browser opening on startup.
- GitHub Actions workflow for publishing to PyPI via trusted publishing.
- GitHub Actions CI workflow (ruff lint + pytest across Python 3.11–3.13).
- Issue templates (bug report, feature request) and PR template.

[Unreleased]: https://github.com/lrivallain/az-mapping/compare/v2026.2.0...HEAD
[2026.2.0]: https://github.com/lrivallain/az-mapping/releases/tag/v2026.2.0
