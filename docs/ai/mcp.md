---
description: "Connect AI agents (Claude, VS Code Copilot) to az-scout via the Model Context Protocol (MCP) server."
---

# MCP Server

az-scout includes a full [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that exposes all capabilities as tools for AI agents.

---

## Available Tools

### Core discovery tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_tenants` | *(none)* | List Azure AD tenants with authentication status |
| `list_subscriptions` | `tenant_id?` | List enabled subscriptions, optionally scoped to a tenant |
| `list_regions` | `subscription_id?`, `tenant_id?` | List regions that support Availability Zones |

### Topology tools *(built-in plugin)*

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_zone_mappings` | `region`, `subscription_ids`, `tenant_id?` | Logical→physical zone mappings for subscriptions in a region |

### Planner tools *(built-in plugin)*

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_sku_availability` | `region`, `subscription_id`, `tenant_id?`, `resource_type?`, `name?`, `family?`, `min_vcpus?`, `max_vcpus?`, `min_memory_gb?`, `max_memory_gb?` | VM SKU availability per zone with quota, restrictions, and confidence |
| `get_spot_scores` | `region`, `subscription_id`, `vm_sizes`, `tenant_id?` | Spot Placement Scores (High / Medium / Low) for a list of VM sizes |
| `get_sku_deployment_confidence` | `region`, `subscription_id`, `skus`, `prefer_spot?`, `instance_count?`, `include_signals?`, `include_provenance?`, `tenant_id?` | Deployment Confidence Scores (0–100) with full signal breakdown |
| `get_sku_pricing_detail` | `region`, `sku_name`, `tenant_id?` | Detailed Linux pricing (PayGo, Spot, RI 1Y/3Y, SP 1Y/3Y) and VM profile |

!!! tip "Plugin tools"
    Plugins can register additional MCP tools. For example, the [Strategy Advisor plugin](https://github.com/az-scout/az-scout-plugin-strategy-advisor) adds a `capacity_strategy` tool.

### `get_sku_availability` filters

Use these optional filters to reduce output size — important in conversational contexts:

| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `name` | `str` | `"D2s"` | Case-insensitive substring match on SKU name |
| `family` | `str` | `"DSv3"` | Case-insensitive substring match on SKU family |
| `min_vcpus` / `max_vcpus` | `int` | `4` / `8` | vCPU count range (inclusive) |
| `min_memory_gb` / `max_memory_gb` | `float` | `16.0` | Memory in GB range (inclusive) |

---

## Transport Options

### stdio (default)

The default transport for use with Claude Desktop, VS Code Copilot, and other desktop AI clients.

```bash
az-scout mcp
```

Add to your MCP client configuration:

=== "Direct"

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

=== "With uv"

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

### Streamable HTTP

When running `az-scout web`, the MCP server is automatically available at `/mcp` alongside the web UI — no separate server needed.

For MCP-only use with Streamable HTTP:

```bash
az-scout mcp --http --port 8082
```

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "az-scout": {
      "url": "http://localhost:8082/mcp"
    }
  }
}
```

For a hosted Container App deployment, point to `https://<your-app-url>/mcp`.

---

## Hosted Deployment (EasyAuth)

When running as a Container App with Entra ID authentication (EasyAuth) enabled, MCP clients must pass a bearer token in the `Authorization` header.

See the [EasyAuth guide](../deployment/easyauth.md#7-connect-mcp-clients-through-easyauth) for detailed configuration instructions.

---

## Prompt Examples

See the [Prompt Examples](prompts.md) page for natural-language queries you can use with any MCP-connected AI agent.
