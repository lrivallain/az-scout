# az-mapping

Visualize how Azure maps **logical** Availability Zones to **physical** zones across your subscriptions.

> Different subscriptions may map the same logical zone (e.g. Zone 1) to different physical datacenters. This tool lets you compare them side-by-side.

## Quick start

```bash
# Make sure you are authenticated to Azure
az login

# Run the tool (no install required)
uvx az-mapping
```

Your browser opens automatically at `http://127.0.0.1:5001`.

### CLI options

```
az-mapping [COMMAND] [OPTIONS]
```

#### `az-mapping web` (default)

Run the web UI. This is the default when no subcommand is given.

```
  --host TEXT     Host to bind to.  [default: 127.0.0.1]
  --port INTEGER  Port to listen on.  [default: 5001]
  --no-open       Don't open the browser automatically.
  -v, --verbose   Enable verbose logging.
  --reload        Auto-reload on code changes (development only).
  --help          Show this message and exit.
```

#### `az-mapping mcp`

Run the MCP server.

```
  --sse           Use SSE transport instead of stdio.
  --port INTEGER  Port for SSE transport.  [default: 8080]
  -v, --verbose   Enable verbose logging.
  --help          Show this message and exit.
```

### Alternative install

```bash
pip install az-mapping
az-mapping
```

## Prerequisites

| Requirement | Details |
|---|---|
| Python | ≥ 3.11 |
| Azure credentials | Any method supported by `DefaultAzureCredential` (`az login`, managed identity, …) |
| RBAC | **Reader** on the subscriptions you want to query |

## Features

- **Region selector** – AZ-enabled regions, loaded automatically.
- **Subscription picker** – searchable, multi-select.
- **Collapsible sidebar** – toggle the filter panel to maximize the results area.
- **Graph view** – D3.js bipartite diagram (Logical Zone → Physical Zone), colour-coded per subscription with interactive hover highlighting.
- **Table view** – comparison table with consistency indicators.
- **SKU availability view** – shows VM SKU availability per physical zone with vCPU quota usage (limit / used / remaining) and CSV export.
- **Spot Placement Scores** – evaluate the likelihood of Spot VM allocation (High / Medium / Low) per SKU for a given region and instance count, powered by the Azure Compute RP.
- **Deployment Confidence Score** – a composite 0–100 score per SKU estimating deployment success probability, synthesised from quota headroom, Spot Placement Score, availability zone breadth, restrictions, and price pressure signals. Missing signals are automatically excluded with weight renormalisation. The score updates live when Spot Placement Scores arrive.
- **Deployment Plan** – agent-ready `POST /api/deployment-plan` endpoint that evaluates (region, SKU) combinations against zones, quotas, spot scores, pricing, and restrictions. Returns a deterministic, ranked plan with business and technical views (no LLM, no invention — missing data is flagged explicitly).
- **Export** – download the graph as PNG or the tables as CSV.
- **Shareable URLs** – filters are reflected in the URL; reload or share a link to restore the exact view.
- **MCP server** – expose all capabilities as MCP tools for AI agents (see below).

## MCP server

An [MCP](https://modelcontextprotocol.io/) server is included, allowing AI agents (Claude Desktop, VS Code Copilot, etc.) to query zone mappings and SKU availability directly.

### Available tools

| Tool | Description |
|---|---|
| `list_tenants` | Discover Azure AD tenants and authentication status |
| `list_subscriptions` | List enabled subscriptions (optionally scoped to a tenant) |
| `list_regions` | List regions that support Availability Zones |
| `get_zone_mappings` | Get logical→physical zone mappings for subscriptions in a region |
| `get_sku_availability` | Get VM SKU availability per zone with restrictions, capabilities, and vCPU quota per family |
| `get_spot_scores` | Get Spot Placement Scores (High / Medium / Low) for a list of VM sizes in a region |

`get_sku_availability` supports optional filters to reduce output size:
`name`, `family`, `min_vcpus`, `max_vcpus`, `min_memory_gb`, `max_memory_gb`.

### Usage

#### stdio transport (default – for Claude Desktop, VS Code, etc.)

```bash
az-mapping mcp
```

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "az-mapping": {
      "command": "az-mapping",
      "args": ["mcp"]
    }
  }
}
```

If using `uv`:

```json
{
  "mcpServers": {
    "az-mapping": {
      "command": "uvx",
      "args": ["az-mapping", "mcp"]
    }
  }
}
```

#### SSE transport

```bash
az-mapping mcp --sse --port 8080
```

## Deployment Plan API

The `POST /api/deployment-plan` endpoint provides a deterministic decision engine for deployment planning. It is designed for Sales / Solution Engineers and AI agents: no LLM is involved — every decision traces back to real Azure data.

### Request

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

### Response (abbreviated)

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

## How it works

The backend calls the Azure Resource Manager REST API to fetch:
- **Zone mappings**: `availabilityZoneMappings` from `/subscriptions/{id}/locations` endpoint
- **Resource SKUs**: SKU details from `/subscriptions/{id}/providers/Microsoft.Compute/skus` endpoint with zone restrictions and capabilities
- **Compute Usages**: vCPU quota per VM family from `/subscriptions/{id}/providers/Microsoft.Compute/locations/{region}/usages` endpoint (cached for 10 minutes, with retry on throttling and graceful handling of 403)
- **Spot Placement Scores**: likelihood indicators for Spot VM allocation from `/subscriptions/{id}/providers/Microsoft.Compute/locations/{region}/placementScores/spot/generate` endpoint (batched in chunks of 100, sequential execution with retry/back-off, cached for 10 minutes). Note: these scores reflect the probability of obtaining a Spot VM allocation, not datacenter capacity.

The frontend renders the results as an interactive graph, comparison table, and SKU availability table with quota columns.

API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the server is running.

## License

[MIT](LICENSE.txt)
