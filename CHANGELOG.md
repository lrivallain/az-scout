# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Calendar Versioning](https://calver.org/) (`YYYY.MM.MICRO`).

## Unreleased

### Added

- **Public ARM helpers** – new `arm_get()`, `arm_post()`, and `arm_paginate()` functions in `azure_api` provide authenticated ARM calls with built-in 429/5xx retry, exponential backoff, `Retry-After` header support, and structured error handling (`ArmAuthorizationError`, `ArmNotFoundError`, `ArmRequestError`). These are the recommended way for plugins and core modules to interact with Azure Resource Manager (#97).
- **`get_headers()` public alias** – promoted from internal `_get_headers()` for plugins that need raw Bearer-token headers for non-ARM endpoints (#95).
- **`PLUGIN_API_VERSION` bumped to `1.1`** – additive change, backward compatible.
- **Copilot prompts** – added `triage-issue`, `review-plugin`, `add-plugin-to-catalog`, and `tag-release` reusable prompt files for coding agents.

### Changed

- **Internal modules migrated to ARM helpers** – `discovery.py`, `skus.py`, `quotas.py`, and `spot.py` now use `arm_get`/`arm_post`/`arm_paginate` instead of raw `requests.get`/`requests.post` + manual retry loops. This eliminates duplicated retry/backoff/error handling code across 4 modules.

## [2026.3.3] - 2026-03-06

### Added

- **Plugin creation** - `az-scout create-plugin` scaffolds a new plugin project with a Rich-powered interactive CLI experience (prompts + generation summary) and supports non-interactive usage for automation.
- **Plugin system prompt addendum hook** – plugins can now contribute extra guidance to the default AI chat `discussion` system prompt via an optional `get_system_prompt_addendum()` capability. This enables domain-specific disambiguation (for example, interpreting `AV*` asks as AVS context) without requiring a dedicated chat mode (#93).
- **Frontend context events for plugin authors** – core UI now emits event-driven context updates for plugin scripts:
  - `azscout:tenants-loaded`
  - `azscout:tenant-changed`
  - `azscout:regions-loaded`
  - `azscout:region-changed`
  - `azscout:subscriptions-loaded`

### Changed

- **Plugin documentation simplified to event-first context integration** – removed legacy `MutationObserver`/direct DOM-watch guidance from plugin docs in favor of the new `azscout:*` event model.
- **Plugin scaffold JS template updated** – scaffold tab script now listens to `azscout:tenant-changed` and `azscout:region-changed`, and includes an empty `azscout:subscriptions-loaded` listener as an extension point for plugin authors.

### Removed

- **Deployment plan generation** – the `/api/deployment-plan` endpoint and related logic for generating deployment plans based on SKU availability and confidence scores has been removed.

## [2026.3.2] - 2026-03-05

### Added

- **Documentation** - Add `mkdocs` docs based websites, hosted in GitHub Pages, with a custom theme and structure. Initial content includes:
  - Home page with project overview and quick start guide.
  - Detailed API reference generated from FastAPI's OpenAPI schema.
  - Plugin development guide with architecture overview, API contract, and scaffold reference.
  - Scoring methodology documentation explaining the confidence score components and rationale...
- **Internal plugin architecture** – core features (AZ Topology, Deployment Planner) are now
  structured as internal plugins using the same `AzScoutPlugin` protocol as external plugins.
  Internal plugins ship inside the core package and are discovered automatically at startup.
  Routes mount at `/api` (backward-compatible URLs), static assets at `/internal/{name}/static`,
  and tabs render dynamically via the Jinja2 template (#76).
- **`azure_api` stable plugin API surface** – added `PLUGIN_API_VERSION = "1.0"` and `__all__`
  to `az_scout.azure_api`, formally declaring the 20 public functions/constants that plugins
  can rely on. Internal helpers are still accessible but excluded from the stability guarantee.
- **Planner chat mode as `ChatMode`** – the Planner system prompt (previously hardcoded in
  `ai_chat.py`) is now a `ChatMode` contributed by the planner internal plugin, discovered
  dynamically via `get_plugin_chat_modes()`.
- **Plugin catalog (recommendations)** – the Plugin Manager now shows a curated list of
  recommended plugins loaded from `recommended_plugins.json`, with quick-install buttons and
  installed status badges. New `GET /api/plugins/recommended` endpoint and
  `load_recommended_plugins()` helper in `plugin_manager`.
- **Built-in badge in Plugin Manager** – loaded plugins list now shows a "built-in" badge
  for internal plugins (topology, planner). The `GET /api/plugins` response includes an
  `internal` field.
- **Content-Security-Policy headers** – all HTML responses now include a CSP header restricting
  scripts, styles, and fonts to `'self'` + CDN origins (`cdn.jsdelivr.net`, `d3js.org`),
  with `frame-ancestors 'none'`.
- **Biome JS linting** – added [Biome](https://biomejs.dev/) as a JavaScript linter (standalone
  binary, no npm). Config in `biome.json`, integrated into CI via `biomejs/setup-biome@v2`.
- **Strict mypy compliance** – `mypy --strict` now passes with 0 errors (was 67). Added explicit
  type parameters across `azure_api/`, scoring, and planner modules.
- **`enrich_skus_with_confidence()` helper** – DRY convenience wrapper in
  `scoring/deployment_confidence.py` replacing 3 duplicate signal-compute-assign loops.
- **Plugin developer documentation** – `docs/plugins.md` updated with internal plugin
  architecture, `PLUGIN_API_VERSION` contract, `azure_api.__all__` reference table, and shared
  module catalogue for plugin authors.

### Changed

- **AZ Topology extracted to internal plugin** – the `/api/mappings` route, `get_zone_mappings`
  MCP tool, tab HTML, and `az-mapping.js` moved from core to
  `internal_plugins/topology/`. The tab markup is loaded as an HTML fragment at runtime.
- **Deployment Planner extracted to internal plugin** – 5 API routes (`/api/skus`,
  `/api/deployment-confidence`, `/api/spot-scores`, `/api/sku-pricing`),
  4 MCP tools, the planner tab HTML + modals (spot, pricing), and `planner.js` moved to
  `internal_plugins/planner/`. All API URLs are preserved.
- **`app.py` slimmed to bootstrap-only** – discovery routes extracted to `routes/discovery.py`,
  logging setup to `logging_config.py`. Core `app.py` now 220 lines (was 710).
- **`plugin_manager.py` split into package** – 1 434-line monolith replaced by 7-module
  `plugin_manager/` package (`_models`, `_github`, `_pypi`, `_installer`, `_storage`,
  `_operations`, `__init__`).
- **`ai_chat.py` split into package** – 741-line monolith replaced by 6-module
  `services/ai_chat/` package (`_config`, `_prompts`, `_tools`, `_dispatch`, `_stream`,
  `__init__`).
- **`mcp_server.py` slimmed down** – removed ~310 lines of tool definitions that now live
  in internal plugins. Core only retains discovery tools (tenants, subscriptions, regions).
- **Deployment Confidence Scoring** – reworked scoring signals for better accuracy (#36):
  - Replaced `quota` with **Quota Pressure** (`quotaPressure`) – non-linear bands that penalise
    heavily above 80% family usage and when remaining vCPUs cannot fit the requested instance count.
  - Replaced `restrictions` with **Restriction Density** (`restrictionDensity`) – per-zone
    granularity instead of a binary flag, so partial restrictions reduce the score proportionally.
  - Added **knockout layer** – hard blockers (zero quota headroom, zero available zones) force
    score = 0, label = "Blocked", `scoreType = "blocked"` with explicit `knockoutReasons`.
  - `instanceCount` parameter now affects quota pressure calculation (demand-adjusted).
- **Frontend JS split** – monolithic `app.js` (3 000+ lines) split into four domain-specific files:
  `app.js` (core), `az-mapping.js` (topology), `planner.js` (deployment planner), `chat.js`
  (AI chat panel).
- **Spot Placement Score cache TTL** increased from 10 minutes to 1 hour to reduce pressure on the
  rate-limited Azure API.
- **Chat UI** - Tool call badges (e.g. "list subscriptions", "get zone mappings") now remain visible
  after the tool finishes executing, and clicking a completed badge opens a detail modal showing the full
  input arguments and output content with JSON syntax highlighting. (#74)


## [2026.3.1] - 2026-03-02

### Added

- **PyPI as plugin source** – the Plugin Manager now supports installing plugins from PyPI in
  addition to GitHub repos. Version auto-resolves to the latest release when not specified (#70).
- **In-process hot-reload for plugins** – installing, updating, or uninstalling a plugin no longer
  requires a server restart. Routes, MCP tools, static assets, and chat modes are reloaded
  automatically (#69).
- **Diagnostic logging for `azure_api`** – debug-level log statements across auth, pagination,
  discovery, SKUs, pricing, quotas, and spot modules for easier troubleshooting (#68).
- **Unified logging format** – all log output (core, plugins, uvicorn, MCP, httpx) now uses a
  single coloured format with a `[category]` tag: `[core]`, `[plugin:<name>]`, `[server]`,
  `[mcp]`, `[http]`. Added `get_plugin_logger()` helper in `plugin_api` for plugin authors (#72).
- **Playwright E2E test suite** – browser-based integration tests for topology, planner, and
  bootstrap workflows (#51).
- **Plugin Manager: update support** – installed plugins can be updated to a newer version
  from the UI (#54).
- **`/api/locations` endpoint** – list Azure locations for a subscription (#55).

### Removed

- **Admission Intelligence** – removed the experimental composite heuristic scoring system
  (6 signals, SQLite time-series store, ~2,700 lines). The feature was never production-validated
  and added significant complexity without delivering reliable results (#66).

### Fixed

- **Plugin persistence in containers** – plugins installed via the Plugin Manager now survive
  container restarts and scale-to-zero events (#65).
- **Plugin install 500 error** – fixed crash when `uv` is absent in container environments (#63).
- **Plugin manager PermissionError** – fixed file permission issues in container mode (#59).
- **Verbose logging (`-v`) actually works** – fixed bug where `--reload` mode ignored the verbose
  flag (env var `AZ_SCOUT_LOG_LEVEL` now propagates to reload workers). Uvicorn `log_level` is
  now `debug` (was `info`) when verbose (#72).
- **Version scheme for container builds** – switched from `calver-by-date` to `guess-next-dev` to
  support the `YYYY.M.MICRO` tag format (#72).

### Changed

- **Latency Stats extracted to plugin** – the inter-region latency dataset and MCP tool are no longer
  bundled in the core application. They are now available as a standalone plugin:
  [`az-scout-plugin-latency-stats`](https://github.com/lrivallain/az-scout-plugin-latency-stats).
- **Strategy Advisor extracted to plugin** – the Capacity Strategy Advisor is no longer bundled in
  the core application. It is now available as a standalone plugin:
  [`az-scout-plugin-strategy-advisor`](https://github.com/lrivallain/az-scout-plugin-strategy-advisor).
- Use calver in the plugin scaffold structure.
- Plugin Manager UI: validate/install/uninstall from GitHub repos (#50).

## [2026.2.8] - 2026-02-28

### Added

- **AI Chat Assistant** – interactive chat panel powered by Azure OpenAI with streaming responses,
  tool calling (zones, SKUs, pricing, spot scores), markdown/table rendering, and clickable choice chips.
  - Pin-to-side mode docks the chat as a resizable sidebar.
  - Tenant and region context auto-injected into tool calls; `switch_tenant`/`switch_region` tools update the UI.
  - Conversation persistence, input history (Up/Down arrows), error retry, and suggested prompts on start.
- **Planner chat mode** – pill-style toggle in the chat panel header switches between *Assistant* (general Q&A)
  and *Planner* (guided deployment advisor). The planner follows three independent planning paths
  (region selection, SKU selection, zone selection) and relies on the model's built-in knowledge of
  Azure VM families and best practices alongside live tool data.
  - Per-mode conversation state (messages, input history) persisted independently to `localStorage`.
- Numeric operator filters on SKU table columns (`>`, `>=`, `<`, `<=`, `=`, ranges).
- **Plugin system** – extend az-scout with pip-installable plugins discovered via Python entry points.
  Plugins can contribute API routes, MCP tools, UI tabs, static assets, and AI chat modes.
  See [docs/plugins.md](docs/plugins.md) and the [scaffold](docs/plugin-scaffold/).

### Fixed

- Graph text overflow: subscription names exceeding their box due to font-weight mismatch in `measureText()`.
- MCP→OpenAI schema converter: `items` not propagated from `anyOf` branches for `list[str] | None` parameters.
- Unauthenticated tenants now hidden from the dropdown selector with a disabled hint option.

## [2026.2.7] - 2026-02-20

### Changed

- Replacement of SSE transport with Streamable HTTP in the MCP server for broader compatibility (e.g., Azure Container Apps, GitHub Codespaces).
- MCP server available at `/mcp` for integration with web-based clients or when running as a hosted deployment (Container App, etc.).

## [2026.2.6] - 2026-02-20

### Added

- Adds **Container App deployment** with optional Entra ID authentication (EasyAuth), a one-click "Deploy to Azure" portal experience, GHCR container CI, and supporting documentation.
  - Dockerfile + GHCR publish workflow
    - Bicep template with managed identity, Reader + VM Contributor roles, optional EasyAuth
    - Custom portal form (createUiDefinition.json) with multi-select subscription picker
    - EASYAUTH.md: full setup guide
  - Pre-commit hook to keep ARM JSON in sync with Bicep
  - EasyAuth user info in navbar
  - Single-tenant UI polish

## [2026.2.5] - 2026-02-19

### Changed

- Project renamed from `az-mapping` to `az-scout` – package, CLI entry point, imports, documentation, and CI/CD all updated. PyPI package is now `az-scout`.

## [2026.2.4] - 2026-02-19

### Added

- **Deployment Confidence Score** – composite 0–100 score per SKU estimating deployment success,
  synthesised from quota headroom, Spot Placement Score, zone breadth, restrictions, and price
  pressure. Missing signals are excluded with automatic weight renormalisation.
- **Spot Placement Scores** – per-SKU Spot VM allocation likelihood (High / Medium / Low),
  fetched from the Azure Compute RP with batching, retry/back-off, and 10-minute cache.
- **SKU pricing** – retail prices (PayGo, Spot, RI 1Y/3Y, SP 1Y/3Y) with currency selector,
  spot discount badge, and pricing detail modal with VM profile section.
- **Region summary bar** – readiness and consistency scores at the top of results.
- **Tenant preload** – background thread warms the tenant cache at startup (5-minute TTL)
  for faster first page load.
- **Version display** – package version shown in the API (OpenAPI spec) and web page footer.
- **Column toggles** – show/hide Prices and Spot columns with `localStorage` persistence.
- MCP tools: `get_sku_pricing_detail`, `get_spot_scores`, confidence score and VM profile.

### Changed

- **Project renamed** from `az-mapping` to `az-scout` – package, CLI entry point, imports,
  documentation, and CI/CD all updated. PyPI package is now `az-scout`.
- **Bootstrap 5 rewrite** – migrated from vanilla CSS to Bootstrap 5.3 with Simple-DataTables,
  per-column filters, dark/light theme toggle, and responsive modal (fullscreen on mobile).
- **Two-page layout** – UI split into Topology and Planner tabs with hash routing.
- SKU table headers show zone availability icons instead of plain text.
- CalVer versioning simplified: `calver-by-date` scheme, no local version suffix.

### Fixed

- Pricing modal now scrollable when content overflows.
- Spot score calculation uses per-zone averaging (not best-zone-only).
- Price Pressure signal computed from modal pricing data even without pre-fetched prices.
- Test warnings from preload daemon thread suppressed by mocking in fixtures.

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
