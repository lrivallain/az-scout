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
az-mapping [OPTIONS]

  --host TEXT     Host to bind to.  [default: 127.0.0.1]
  --port INTEGER  Port to listen on.  [default: 5001]
  --no-open       Don't open the browser automatically.
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
- **SKU availability view** – explore VM SKUs available in each Availability Zone:
  - Lists resource SKUs (VM sizes) with their capabilities (vCPUs, memory)
  - Shows zone availability indicators (✓ available, — unavailable, ⚠ restricted)
  - Filterable by SKU name for quick searches (e.g., "D2s", "E4")
  - Export SKU data as CSV
- **Export** – download the graph as PNG or the tables as CSV.
- **Shareable URLs** – filters are reflected in the URL; reload or share a link to restore the exact view.

## How it works

The backend calls the Azure Resource Manager REST API to fetch:
- **Zone mappings** (`2022-12-01` API): `availabilityZoneMappings` from `/subscriptions/{id}/locations` endpoint
- **Resource SKUs** (`2021-07-01` API): SKU details from `/subscriptions/{id}/providers/Microsoft.Compute/skus` endpoint with zone restrictions and capabilities

The frontend renders the results as an interactive graph, comparison table, and SKU availability table.

## License

[MIT](LICENSE.txt)
