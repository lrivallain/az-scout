# Enable Entra ID Authentication

This guide walks through creating an Entra ID App Registration and configuring az-scout to require sign-in via Microsoft Entra ID (MSAL.js front-end + fastapi-azure-auth back-end).

## Architecture overview

```
Browser (MSAL.js)                az-scout (FastAPI)              Azure ARM
  │                                   │                            │
  ├─── loginPopup() ──────► Entra ID  │                            │
  │    ◄── id_token + access_token ───┤                            │
  │                                   │                            │
  ├─── GET /api/* ────────────────────►│                            │
  │    Authorization: Bearer <token>  │                            │
  │                                   ├── OBO exchange ───────────►│
  │                                   │   (user_assertion → ARM)   │
  │    ◄── JSON response ────────────┤◄── ARM data ───────────────┤
```

1. The browser uses **MSAL.js** to sign the user in (popup flow) and obtains an access token scoped to `api://<clientId>/access_as_user`.
2. Every API call includes this token in the `Authorization` header.
3. **fastapi-azure-auth** validates the token on the backend.
4. For ARM calls, the backend exchanges the user token via the **On-Behalf-Of (OBO)** flow to get a token scoped to `https://management.azure.com/.default`.

## Prerequisites

- An Azure subscription
- **Azure CLI** (`az`) installed and authenticated
- Permissions to create App Registrations in your Entra ID tenant (or ask your tenant admin)

## 1. Create the App Registration

```bash
# Set variables
APP_NAME="az-scout"
TENANT_ID=$(az account show --query tenantId -o tsv)

# Create the App Registration (single-tenant)
az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv
```

Save the output — this is your **Application (client) ID** (`AUTH_CLIENT_ID`).

```bash
CLIENT_ID=<paste-the-client-id>
```

## 2. Create a Client Secret

```bash
az ad app credential reset \
  --id $CLIENT_ID \
  --display-name "az-scout-secret" \
  --years 2 \
  --query password -o tsv
```

Save the output — this is your **Client Secret** (`AUTH_CLIENT_SECRET`).

> **Important:** The secret value is shown only once. Store it securely.

## 3. Expose an API scope

Create the `access_as_user` scope that the frontend requests:

```bash
# Generate a scope ID
SCOPE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

# Set the Application ID URI and add the scope
az ad app update --id $CLIENT_ID \
  --identifier-uris "api://$CLIENT_ID" \
  --set "api.oauth2PermissionScopes=[{ \
    \"id\": \"$SCOPE_ID\", \
    \"adminConsentDescription\": \"Access az-scout API on behalf of the signed-in user\", \
    \"adminConsentDisplayName\": \"Access az-scout\", \
    \"userConsentDescription\": \"Access az-scout API on your behalf\", \
    \"userConsentDisplayName\": \"Access az-scout\", \
    \"isEnabled\": true, \
    \"type\": \"User\", \
    \"value\": \"access_as_user\" \
  }]"
```

Your **API Scope URI** (`AUTH_API_SCOPE`) is:

```
api://<CLIENT_ID>/access_as_user
```

## 4. Pre-authorize the application

Pre-authorize the app for its own scope so users are **not prompted for consent**:

```bash
az ad app update --id $CLIENT_ID \
  --set "api.preAuthorizedApplications=[{ \
    \"appId\": \"$CLIENT_ID\", \
    \"delegatedPermissionIds\": [\"$SCOPE_ID\"] \
  }]"
```

## 5. Create a Service Principal

A Service Principal is required for users to sign in:

```bash
az ad sp create --id $CLIENT_ID
```

## 6. Add SPA redirect URIs

Register the URLs where Entra ID is allowed to redirect after authentication.

**For local development:**

```bash
az ad app update --id $CLIENT_ID \
  --spa-redirect-uris \
    "http://localhost:5001" \
    "http://127.0.0.1:5001"
```

**After deployment** (add the Container App URL):

```bash
APP_URL=$(az deployment group show \
  -g <resource-group> -n main \
  --query "properties.outputs.appUrl.value" -o tsv)

# Add the deployed URL alongside the local ones
az ad app update --id $CLIENT_ID \
  --spa-redirect-uris \
    "http://localhost:5001" \
    "http://127.0.0.1:5001" \
    "$APP_URL"
```

> **Important:** These must be **SPA** redirect URIs (not Web). The MSAL.js popup flow requires the SPA platform.

## 7. Grant Microsoft Graph User.Read (optional)

This is only needed if your tenant requires explicit permission grants:

```bash
# Microsoft Graph app ID: 00000003-0000-0000-c000-000000000000
# User.Read permission ID: e1fe6dd8-ba31-4d61-89e7-88639da4683d
az ad app permission add \
  --id $CLIENT_ID \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope
```

## 8. Deploy with authentication

### Option A: Azure CLI

```bash
RG="rg-az-scout"
az deployment group create \
  -g $RG \
  -f deploy/main.bicep \
  -p readerSubscriptionIds='["<sub-id-1>","<sub-id-2>"]' \
  -p authMode=entra \
  -p authClientId=$CLIENT_ID \
  -p authClientSecret='<your-client-secret>' \
  -p authApiScope="api://$CLIENT_ID/access_as_user" \
  -p authTenantId=$TENANT_ID
```

### Option B: Deploy to Azure button

Use the portal deployment form — the **Authentication (Entra ID)** step collects all required values.

### Post-deployment: add the redirect URI

After deployment, get the app URL and add it as a SPA redirect URI (see [step 6](#6-add-spa-redirect-uris)).

## 9. Local development

Create a `.env` file (see `.env.example`):

```env
AUTH_MODE=entra
AUTH_TENANT_ID=<your-tenant-id>
AUTH_CLIENT_ID=<your-client-id>
AUTH_CLIENT_SECRET=<your-client-secret>
AUTH_API_SCOPE=api://<your-client-id>/access_as_user
```

Start the server:

```bash
uv run az-scout web --port 5001
```

Open `http://localhost:5001` — click **Sign in** in the navbar.

> **Tip:** Set `AUTH_MODE=mock` to bypass authentication during development. In mock mode, no Entra ID configuration is needed and all API endpoints are accessible without a token.

## 10. MCP client authentication

When authentication is enabled, the MCP endpoint (`/mcp`) is also protected. MCP clients must include a bearer token.

### Obtain a token manually

```bash
# Get a token for the az-scout API scope
TOKEN=$(az account get-access-token \
  --resource "api://$CLIENT_ID" \
  --query accessToken -o tsv)
```

### Configure your MCP client

```json
{
  "mcpServers": {
    "az-scout": {
      "url": "https://<your-app-url>/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `AADSTS700054: response_type 'id_token' is not enabled` | App Registration has no SPA redirect URIs | Add SPA redirect URIs (step 6) — do not use Web platform |
| `AADSTS650056: Misconfigured application` | No API permissions or scope not exposed | Verify the `access_as_user` scope exists (step 3) |
| `AADSTS50011: Reply URL does not match` | Redirect URI mismatch | Ensure the exact URL (including scheme and port) is registered as a SPA redirect URI |
| `AADSTS65001: User has not consented` | Consent required for the scope | Pre-authorize the app (step 4) or grant admin consent |
| `401 Unauthorized` on API calls | Token expired or missing | MSAL.js auto-refreshes; if persisting, sign out and sign in again |
| `AUTH_MODE=entra requires AUTH_TENANT_ID, AUTH_CLIENT_ID` | Missing environment variables | Set the required env vars (see step 8/9) |

## Quick reference

| Value | Environment variable | Bicep parameter | Where to find it |
|---|---|---|---|
| Tenant ID | `AUTH_TENANT_ID` | `authTenantId` | Portal → Entra ID → Overview |
| Client ID | `AUTH_CLIENT_ID` | `authClientId` | Portal → App registrations → Overview |
| Client Secret | `AUTH_CLIENT_SECRET` | `authClientSecret` | Portal → App registrations → Certificates & secrets |
| API Scope | `AUTH_API_SCOPE` | `authApiScope` | `api://<clientId>/access_as_user` |
| Auth Mode | `AUTH_MODE` | `authMode` | `entra` or `none` |

## Complete script

Here is a single script that performs all steps:

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
APP_NAME="az-scout"
RG="rg-az-scout"
LOCATION="swedencentral"
READER_SUBS='["<sub-id-1>"]'   # JSON array of subscription IDs

# --- 1. Create App Registration ---
TENANT_ID=$(az account show --query tenantId -o tsv)
CLIENT_ID=$(az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)
echo "Client ID: $CLIENT_ID"

# --- 2. Create Client Secret ---
CLIENT_SECRET=$(az ad app credential reset \
  --id "$CLIENT_ID" \
  --display-name "${APP_NAME}-secret" \
  --years 2 \
  --query password -o tsv)
echo "Client Secret: $CLIENT_SECRET (save this!)"

# --- 3. Expose API scope ---
SCOPE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
az ad app update --id "$CLIENT_ID" \
  --identifier-uris "api://$CLIENT_ID" \
  --set "api.oauth2PermissionScopes=[{ \
    \"id\": \"$SCOPE_ID\", \
    \"adminConsentDescription\": \"Access az-scout API on behalf of the signed-in user\", \
    \"adminConsentDisplayName\": \"Access az-scout\", \
    \"userConsentDescription\": \"Access az-scout API on your behalf\", \
    \"userConsentDisplayName\": \"Access az-scout\", \
    \"isEnabled\": true, \
    \"type\": \"User\", \
    \"value\": \"access_as_user\" \
  }]"

# --- 4. Pre-authorize ---
az ad app update --id "$CLIENT_ID" \
  --set "api.preAuthorizedApplications=[{ \
    \"appId\": \"$CLIENT_ID\", \
    \"delegatedPermissionIds\": [\"$SCOPE_ID\"] \
  }]"

# --- 5. Create Service Principal ---
az ad sp create --id "$CLIENT_ID"

# --- 6. Add local SPA redirect URIs ---
az ad app update --id "$CLIENT_ID" \
  --spa-redirect-uris \
    "http://localhost:5001" \
    "http://127.0.0.1:5001"

# --- 7. Deploy ---
az group create -n "$RG" -l "$LOCATION"
az deployment group create \
  -g "$RG" \
  -f deploy/main.bicep \
  -p readerSubscriptionIds="$READER_SUBS" \
  -p authMode=entra \
  -p authClientId="$CLIENT_ID" \
  -p authClientSecret="$CLIENT_SECRET" \
  -p authApiScope="api://$CLIENT_ID/access_as_user" \
  -p authTenantId="$TENANT_ID"

# --- 8. Add deployed URL as redirect URI ---
APP_URL=$(az deployment group show \
  -g "$RG" -n main \
  --query "properties.outputs.appUrl.value" -o tsv)
az ad app update --id "$CLIENT_ID" \
  --spa-redirect-uris \
    "http://localhost:5001" \
    "http://127.0.0.1:5001" \
    "$APP_URL"

echo ""
echo "=== Done ==="
echo "App URL:       $APP_URL"
echo "Client ID:     $CLIENT_ID"
echo "Tenant ID:     $TENANT_ID"
echo "API Scope:     api://$CLIENT_ID/access_as_user"
```
