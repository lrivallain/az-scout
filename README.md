# Azure Scout: `az-scout`

[![CI](https://github.com/az-scout/az-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/az-scout/az-scout/actions/workflows/ci.yml)
[![Publish to PyPI](https://github.com/az-scout/az-scout/actions/workflows/publish.yml/badge.svg)](https://github.com/az-scout/az-scout/actions/workflows/publish.yml)
[![Publish Container Image](https://github.com/az-scout/az-scout/actions/workflows/container.yml/badge.svg)](https://github.com/az-scout/az-scout/actions/workflows/container.yml)
[![PyPI version](https://img.shields.io/pypi/v/az-scout)](https://pypi.org/project/az-scout/)
[![Downloads](https://img.shields.io/pypi/dm/az-scout)](https://pypi.org/project/az-scout/)
[![License](https://img.shields.io/github/license/az-scout/az-scout)](LICENSE.txt)

Scout Azure regions for VM availability, zone mappings, pricing, spot scores, and quota — then plan deployments with confidence.

📖 **Full documentation:** [azscout.vupti.me](https://azscout.vupti.me)

**az-scout** helps Azure operators and architects answer the questions that matter when planning resilient, cost-efficient VM deployments:

- *Do my subscriptions share the same physical datacenter for logical zone 1?*
- *Which VM SKUs are available in all three zones with headroom in my quota?*
- *What is the Spot placement likelihood for this SKU family right now?*
- *Which deployment plan gives me the best confidence score across zones?*

All from a single web UI — or through an MCP-powered AI agent connected to your favourite tools (Claude, VS Code Copilot, etc.).

![az-scout web UI showing zone mappings and SKU availability](docs/assets/screenshots/hero.png)

## Quick start

### Prerequisites

| Requirement | Details |
|---|---|
| Python | ≥ 3.11 |
| Azure credentials | Any method supported by `DefaultAzureCredential` (`az login`, managed identity, …) |
| RBAC | **Reader** on the subscriptions you want to query, **Virtual Machine Contributor** on the subscriptions for Spot Placement Scores retrieval |
| Azure OpenAI *(optional)* | For the AI Chat Assistant: set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, and optionally `AZURE_OPENAI_API_VERSION` |

### Run locally with `uv` tool (recommended)

```bash
# Make sure you are authenticated to Azure
az login

# Run the tool (no install required)
uvx az-scout
```

Your browser opens automatically at `http://127.0.0.1:5001`.


## Plugins

az-scout can be extended with pip-installable plugins discovered automatically at startup. See [docs/PLUGINS.md](docs/PLUGINS.md) for the plugin development guide and the [scaffold](docs/plugin-scaffold/) for a ready-to-use template.

A **Plugin Manager** is included in the UI to view installed plugins and their details.

## Installation options

### Recommended: install az-scout with `uv`

```bash
uv install az-scout
uvx az-scout
```

### Alternative: install az-scout with `pip`

```bash
pip install az-scout
az-scout
```

### Docker

```bash
docker run --rm -p 8000:8000 \
  -e AZURE_TENANT_ID=<your-tenant> \
  -e AZURE_CLIENT_ID=<your-sp-client-id> \
  -e AZURE_CLIENT_SECRET=<your-sp-secret> \
  ghcr.io/az-scout/az-scout:latest
```

### Dev Container

A [Dev Container](https://containers.dev/) configuration is included for a one-click development environment. Requires Docker and VS Code with the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.

Open the repo in VS Code → **Reopen in Container** → run `uv run az-scout web --host 0.0.0.0 --port 5001 --reload --no-open -v`.

### Azure Container App

It is also possible to deploy az-scout as a web app in Azure using the provided Bicep template (see [Deploy to Azure](#deploy-to-azure-container-app) section below).

**Note:** The web UI is designed for local use and may **not be suitable for public-facing deployment without additional security measures** (authentication, network restrictions, etc.). The MCP server can be exposed over the public internet if needed, but ensure you have proper authentication and authorization in place to protect access to Azure data.

#### UI guided deployment

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Faz-scout%2Faz-scout%2Fmain%2Fdeploy%2Fmain.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2Faz-scout%2Faz-scout%2Fmain%2Fdeploy%2FcreateUiDefinition.json)

A Bicep template is provided to deploy az-scout as an Azure Container App with a managed identity.
You can use the **Deploy to Azure** button above for a portal-guided experience, or use the CLI commands below.

#### Resources created

The deployment creates:

| Resource | Purpose |
|---|---|
| **Container App** | Runs `ghcr.io/az-scout/az-scout` |
| **Managed Identity** | `Reader` role on target subscriptions |
| **VM Contributor** | `Virtual Machine Contributor` role for Spot Placement Scores (enabled by default) |
| **Log Analytics** | Container logs and diagnostics |
| **Container Apps Env** | Hosting environment |

> **Note:** The `Virtual Machine Contributor` role is required for querying Spot Placement Scores (POST endpoint). Set `enableSpotScoreRole=false` to skip this if you don't need spot scores or prefer to manage permissions manually.

## Usage

### CLI options

```bash
az-scout [COMMAND] [OPTIONS]

az-scout --help.     # show global help
az-scout web --help  # show web subcommand help
az-scout mcp --help  # show mcp subcommand help
az-scout --version   # show version
```

#### `az-scout web` (default)

Run the web UI. This is the default when no subcommand is given.

```
  --host TEXT     Host to bind to.  [default: 127.0.0.1]
  --port INTEGER  Port to listen on.  [default: 5001]
  --no-open       Don't open the browser automatically.
  -v, --verbose   Enable verbose logging.
  --reload        Auto-reload on code changes (development only).
  --help          Show this message and exit.
```

#### `az-scout mcp`

Run the MCP server.

```
  --http          Use Streamable HTTP transport instead of stdio.
  --port INTEGER  Port for Streamable HTTP transport.  [default: 8080]
  -v, --verbose   Enable verbose logging.
  --help          Show this message and exit.
```

### MCP server

An [MCP](https://modelcontextprotocol.io/) server is included, allowing AI agents (Claude Desktop, VS Code Copilot, etc.) to query zone mappings and SKU availability directly.

#### Available tools

| Tool | Description |
|---|---|
| `list_tenants` | Discover Azure AD tenants and authentication status |
| `list_subscriptions` | List enabled subscriptions (optionally scoped to a tenant) |
| `list_regions` | List regions that support Availability Zones |
| `get_zone_mappings` | Get logical→physical zone mappings for subscriptions in a region |
| `get_sku_availability` | Get VM SKU availability per zone with restrictions, capabilities, quota, and deployment confidence |
| `get_spot_scores` | Get Spot Placement Scores (High / Medium / Low) for a list of VM sizes in a region |
| `get_sku_deployment_confidence` | Compute Deployment Confidence Scores (0–100) for one or more VM SKUs with full signal breakdown |
| `get_sku_pricing_detail` | Get detailed Linux pricing (PayGo, Spot, RI 1Y/3Y, SP 1Y/3Y) and VM profile for a single SKU |

> **Plugin tools:** Plugins can register additional MCP tools. For example, the [Strategy Advisor plugin](https://github.com/az-scout/az-scout-plugin-strategy-advisor) adds a `capacity_strategy` tool.

### API

API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the server is running.

## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Spot placement scores are probabilistic. Quota values and pricing are dynamic and may change between planning and actual deployment. Latency values are based on [Microsoft published statistics](https://learn.microsoft.com/en-us/azure/networking/azure-network-latency) and must be validated with in-tenant measurements.
