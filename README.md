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
- **SKU availability view** – shows VM SKU availability per physical zone with filtering and CSV export.
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
| `get_sku_availability` | Get VM SKU availability per zone with restrictions and capabilities |

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

## How it works

The backend calls the Azure Resource Manager REST API to fetch:
- **Zone mappings**: `availabilityZoneMappings` from `/subscriptions/{id}/locations` endpoint
- **Resource SKUs**: SKU details from `/subscriptions/{id}/providers/Microsoft.Compute/skus` endpoint with zone restrictions and capabilities

The frontend renders the results as an interactive graph, comparison table, and SKU availability table.

API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the server is running.

## License

[MIT](LICENSE.txt)
