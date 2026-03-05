---
description: "REST API reference for az-scout: discovery, topology, planner, scoring, and plugin management endpoints."
---

# API Reference

az-scout exposes a REST API backed by FastAPI. Interactive documentation is available at runtime:

- **Swagger UI**: `http://127.0.0.1:5001/docs`
- **ReDoc**: `http://127.0.0.1:5001/redoc`

---

## Discovery Endpoints

### `GET /api/tenants`

List Azure AD tenants accessible with the current credentials.

=== "curl"

    ```bash
    curl http://localhost:5001/api/tenants
    ```

=== "httpx (Python)"

    ```python
    import httpx
    r = httpx.get("http://localhost:5001/api/tenants")
    print(r.json())
    ```

**Response:**

```json
[
  {
    "tenant_id": "00000000-0000-0000-0000-000000000000",
    "display_name": "Contoso",
    "authenticated": true
  }
]
```

---

### `GET /api/subscriptions`

List enabled Azure subscriptions.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `tenantId` | `string` *(optional)* | Scope to a specific tenant |

=== "curl"

    ```bash
    curl "http://localhost:5001/api/subscriptions?tenantId=00000000-..."
    ```

**Response:**

```json
[
  {
    "id": "00000000-0000-0000-0000-000000000000",
    "name": "Production"
  }
]
```

---

### `GET /api/regions`

List Azure regions that support Availability Zones.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `subscriptionId` | `string` *(optional)* | Scope to a specific subscription |
| `tenantId` | `string` *(optional)* | Scope to a specific tenant |

**Response:**

```json
[
  {
    "name": "westeurope",
    "displayName": "West Europe"
  }
]
```

---

## Topology Endpoints

### `GET /api/mappings`

Get logical-to-physical zone mappings for subscriptions in a region.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `region` | `string` | Azure region name (e.g. `westeurope`) |
| `subscriptionIds` | `string[]` | One or more subscription IDs |
| `tenantId` | `string` *(optional)* | Tenant ID |

**Response:**

```json
[
  {
    "subscription_id": "00000000-0000-0000-0000-000000000000",
    "subscription_name": "Production",
    "mappings": [
      { "logicalZone": "1", "physicalZone": "westeurope-az1" },
      { "logicalZone": "2", "physicalZone": "westeurope-az2" },
      { "logicalZone": "3", "physicalZone": "westeurope-az3" }
    ]
  }
]
```

---

## Planner Endpoints

### `GET /api/skus`

Get VM SKU availability per zone for a region and subscription.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `region` | `string` | Azure region name |
| `subscriptionId` | `string` | Subscription ID |
| `tenantId` | `string` *(optional)* | Tenant ID |
| `resourceType` | `string` *(optional)* | Resource type filter (default: `virtualMachines`) |
| `name` | `string` *(optional)* | SKU name substring filter |
| `family` | `string` *(optional)* | SKU family substring filter |
| `minVcpus` / `maxVcpus` | `integer` *(optional)* | vCPU count range |
| `minMemoryGb` / `maxMemoryGb` | `float` *(optional)* | Memory range in GB |
| `includePrices` | `boolean` *(optional)* | Include retail pricing (default: `false`) |
| `currencyCode` | `string` *(optional)* | Currency code (default: `USD`) |

=== "curl"

    ```bash
    curl "http://localhost:5001/api/skus?region=westeurope&subscriptionId=xxx&name=D4s&includePrices=true"
    ```

=== "curl (filtered)"

    ```bash
    # 4-8 vCPU D-series VMs with pricing
    curl "http://localhost:5001/api/skus?region=eastus&subscriptionId=xxx&family=DSv5&minVcpus=4&maxVcpus=8&includePrices=true"
    ```

**Response (truncated):**

```json
[
  {
    "name": "Standard_D4s_v5",
    "tier": "Standard",
    "family": "standardDSv5Family",
    "zones": ["1", "2", "3"],
    "restrictions": [],
    "capabilities": { "vCPUs": "4", "MemoryGB": "16" },
    "quota": { "limit": 350, "used": 48, "remaining": 302 },
    "pricing": { "paygo": 0.192, "spot": 0.0384, "currency": "USD" },
    "confidence": { "score": 88, "label": "High", "scoreType": "basic" }
  }
]
```

---

### `POST /api/deployment-confidence`

Compute Deployment Confidence Scores for one or more SKUs.

=== "curl"

    ```bash
    curl -X POST http://localhost:5001/api/deployment-confidence \
      -H 'Content-Type: application/json' \
      -d '{
        "region": "westeurope",
        "subscriptionId": "xxx",
        "skus": ["Standard_D4s_v5", "Standard_E4s_v5"],
        "preferSpot": false,
        "instanceCount": 3,
        "includeSignals": true
      }'
    ```

**Request body:**

```json
{
  "region": "westeurope",
  "subscription_id": "00000000-0000-0000-0000-000000000000",
  "skus": ["Standard_D4s_v5", "Standard_E4s_v5"],
  "prefer_spot": false,
  "instance_count": 3,
  "include_signals": true
}
```

---

### `GET /api/spot-scores`

Get Spot Placement Scores for VM sizes in a region.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `region` | `string` | Azure region name |
| `subscriptionId` | `string` | Subscription ID |
| `vmSizes` | `string[]` | List of VM size names |
| `tenantId` | `string` *(optional)* | Tenant ID |

---

### `GET /api/sku-pricing`

Get retail pricing for a SKU in a region.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `region` | `string` | Azure region name |
| `skuName` | `string` | SKU name (e.g. `Standard_D4s_v5`) |

---

## Plugin Manager Endpoints

### `GET /api/plugins`

List all loaded plugins (built-in and external).

### `GET /api/plugins/recommended`

List curated recommended plugins with install status.

### `POST /api/plugins/install`

Install a plugin from PyPI or a GitHub URL.

### `DELETE /api/plugins/{name}`

Uninstall a plugin by name.

---

## Error Responses

Per-subscription errors are returned inline (not as HTTP errors):

```json
{
  "subscription_id": "00000000-0000-0000-0000-000000000000",
  "error": {
    "code": "AuthorizationFailed",
    "message": "User is not authorized to perform this action."
  }
}
```

Unhandled server errors return HTTP 500:

```json
{ "error": "Unexpected error message" }
```
