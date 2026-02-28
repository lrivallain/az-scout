# Azure Scout: `az-scout`

[![CI](https://github.com/lrivallain/az-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/lrivallain/az-scout/actions/workflows/ci.yml)
[![Publish to PyPI](https://github.com/lrivallain/az-scout/actions/workflows/publish.yml/badge.svg)](https://github.com/lrivallain/az-scout/actions/workflows/publish.yml)
[![Publish Container Image](https://github.com/lrivallain/az-scout/actions/workflows/container.yml/badge.svg)](https://github.com/lrivallain/az-scout/actions/workflows/container.yml)
[![PyPI version](https://img.shields.io/pypi/v/az-scout)](https://pypi.org/project/az-scout/)
[![Downloads](https://img.shields.io/pypi/dm/az-scout)](https://pypi.org/project/az-scout/)
[![License](https://img.shields.io/github/license/lrivallain/az-scout)](LICENSE.txt)

Scout Azure regions for VM availability, zone mappings, pricing, spot scores, and quota — then plan deployments with confidence.

> **az-scout** helps you compare how Azure maps logical Availability Zones to physical zones across subscriptions, evaluate SKU capacity and pricing, and generate deterministic deployment plans — all from a single web UI or MCP-powered AI agent.


## Features

- **Logical-to-physical zone mapping** – visualise how Azure maps logical Availability Zones to physical zones across subscriptions in a region.
- **SKU availability view** – VM SKU availability per physical zone with vCPU quota usage, numeric filters, and CSV export.
- **Spot Placement Scores** – per-SKU Spot VM allocation likelihood (High / Medium / Low) from the Azure Compute RP.
- **Deployment Confidence Score** – composite 0–100 score per SKU estimating deployment success, synthesised from quota, spot scores, zone breadth, restrictions, and price pressure.
- **Deployment Plan** – deterministic `POST /api/deployment-plan` endpoint returning ranked plans with business and technical views (no LLM).
- **AI Chat Assistant** *(optional)* – chat panel powered by Azure OpenAI with streaming, tool calling, and markdown rendering. Requires Azure OpenAI environment variables.
- **MCP server** – expose all capabilities as [MCP](https://modelcontextprotocol.io/) tools for AI agents.
- **Plugin system** – extend az-scout with pip-installable plugins (API routes, MCP tools, UI tabs, chat modes). See [Known plugins](#known-plugins) below.


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


## Known plugins

az-scout can be extended with pip-installable plugins discovered automatically at startup. See [docs/plugins.md](docs/plugins.md) for the plugin development guide and the [scaffold](docs/plugin-scaffold/) for a ready-to-use template.

| Plugin | Description |
|---|---|
| [az-scout-plugin-strategy-advisor](https://github.com/lrivallain/az-scout-plugin-strategy-advisor) | Multi-region capacity strategy recommendation engine — evaluates regions, quotas, spot scores, pricing, and latency to recommend deployment strategies |
| [az-scout-plugin-batch-sku](https://github.com/lrivallain/az-scout-plugin-batch-sku) | Azure Batch SKU availability — discover and compare Batch-supported VM SKUs per region |

Install a plugin:

```bash
uv pip install <plugin-package-name>
```


## Installation options

### Recommended: install with `uv`

```bash
uv install az-scout
uvx az-scout
```

### Alternative: install with `pip`

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
  ghcr.io/lrivallain/az-scout:latest
```

### Dev Container

A [Dev Container](https://containers.dev/) configuration is included for a one-click development environment. Requires Docker and VS Code with the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.

Open the repo in VS Code → **Reopen in Container** → run `uv run az-scout web --host 0.0.0.0 --port 5001 --reload --no-open -v`.

### Azure Container App

It is also possible to deploy az-scout as a web app in Azure using the provided Bicep template (see [Deploy to Azure](#deploy-to-azure-container-app) section below).

**Note:** The web UI is designed for local use and may **not be suitable for public-facing deployment without additional security measures** (authentication, network restrictions, etc.). The MCP server can be exposed over the public internet if needed, but ensure you have proper authentication and authorization in place to protect access to Azure data.

#### UI guided deployment

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Flrivallain%2Faz-scout%2Fmain%2Fdeploy%2Fmain.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2Flrivallain%2Faz-scout%2Fmain%2Fdeploy%2FcreateUiDefinition.json)

A Bicep template is provided to deploy az-scout as an Azure Container App with a managed identity.
You can use the **Deploy to Azure** button above for a portal-guided experience, or use the CLI commands below.

#### Bicep deploy from CLI

```bash
# Create a resource group
az group create -n rg-az-scout -l <your-region>

# Deploy (replace subscription IDs with your own)
az deployment group create \
  -g rg-az-scout \
  -f deploy/main.bicep \
  -p readerSubscriptionIds='["SUB_ID_1","SUB_ID_2"]'
```

See [`deploy/main.example.bicepparam`](deploy/main.example.bicepparam) for all available parameters.

#### Resources created

The deployment creates:

| Resource | Purpose |
|---|---|
| **Container App** | Runs `ghcr.io/lrivallain/az-scout` |
| **Managed Identity** | `Reader` role on target subscriptions |
| **VM Contributor** | `Virtual Machine Contributor` role for Spot Placement Scores (enabled by default) |
| **Log Analytics** | Container logs and diagnostics |
| **Container Apps Env** | Hosting environment |

> **Note:** The `Virtual Machine Contributor` role is required for querying Spot Placement Scores (POST endpoint). Set `enableSpotScoreRole=false` to skip this if you don't need spot scores or prefer to manage permissions manually.

#### Enable Entra ID authentication (EasyAuth)

For a complete walkthrough (App Registration creation, client secret, user assignment, troubleshooting), see [`deploy/EASYAUTH.md`](deploy/EASYAUTH.md).

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
| `get_sku_availability` | Get VM SKU availability per zone with restrictions, capabilities, and vCPU quota per family |
| `get_spot_scores` | Get Spot Placement Scores (High / Medium / Low) for a list of VM sizes in a region |
| `get_sku_pricing_detail` | Get detailed Linux pricing (PayGo, Spot, RI 1Y/3Y, SP 1Y/3Y) and VM profile for a single SKU |
| `region_latency` | Return indicative RTT latency between two Azure regions (Microsoft published statistics) |

> **Plugin tools:** Plugins can register additional MCP tools. For example, the [Strategy Advisor plugin](https://github.com/lrivallain/az-scout-plugin-strategy-advisor) adds a `capacity_strategy` tool.

`get_sku_availability` supports optional filters to reduce output size:
`name`, `family`, `min_vcpus`, `max_vcpus`, `min_memory_gb`, `max_memory_gb`.

#### stdio transport (default – for Claude Desktop, VS Code, etc.)

```bash
az-scout mcp
```

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "az-scout": {
      "command": "az-scout",
      "args": ["mcp"]
    }
  }
}
```

If using `uv`:

```json
{
  "mcpServers": {
    "az-scout": {
      "command": "uvx",
      "args": ["az-scout", "mcp"]
    }
  }
}
```

#### Streamable HTTP transport

When running in `web` mode, the MCP server is automatically available at `/mcp` for integration with web-based clients or when running as a hosted deployment (Container App, etc.).

For **MCP-only** use with Streamable HTTP transport, run:

```bash
az-scout mcp --http --port 8082
```

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "az-scout": {
      "url": "http://localhost:8082/mcp" // or "https://<your-app-url>/mcp" for web command
    }
  }
}
```

> **Hosted deployment:** When running as a Container App (or any hosted web server), the MCP endpoint is automatically available at `/mcp` alongside the web UI — no separate server needed. Point your MCP client to `https://<your-app-url>/mcp`.
>
> **EasyAuth:** If your Container App has EasyAuth enabled, MCP clients must pass a bearer token in the `Authorization` header. See the [EasyAuth guide](deploy/EASYAUTH.md#7-connect-mcp-clients-through-easyauth) for detailed instructions.

### API

API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the server is running.

## Under the hood

The backend calls the Azure Resource Manager REST API to fetch:
- **Zone mappings**: `availabilityZoneMappings` from `/subscriptions/{id}/locations` endpoint
- **Resource SKUs**: SKU details from `/subscriptions/{id}/providers/Microsoft.Compute/skus` endpoint with zone restrictions and capabilities
- **Compute Usages**: vCPU quota per VM family from `/subscriptions/{id}/providers/Microsoft.Compute/locations/{region}/usages` endpoint (cached for 10 minutes, with retry on throttling and graceful handling of 403)
- **Spot Placement Scores**: likelihood indicators for Spot VM allocation from `/subscriptions/{id}/providers/Microsoft.Compute/locations/{region}/placementScores/spot/generate` endpoint (batched in chunks of 100, sequential execution with retry/back-off, cached for 10 minutes). Note: these scores reflect the probability of obtaining a Spot VM allocation, not datacenter capacity.

## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Spot placement scores are probabilistic. Quota values and pricing are dynamic and may change between planning and actual deployment. Latency values are based on [Microsoft published statistics](https://learn.microsoft.com/en-us/azure/networking/azure-network-latency) and must be validated with in-tenant measurements.
