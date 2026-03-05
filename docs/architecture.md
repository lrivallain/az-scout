---
description: "System architecture, data flow, and internal plugin structure of az-scout."
---

# Architecture

This page describes az-scout's internal architecture — useful for contributors and plugin developers.

---

## System Overview

```mermaid
graph TB
    subgraph Browser["Web Browser"]
        UI["Single-page UI<br/>(Bootstrap 5 + D3.js)"]
    end

    subgraph Core["az-scout core"]
        APP["app.py<br/>(FastAPI bootstrap)"]
        DISC["routes/discovery.py<br/>(tenants, subs, regions)"]
        CHAT["POST /api/chat<br/>(SSE streaming)"]
        MCP["MCP Server<br/>(/mcp endpoint)"]
    end

    subgraph Plugins["Internal Plugins"]
        TOPO["topology/<br/>GET /api/mappings<br/>get_zone_mappings tool"]
        PLAN["planner/<br/>GET /api/skus<br/>POST /api/deployment-confidence<br/>+ 3 more routes, 4 MCP tools"]
    end

    subgraph Shared["Shared Modules"]
        API["azure_api/<br/>(ARM calls, caching)"]
        SCORE["scoring/<br/>(Deployment Confidence)"]
        SVC["services/<br/>(deployment_planner, ai_chat)"]
    end

    subgraph Azure["Azure"]
        ARM["Azure Resource Manager"]
        PRICES["Retail Prices API"]
        OPENAI["Azure OpenAI"]
    end

    UI -->|REST| APP
    UI -->|REST| DISC
    UI -->|REST| TOPO
    UI -->|REST| PLAN
    UI -->|SSE| CHAT

    MCP -->|tool calls| TOPO
    MCP -->|tool calls| PLAN

    TOPO --> API
    PLAN --> API
    PLAN --> SCORE
    DISC --> API
    CHAT --> SVC

    API --> ARM
    API --> PRICES
    SVC --> OPENAI
```

---

## Request Flow: SKU Availability

How a `GET /api/skus?region=westeurope&subscriptionId=xxx&includePrices=true` request flows through the system:

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as planner/routes.py
    participant A as azure_api/skus.py
    participant Q as azure_api/quotas.py
    participant P as azure_api/pricing.py
    participant S as scoring/
    participant ARM as Azure ARM

    B->>R: GET /api/skus?region=westeurope&...
    R->>A: get_skus(region, sub_id, ...)
    A->>ARM: GET /providers/Microsoft.Compute/skus?$filter=location eq 'westeurope'
    ARM-->>A: SKU list (zones, restrictions, capabilities)
    A-->>R: list[dict]

    R->>Q: enrich_skus_with_quotas(skus, ...)
    Q->>ARM: GET /providers/Microsoft.Compute/locations/westeurope/usages
    ARM-->>Q: quota usage per family
    Q-->>R: skus updated in-place

    R->>P: enrich_skus_with_prices(skus, ...)
    P->>ARM: GET prices.azure.com/retail/prices?$filter=...
    ARM-->>P: PAYGO + Spot prices
    P-->>R: skus updated in-place

    R->>S: enrich_skus_with_confidence(skus)
    S-->>R: confidence scores added

    R-->>B: JSONResponse(skus)
```

---

## Internal Plugin Architecture

Both built-in features (AZ Topology, Deployment Planner) and external plugins use the same `AzScoutPlugin` protocol:

```mermaid
graph LR
    subgraph Core
        DISC2["discover_internal_plugins()"]
        EP["importlib entry_points()"]
        REG["register_plugins()"]
    end

    subgraph Internal["Internal Plugins (core package)"]
        T["topology/"]
        P["planner/"]
    end

    subgraph External["External Plugins (pip packages)"]
        E1["az-scout-plugin-batch-sku"]
        E2["az-scout-plugin-latency-stats"]
    end

    DISC2 --> T
    DISC2 --> P
    EP --> E1
    EP --> E2

    T --> REG
    P --> REG
    E1 --> REG
    E2 --> REG

    REG -->|routes| FA["FastAPI app"]
    REG -->|tools| MC["MCP server"]
    REG -->|tabs| UI2["Jinja2 template"]
    REG -->|chat modes| CH["AI chat"]
```

**Internal vs external plugins:**

| Aspect | Internal | External |
|--------|----------|----------|
| Location | `src/az_scout/internal_plugins/` | Separate pip package |
| Route prefix | `/api` (backward-compatible) | `/plugins/{name}` |
| Static prefix | `/internal/{name}/static` | `/plugins/{name}/static` |
| Discovery | `discover_internal_plugins()` | `importlib.metadata.entry_points` |
| Plugin Manager | Shows "built-in" badge | Install / uninstall / update |

---

## Module Map

```
src/az_scout/
├── app.py                    # FastAPI bootstrap (220 lines)
├── logging_config.py         # Unified coloured logging
├── cli.py                    # Click CLI (web + mcp)
├── mcp_server.py             # MCP server (discovery tools only)
├── plugin_api.py             # AzScoutPlugin protocol + dataclasses
├── plugins.py                # Plugin discovery + registration
├── plugin_manager/           # Plugin install/validate/uninstall (7 modules)
├── azure_api/                # Azure ARM helpers (stable API: __all__ + PLUGIN_API_VERSION)
├── scoring/                  # Deployment Confidence Score
├── services/
│   ├── ai_chat/              # AI chat (6 modules)
├── models/                   # Pydantic models
├── routes/
│   ├── __init__.py            # Plugin manager API
│   └── discovery.py           # Tenants, subscriptions, regions
├── internal_plugins/
│   ├── topology/              # AZ Topology tab
│   └── planner/               # Deployment Planner tab
├── static/                    # Core JS/CSS/images
└── templates/                 # Jinja2 template
```
