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

- **Logical-to-physical zone mapping** – visualise how Azure maps logical Availability Zones (Zone 1, Zone 2, Zone 3) to physical zones (e.g., eastus-az1, eastus-az2) across subscriptions in a region.
- **SKU availability view** – shows VM SKU availability per physical zone with vCPU quota usage (limit / used / remaining), numeric operator filters, and CSV export.
- **Spot Placement Scores** – evaluate the likelihood of Spot VM allocation (High / Medium / Low) per SKU for a given region and instance count, powered by the Azure Compute RP.
- **Deployment Confidence Score** – a composite 0–100 score per SKU estimating deployment success probability, synthesised from quota headroom, Spot Placement Score, availability zone breadth, restrictions, and price pressure signals. Missing signals are automatically excluded with weight renormalisation. The score updates live when Spot Placement Scores arrive.
- **Deployment Plan** – agent-ready `POST /api/deployment-plan` endpoint that evaluates (region, SKU) combinations against zones, quotas, spot scores, pricing, and restrictions. Returns a deterministic, ranked plan with business and technical views (no LLM, no invention — missing data is flagged explicitly).
- **Capacity Strategy Advisor** – a multi-region strategy recommendation engine that goes beyond single-region planning. Given a workload profile (instances, constraints, statefulness, latency sensitivity, budget), it evaluates candidate regions against zones, quotas, restrictions, spot scores, pricing, confidence and inter-region latency to recommend a deployment strategy: `single_region`, `active_active`, `active_passive`, `sharded_multi_region`, `burst_overflow`, `time_window_deploy`, or `progressive_ramp`. Includes business justification, technical allocations, latency matrix, and warnings. No LLM — all decisions are deterministic and traceable.
- **AI Chat Assistant** *(optional)* – interactive chat panel powered by Azure OpenAI with streaming responses, tool calling (zones, SKUs, pricing, spot scores), and markdown rendering. Supports pin-to-side mode, conversation persistence, input history, clickable choice chips, and error retry. Requires Azure OpenAI environment variables (see below).
- **MCP server** – expose all capabilities as MCP tools for AI agents (see below).


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

The repository includes a [Dev Container](https://containers.dev/) configuration for a one-click development environment with all tools pre-installed.

#### Prerequisites

- [Docker](https://www.docker.com/) running locally
- [VS Code](https://code.visualstudio.com/) with the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension

#### Getting started

1. Clone the repository and open it in VS Code.
2. When prompted, click **"Reopen in Container"** — or run the command **Dev Containers: Reopen in Container** from the Command Palette (`Ctrl+Shift+P`).
3. Wait for the container to build and dependencies to install (first time only).
4. Start the server:

   ```bash
   # Via VS Code task (Terminal → Run Task → "Backend: run")
   # Or from the terminal:
   uv run az-scout web --host 0.0.0.0 --port 5001 --reload --no-open -v
   ```

5. Open http://localhost:5001 in your browser.

#### What's included

| Category | Details |
|---|---|
| **Python** | 3.12 + `uv` package manager |
| **System tools** | git, curl, jq, make, unzip, ripgrep |
| **Azure CLI** | Pre-installed via devcontainer feature |
| **VS Code extensions** | Python, Pylance, Ruff, Docker, Azure, GitLens, Copilot |
| **Tasks** | `Backend: run`, `Backend: test`, `Backend: lint`, `Dev: run all checks` |

> **Note:** Azure authentication is **not required** to build or run tests. Use `az login` inside the container when you need to query live Azure data.

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
| `capacity_strategy` | Compute a deterministic multi-region deployment strategy based on capacity signals and latency |
| `region_latency` | Return indicative RTT latency between two Azure regions (Microsoft published statistics) |

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

### Deployment Plan API

The `POST /api/deployment-plan` endpoint provides a deterministic decision engine for deployment planning. It is designed for Sales / Solution Engineers and AI agents: no LLM is involved — every decision traces back to real Azure data.

#### Request

```json
{
  "subscriptionId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "regionConstraints": {
    "allowRegions": ["francecentral", "westeurope"],
    "dataResidency": "EU"
  },
  "skuConstraints": {
    "preferredSkus": ["Standard_D2s_v3", "Standard_E8s_v4"],
    "requireZonal": true
  },
  "scale": { "instanceCount": 4 },
  "pricing": {
    "currencyCode": "EUR",
    "preferSpot": true,
    "maxHourlyBudget": 2.0
  },
  "timing": { "urgency": "now" }
}
```

#### Response (abbreviated)

```json
{
  "summary": {
    "recommendedRegion": "francecentral",
    "recommendedSku": "Standard_D2s_v3",
    "recommendedMode": "zonal",
    "riskLevel": "low",
    "confidenceScore": 85
  },
  "businessView": {
    "keyMessage": "Standard_D2s_v3 in francecentral is recommended ...",
    "reasons": ["Available in 3 availability zone(s).", "Sufficient quota ..."],
    "risks": [],
    "mitigations": [],
    "alternatives": [{ "region": "westeurope", "sku": "Standard_E8s_v4", "reason": "..." }]
  },
  "technicalView": {
    "evaluation": { "regionsEvaluated": ["francecentral", "westeurope"], "perRegionResults": [] },
    "dataProvenance": { "evaluatedAt": "...", "cacheTtl": {}, "apiVersions": {} }
  },
  "warnings": ["Spot placement score is probabilistic and not a guarantee."],
  "errors": []
}
```

> **Note:** Spot placement scores are probabilistic and not a guarantee of allocation. Quota values are dynamic and may change between planning and actual deployment.

### Capacity Strategy Advisor API

The `POST /api/capacity-strategy` endpoint is a multi-region strategy recommendation engine. It evaluates candidate (region, SKU) combinations against zones, quotas, restrictions, spot scores, pricing, deployment confidence, and inter-region latency to recommend a deployment strategy. No LLM — every decision is deterministic and traceable.

#### Latency data source

Inter-region RTT values come from [Microsoft published network latency statistics](https://learn.microsoft.com/en-us/azure/networking/azure-network-latency). These are indicative and must be validated with in-tenant measurements (e.g. Azure Connection Monitor).

#### Request

```json
{
  "workloadName": "inference-cluster",
  "subscriptionId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "scale": {
    "sku": "Standard_NC24ads_A100_v4",
    "instanceCount": 48,
    "gpuCountTotal": 48
  },
  "constraints": {
    "dataResidency": "EU",
    "requireZonal": true,
    "maxInterRegionRttMs": 50
  },
  "usage": {
    "statefulness": "stateless",
    "crossRegionTraffic": "medium",
    "latencySensitivity": "high"
  },
  "pricing": {
    "currencyCode": "EUR",
    "preferSpot": true,
    "maxHourlyBudget": 100.0
  },
  "timing": {
    "deploymentUrgency": "this_week"
  }
}
```

#### Response (abbreviated)

```json
{
  "summary": {
    "workloadName": "inference-cluster",
    "strategy": "sharded_multi_region",
    "totalInstances": 48,
    "regionCount": 3,
    "estimatedHourlyCost": 82.56,
    "currency": "EUR",
    "overallConfidence": 72,
    "overallConfidenceLabel": "Medium"
  },
  "businessView": {
    "keyMessage": "Recommended strategy: Sharded multi-region ...",
    "justification": ["Insufficient quota in any single region; ..."],
    "risks": ["Quota is insufficient or low in some regions."],
    "mitigations": ["Request quota increase via Azure portal."]
  },
  "technicalView": {
    "allocations": [
      {
        "region": "swedencentral",
        "role": "primary",
        "sku": "Standard_NC24ads_A100_v4",
        "instanceCount": 20,
        "zones": ["1", "2", "3"],
        "confidenceScore": 78
      }
    ],
    "latencyMatrix": {
      "swedencentral": { "swedencentral": 0, "westeurope": 29, "francecentral": 32 }
    },
    "evaluatedAt": "2025-01-15T14:30:00+00:00"
  },
  "warnings": ["Spot placement score is probabilistic and not a guarantee."],
  "missingInputs": [],
  "errors": [],
  "disclaimer": "This tool is not affiliated with Microsoft. ..."
}
```

#### Strategy types

| Strategy | When selected |
|---|---|
| `single_region` | Enough capacity in one region, or only one candidate available |
| `active_active` | Stateless workload with multi-region benefit |
| `active_passive` | Stateful workload requiring a failover region |
| `sharded_multi_region` | Quota insufficient in any single region — instances split across regions |
| `progressive_ramp` | Partial quota available — start in primary, overflow to secondary |
| `time_window_deploy` | Spot preference but current spot score is low — wait for better window |
| `burst_overflow` | Burst-capable workload with a dedicated overflow region |

#### Agent usage examples

Via the MCP `capacity_strategy` tool:

- *"Deploy 48 GPUs across EU regions with max 50ms latency"*
- *"Multi-region inference architecture for Standard_NC24ads_A100_v4 with spot pricing"*
- *"Active/passive failover for stateful workload in France with EUR budget cap"*


## Under the hood

The backend calls the Azure Resource Manager REST API to fetch:
- **Zone mappings**: `availabilityZoneMappings` from `/subscriptions/{id}/locations` endpoint
- **Resource SKUs**: SKU details from `/subscriptions/{id}/providers/Microsoft.Compute/skus` endpoint with zone restrictions and capabilities
- **Compute Usages**: vCPU quota per VM family from `/subscriptions/{id}/providers/Microsoft.Compute/locations/{region}/usages` endpoint (cached for 10 minutes, with retry on throttling and graceful handling of 403)
- **Spot Placement Scores**: likelihood indicators for Spot VM allocation from `/subscriptions/{id}/providers/Microsoft.Compute/locations/{region}/placementScores/spot/generate` endpoint (batched in chunks of 100, sequential execution with retry/back-off, cached for 10 minutes). Note: these scores reflect the probability of obtaining a Spot VM allocation, not datacenter capacity.

## Deployment Confidence Score

The **Deployment Confidence Score** is a heuristic 0–100 estimate of how likely a VM SKU deployment is to succeed in a given region/subscription. It is computed **exclusively on the backend** by the canonical module `src/az_scout/scoring/deployment_confidence.py` — the frontend displays what the API returns and never recomputes locally.

### Scoring version

The current scoring version is **v1** (`SCORING_VERSION = "v1"`). Every API response includes a `scoringVersion` field. Bump the version when weights or normalisation rules change.

### Signals & weights

| Signal | Weight | Source | Normalisation |
|---|---|---|---|
| `quota` | 0.25 | Compute Usages API | `remaining_vcpus / vcpus_per_vm / 10`, capped at 1.0 |
| `spot` | 0.35 | Spot Placement Scores API | High → 1.0, Medium → 0.6, Low → 0.25 |
| `zones` | 0.15 | Resource SKUs API | `available_zones / 3`, capped at 1.0 |
| `restrictions` | 0.15 | Resource SKUs API | No restrictions → 1.0, any → 0.0 |
| `pricePressure` | 0.10 | Retail Prices API | `(0.8 − spot/paygo) / 0.6`, clamped 0–1 |

### Missing signals & renormalisation

When a signal is unavailable (e.g. Spot score not fetched), it is excluded and the remaining weights are renormalised so they sum to 1.0. If fewer than **2** signals are available, the result is `label="Unknown", score=0`.

### Label mapping

| Threshold | Label |
|---|---|
| ≥ 80 | High |
| ≥ 60 | Medium |
| ≥ 40 | Low |
| < 40 | Very Low |

### Disclaimers

Every result includes these disclaimers:

1. This is a heuristic estimate, not a guarantee of deployment success.
2. Signals are derived from Azure APIs and may change at any time.
3. No Microsoft guarantee is expressed or implied.

### Bulk endpoint

`POST /api/deployment-confidence` accepts a list of SKU names and returns canonical confidence results for each. The frontend calls this endpoint after spot-score updates to refresh displayed scores.


## Authentication

az-scout supports two authentication modes controlled by the `AUTH_MODE` environment variable:

| Mode | Description |
|---|---|
| `entra` | Real Entra ID authentication via `fastapi-azure-auth`. All `/api/*` endpoints require a valid bearer token. |
| `mock` | Authentication is bypassed — all API calls succeed without a token. **For local development only.** |

The `/health` endpoint is always public (no authentication required).

### Quick start (mock mode)

```bash
# No Azure config needed
AUTH_MODE=mock uv run uvicorn az_scout.app:app --reload
# or
make dev-mock
```

### Quick start (Entra ID mode)

```bash
# Set required environment variables (or use a .env file — see .env.example)
export AUTH_MODE=entra
export AZURE_TENANT_ID=<your-tenant-id>
export AZURE_CLIENT_ID=<your-client-id>
export AZURE_CLIENT_SECRET=<your-client-secret>
export AZURE_API_SCOPE=api://<your-client-id>/access_as_user

uv run uvicorn az_scout.app:app --reload
# or
make dev
```

Then open `http://localhost:8000/docs` and click **Authorize** to sign in via the Swagger UI.

### Entra ID Setup

1. **Create an App Registration** in the [Azure portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).

2. **Expose an API**
   - Set the **Application ID URI** to `api://<client_id>`.
   - Add a scope: `access_as_user` (type: Delegated, admin consent: as needed).
   - See [Quickstart: Expose a web API](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-expose-web-apis).

3. **Authentication**
   - Add the **Web** platform.
   - Add a Redirect URI: `http://localhost:8000/docs/oauth2-redirect`
   - Enable **ID tokens** and **Access tokens**.

4. **Create a Client Secret**
   - Under **Certificates & secrets**, create a new client secret.
   - Copy the **Value** (not the Secret ID) into `AZURE_CLIENT_SECRET`.

5. **Grant Admin Consent** (if required by your organisation)
   - Under **API permissions**, grant admin consent for the configured scopes.

6. **Configure your `.env`** – copy `.env.example` and fill in the values.

### On-Behalf-Of (OBO) Flow

When `AUTH_MODE=entra`, the API can exchange the user's bearer token for an Azure Resource Manager token using the [OBO flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow). This allows downstream ARM calls to run with the signed-in user's identity and permissions.

The OBO credential is provided by [`OnBehalfOfCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.onbehalfofcredential) from `azure-identity`.

**Requirement:** `AZURE_CLIENT_SECRET` must be set for OBO to work.

### MCP Compatibility

When the MCP server is mounted in the same FastAPI process (`/mcp`), it shares the same authentication context. MCP clients connecting via Streamable HTTP should pass a bearer token in the `Authorization` header when Entra ID authentication is enabled.

When the MCP server runs standalone (`az-scout mcp`), it uses `DefaultAzureCredential` directly (same as before).

### Disclaimer

- This API uses delegated user permissions via Entra ID.
- Deployment signals are heuristic estimates.
- No deployment success is guaranteed.


## License

[MIT](LICENSE.txt)


## Disclaimer

> **This tool is not affiliated with Microsoft.** All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Spot placement scores are probabilistic. Quota values and pricing are dynamic and may change between planning and actual deployment. Latency values are based on [Microsoft published statistics](https://learn.microsoft.com/en-us/azure/networking/azure-network-latency) and must be validated with in-tenant measurements.
