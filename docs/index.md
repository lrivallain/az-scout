---
hide:
  - navigation
  - toc
description: "Scout Azure regions for VM availability, zone mappings, pricing, spot scores, and quota — then plan deployments with confidence."
---

<div align="center" markdown>
# Azure Scout

[![CI](https://github.com/lrivallain/az-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/lrivallain/az-scout/actions/workflows/ci.yml)
[![Publish to PyPI](https://github.com/lrivallain/az-scout/actions/workflows/publish.yml/badge.svg)](https://github.com/lrivallain/az-scout/actions/workflows/publish.yml)
[![PyPI version](https://img.shields.io/pypi/v/az-scout)](https://pypi.org/project/az-scout/)
[![Downloads](https://img.shields.io/pypi/dm/az-scout)](https://pypi.org/project/az-scout/)
[![License](https://img.shields.io/github/license/lrivallain/az-scout)](https://github.com/lrivallain/az-scout/blob/main/LICENSE.txt)

**Scout Azure regions for VM availability, zone mappings, pricing, spot scores, and quota — then plan deployments with confidence.**

[Get Started](getting-started.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/lrivallain/az-scout){ .md-button }
</div>

---

## What is az-scout?

**az-scout** helps Azure operators and architects answer the questions that matter when planning resilient, cost-efficient VM deployments:

- *Do my subscriptions share the same physical datacenter for logical zone 1?*
- *Which VM SKUs are available in all three zones with headroom in my quota?*
- *What is the Spot placement likelihood for this SKU family right now?*

All from a single web UI — or through an MCP-powered AI agent connected to your favourite tools (Claude, VS Code Copilot, etc.).

<div align="center" markdown>
<!-- Hero screenshot: AZ Topology graph + Planner table side by side -->
![az-scout web UI showing zone mappings and SKU availability](assets/screenshots/hero.png){ .screenshot }
</div>

---

## Key Features

<div class="grid cards" markdown>

-   :material-map-marker-radius:{ .lg .middle } **Zone Mapping**

    ---

    Visualise how Azure maps logical Availability Zones to physical zones across subscriptions in a region. Detect misalignments before they cause outages.

    [:octicons-arrow-right-24: Learn more](features.md#zone-mapping)

-   :material-server:{ .lg .middle } **SKU Availability**

    ---

    VM SKU availability per physical zone with vCPU quota usage, numeric filters, and CSV export. Know exactly what you can deploy, where.

    [:octicons-arrow-right-24: Learn more](features.md#sku-availability)

-   :material-lightning-bolt:{ .lg .middle } **Spot Placement Scores**

    ---

    Per-SKU Spot VM allocation likelihood — High / Medium / Low — from the Azure Compute Resource Provider. Make informed decisions on Spot workloads.

    [:octicons-arrow-right-24: Learn more](features.md#spot-placement-scores)

-   :material-gauge:{ .lg .middle } **Deployment Confidence Score**

    ---

    A composite 0–100 score per SKU synthesised from quota pressure, spot scores, zone breadth, restrictions, and price. No guesswork.

    [:octicons-arrow-right-24: Learn more](features.md#deployment-confidence-score)

-   :material-robot:{ .lg .middle } **MCP Server**

    ---

    All capabilities exposed as [Model Context Protocol](https://modelcontextprotocol.io/) tools. Connect Claude Desktop, VS Code Copilot, or any MCP-compatible AI agent.

    [:octicons-arrow-right-24: Learn more](ai/mcp.md)

-   :material-puzzle:{ .lg .middle } **Plugin System**

    ---

    Extend az-scout with pip-installable plugins — new API routes, MCP tools, UI tabs, and chat modes. The scaffold gets you started in minutes.

    [:octicons-arrow-right-24: Learn more](plugins/index.md)

</div>

---

## Quick Start

=== "uv (recommended)"

    ```bash
    # Authenticate to Azure
    az login

    # Run az-scout — no install required
    uvx az-scout
    ```

=== "pip"

    ```bash
    pip install az-scout
    az-scout
    ```

=== "Docker"

    ```bash
    docker run --rm -p 8000:8000 \
      -e AZURE_TENANT_ID=<your-tenant> \
      -e AZURE_CLIENT_ID=<your-sp-client-id> \
      -e AZURE_CLIENT_SECRET=<your-sp-secret> \
      ghcr.io/lrivallain/az-scout:latest
    ```

Your browser opens automatically at `http://127.0.0.1:5001`.

---

## Known Plugins

--8<-- "docs/_includes/known-plugins.md"

[Develop your own plugin →](plugins/index.md){ .md-button }

---

## Disclaimer

> This tool is not affiliated with Microsoft. All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Spot placement scores are probabilistic. Quota values and pricing are dynamic and may change between planning and actual deployment.
