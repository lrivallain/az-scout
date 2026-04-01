# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Calendar Versioning](https://calver.org/) (`YYYY.MM.MICRO`).

## Unreleased

## [2026.4.0] - 2026-04-01

### Added

- **`SkuDict` TypedDict** – Formal data contract (`SkuDict`, `SkuCapabilities`, `SkuQuota`, `SkuPricing`, `SkuConfidence`) in `plugin_api.py` documenting the canonical SKU dict shape expected by enrichment functions.
- **`enrich_skus()` pipeline** – New async helper in `azure_api` that runs the full enrichment chain (quotas → prices → spot → confidence) with opt-in steps and correct ordering. Plugins no longer need to reimplement the 4-step enrichment sequence.
- **Confidence scoring re-exports** – `compute_deployment_confidence`, `signals_from_sku`, and `enrich_skus_with_confidence` are now re-exported from `azure_api` for plugin convenience.
- **Core SKU detail route** – `GET /api/sku-detail` combines VM profile, pricing, and deployment confidence into a single response. Shared by all plugins for SKU detail modals.
- **Tab reordering** – Main tabs can be reordered via drag-and-drop. The custom order is persisted in `localStorage` and restored on page load. A grip icon (⠿) appears on hover to indicate draggability. New plugin tabs are appended at the end; stale tabs from uninstalled plugins are silently dropped.

## [2026.3.8] - 2026-03-28

### Added

- **Docs catalog page** – The plugin catalog documentation page now embeds the shared `catalog.html` fragment via an iframe wrapping a standalone Bootstrap page generated at build time by the `on_post_build` hook. System theme detection, toggle button, and the full card-based catalog UI are available inline in the docs.
- **File instructions** – Split `copilot-instructions.md` (390→93 lines) into 5 domain-specific file instructions that load automatically when editing relevant files: `azure-api`, `obo-auth`, `frontend`, `plugin-dev`, `plugin-scaffold`.
- **`create-plugin` skill** – Interactive skill (`/create-plugin`) that guides plugin scaffold generation, conventions, and quality checks.
- **Auth guard on API routes** – `require_auth` FastAPI dependency enforces OBO authentication on discovery, chat, AI completion, and all plugin API routes. Unauthenticated requests return 401 when OBO is enabled; in non-OBO mode the guard is a no-op.
- **Native extension detection** – Plugin install/update routes detect newly installed compiled extensions (`.so`, `.pyd`, `.dylib`) and return `restart_required: true` in the API response, prompting users to restart the instance.
- **Shared catalog UI** – New `catalog.html` fragment renders plugin cards from `catalog.json` with filter, tags, authors, PyPI badges, long description truncation, and a 3-tab install modal (Plugin Manager / uv / pip). Used by both the Plugin Manager and future docs/standalone catalog pages.
- **Plugin Manager redesign** – Switched from offcanvas to a responsive modal. Catalog cards rendered from shared `catalog.html` are progressively enhanced with install/update/uninstall buttons. Built-in, external, and dependency plugins are detected and displayed with appropriate badges and actions.
- **Built-in plugin metadata** – Internal plugins (topology, planner) now have `display_name` and `description` attributes exposed in the plugin API.
- **Dependency plugin management** – Plugins installed as dependencies into the packages directory (e.g. via another plugin) are now detected and manageable (uninstall) from the UI instead of being labeled "external".
- **Restart banner** – Plugin Manager shows a warning banner when a plugin with native extensions is installed, indicating a container restart is required.
- **Global status bar** – Plugin Manager shows an inline status bar with spinner during install/uninstall/update operations.
- **Auto update check** – Plugin Manager silently checks for updates when opened, showing results immediately.
- **CSP: shields.io** – Added `img.shields.io` to the Content-Security-Policy `img-src` directive for PyPI version badges.

### Fixed

- **Plugin core version guard** – Plugin Manager now validates that a plugin's `az-scout` version requirement is compatible with the running instance before install. Incompatible plugins are blocked with a clear error. A pip constraint file also prevents pip from installing a different core version into the packages directory.

### Changed

- **Plugin Manager layout** – Replaced table-based views with a card grid layout matching the catalog style. All plugins (catalog, installed, built-in, external) shown in a unified card grid with a filter, action bar, and manual install card.
- **Modal sizing** – Plugin Manager modal uses `modal-xl` with `max-width: min(95vw, 1400px)` for responsive width.
- **Plugin list API** – `/api/plugins` response now includes `display_name`, `description`, `in_packages_dir` fields for loaded plugins to support the enhanced UI.

### Removed

- **Duplicate prompts** – Removed `add-plugin-to-catalog.prompt.md` and `review-plugin.prompt.md` from the core repo. These now live canonically in the [plugin-catalog](https://github.com/az-scout/plugin-catalog) repo.
- **Separate catalog table** – Removed the old table-based catalog/installed views in the Plugin Manager in favour of the shared card-based catalog UI.

## [2026.3.7] - 2026-03-26

### Fixed

- **Plugin uninstall in ACA** – `uv pip uninstall` now appends `--system` when running outside a virtual environment (e.g. in Azure Container Apps), fixing `No virtual environment found` errors (#115).
- **Plugin compatibility** – Plugin protocol check now uses a lenient attribute check instead of `isinstance(obj, AzScoutPlugin)`. Plugins missing newer optional methods (e.g. `get_navbar_actions`) load correctly again (#116).
- **Broken versioning** – Removed non-CalVer tag (`obo-single-tenant-v1`) that caused `hatch-vcs` to produce `2.dev4` instead of `2026.3.x.devN` (#117).
- **Chat tables overflow** – Tables in chat bubbles now scroll horizontally instead of overflowing outside the panel.

### Added

- **On-Behalf-Of (OBO) authentication** – Multi-user mode where each user signs in with their Microsoft account and az-scout accesses Azure ARM APIs with their RBAC permissions instead of the app's managed identity. Enabled via `AZ_SCOUT_CLIENT_ID`, `AZ_SCOUT_CLIENT_SECRET`, and `AZ_SCOUT_TENANT_ID` environment variables.
- **Server-side auth flow** – OAuth 2.0 authorization code flow with signed HTTP-only session cookies. Login page with two options: sign in with your account (organizations authority) or target a specific tenant (domain/ID input).
- **Single-tenant-per-session model** – Each session is scoped to the tenant the user authenticated against. OBO always uses the login tenant, eliminating cross-tenant failures. To switch tenants, sign out and sign in with a different tenant.
- **OBO validation at login** – OBO exchange is validated during login before creating the session. Consent, MFA, and other errors are shown on the login page with actionable messages (Grant Admin Consent button, Copy link, etc.) — the main app never loads with invalid auth.
- **Login page** – Dedicated sign-in page with side-by-side cards for account login and tenant-specific login, error alerts for all auth failure types, admin login button in navbar.
- **Role-based access control** – Entra ID App Roles (`Admin`) enforced server-side. Plugin management restricted to home-tenant admins. Non-admins see a read-only UI via `admin-only` CSS class.
- **Auth context middleware** – Raw ASGI middleware propagates user tokens to all routes (including plugins and MCP tools) via module globals and context vars.
- **Sentinel-based OBO guard** – `_NO_TOKEN` sentinel prevents web requests from falling through to `DefaultAzureCredential` when OBO is enabled.
- **MCP auth via Bearer token** – VS Code MCP clients authenticate via `Authorization` header.
- **Retail Prices retry** – Connection errors on the Azure Retail Prices API are now retried with exponential backoff.
- **Biome JS lint** – Added to pre-commit hooks and ship-code-change prompt.
- **Non-streaming AI completion endpoint** – `POST /api/ai/complete` runs the full tool-calling loop server-side and returns a single JSON response. Plugins can use `plugin_ai_complete()` (Python) or `aiComplete()` (JS) for inline AI recommendations outside the chat panel.
- **AI completion caching** – Results are cached in-memory with a configurable TTL (default 5 min, max 128 entries). Plugins can set `cache_ttl=0` to bypass the cache.
- **`is_ai_enabled()` / `aiEnabled`** – Plugin helpers (Python and JS) to check if AI capabilities are configured.
- **`renderMarkdown()` global** – Shared `marked.js` v15 renderer available to all plugins for rendering AI output as HTML.
- **Chat markdown via marked.js** – Chat `_renderMarkdown()` now uses marked.js with custom extensions for `[[…]]` clickable chips, compact chip lists, and styled tables/headings. Replaces the old regex-based parser.
- **Chat pin/open persistence** – Chat panel pinned state and open/closed state are saved to localStorage and restored on reload.
- **Chat h1 rendering** – Markdown `# heading` (h1) is now rendered in chat bubbles.
- **Clickable choices for all chat modes** – The `[[option]]` formatting instruction is now appended to all chat modes (including plugin-contributed modes), so LLM responses consistently use clickable chips for selectable options.
- **Chat history for plugin modes** – `_restoreChatHistory` now restores all saved modes (including plugin-contributed ones), not just `discussion` and `planner`.

### Changed

- **Plugin error handling** – `PluginError` exceptions caused by `OboTokenError` return 401 (not 502) and suppress stacktraces.
- **Remote plugin catalog** – Plugin Manager now fetches from `plugin-catalog.az-scout.com` instead of the embedded JSON file (#107).
- **Persistent plugin packages** – plugin packages now install to `~/.local/share/az-scout/packages/` (persistent) instead of `/tmp/` (lost on reboot). Containers use `AZ_SCOUT_PACKAGES_DIR=/tmp/az-scout-packages` to preserve SMB compatibility.
- **Dynamic docs catalog** – plugin catalog page now renders dynamically from the remote catalog with live PyPI version badges, author avatars, and tags.

### Removed

- **Embedded `recommended_plugins.json`** – replaced by the remote catalog at [az-scout/plugin-catalog](https://github.com/az-scout/plugin-catalog).

## [2026.3.6] - 2026-03-12

### Added

- **Interactive CLI chat** (`az-scout chat`) – terminal-based AI chat with Rich-rendered markdown responses, tool call panels with spinners, `[[choice]]` patterns as numbered options, and conversation history with Up/Down navigation. Supports one-shot queries (`az-scout chat "question"`) and interactive sessions (#103).
- **Slash commands** – `/help`, `/context`, `/tenant`, `/subscription`, `/region`, `/mode`, `/tenants`, `/subscriptions`, `/regions`, `/clear`, `/new`, `/exit` with Tab auto-completion for commands and arguments (mode names, region names, tenant/subscription names).
- **New dependency** – `prompt-toolkit>=3.0` for interactive terminal input with completion and history.
- **ODCR Coverage plugin** – added `az-scout-plugin-odcr-coverage` to the plugin catalog and recommended plugins.

### Changed

- **Domain migration** – updated documentation site URL from `azscout.vupti.me` to `docs.az-scout.com`.
- **ARM token caching** – tokens are now cached per-tenant with thread-safe locking, eliminating redundant `credential.get_token()` calls and debug log spam during concurrent requests.
- **ARM request timing** – all ARM HTTP calls now log elapsed time at DEBUG level (`ARM GET … → 200 (0.34s)`) for performance troubleshooting.
- **Plugin naming convention** – updated docs and scaffold generator to recommend `az-scout-plugin-{name}` package naming (matching all existing plugins).

## [2026.3.5] - 2026-03-10

### Changed

- **GitHub organization migration** – updated all repository URLs, GHCR container image paths, Deploy to Azure button URIs, and OCI labels from `lrivallain/az-scout` to `az-scout/az-scout` across source, docs, deploy templates, CI workflows, and configuration files.

## [2026.3.4] - 2026-03-08

### Added

- **Public ARM helpers** – new `arm_get()`, `arm_post()`, and `arm_paginate()` functions in `azure_api` provide authenticated ARM calls with built-in 429/5xx retry, exponential backoff, `Retry-After` header support, and structured error handling (`ArmAuthorizationError`, `ArmNotFoundError`, `ArmRequestError`). These are the recommended way for plugins and core modules to interact with Azure Resource Manager (#97).
- **`get_headers()` public alias** – promoted from internal `_get_headers()` for plugins that need raw Bearer-token headers for non-ARM endpoints (#95).
- **`PLUGIN_API_VERSION` bumped to `1.1`** – additive change, backward compatible.
- **Plugin error boundary** – `PluginError`, `PluginValidationError`, and `PluginUpstreamError` exception classes in `plugin_api` with a global handler that returns consistent `{"error", "detail"}` JSON responses. Plugins can raise typed exceptions from route handlers instead of manual try/except + JSONResponse (#98).
- **Copilot prompts** – added `triage-issue`, `review-plugin`, `add-plugin-to-catalog`, `tag-release`, and `ship-code-change` reusable prompt files for coding agents.

### Changed

- **Internal modules migrated to ARM helpers** – `discovery.py`, `skus.py`, `quotas.py`, and `spot.py` now use `arm_get`/`arm_post`/`arm_paginate` instead of raw `requests.get`/`requests.post` + manual retry loops. This eliminates duplicated retry/backoff/error handling code across 4 modules.

### Fixed

- **`apiFetch`/`apiPost` error messages** – now reads both `body.error` and `body.detail` keys so FastAPI's standard `HTTPException` pattern works for all plugins without workarounds (#96).
- **Biome JS lint errors** – fixed optional chaining, `Number.isNaN`, `useConst`, arrow functions, and redundant `use strict` across all JS files.

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
  [`az-scout-plugin-latency-stats`](https://github.com/az-scout/az-scout-plugin-latency-stats).
- **Strategy Advisor extracted to plugin** – the Capacity Strategy Advisor is no longer bundled in
  the core application. It is now available as a standalone plugin:
  [`az-scout-plugin-strategy-advisor`](https://github.com/az-scout/az-scout-plugin-strategy-advisor).
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
