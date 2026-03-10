# Enable Entra ID Authentication (EasyAuth)

This guide walks through creating an Entra ID App Registration and configuring EasyAuth on your az-scout Container App.

## Quick start (recommended)

The [`setup-easyauth.sh`](https://github.com/az-scout/az-scout/blob/main/deploy/setup-easyauth.sh) script automates all the steps below. It is **idempotent** — it detects existing configuration at every step and only creates what is missing, so it is safe to re-run at any time.

The script works in **two phases** because the Container App URL is only known after deployment:

### Phase 1 — before deploying (no URL needed)

Creates the App Registration, Service Principal, client secret, and optionally the MCP API scope:

```bash
# Prerequisites: az CLI logged in, jq installed

# Create App Registration + secret
./deploy/setup-easyauth.sh

# With MCP API scope (for bearer-token access)
./deploy/setup-easyauth.sh --enable-mcp
```

The script prints the **Client ID** and **Secret** — use them in the Bicep deployment:

```bash
az deployment group create \
  -g rg-az-scout \
  -f deploy/main.bicep \
  -p readerSubscriptionIds='["<sub-id>"]' \
  -p enableAuth=true \
  -p authClientId='<client-id>' \
  -p authClientSecret='<secret>'
```

### Phase 2 — after deploying (adds redirect URIs)

Re-run the script with the Container App URL to add the EasyAuth callback redirect URI:

```bash
# Provide the URL explicitly
./deploy/setup-easyauth.sh \
  --app-url https://az-scout.<env>.<region>.azurecontainerapps.io

# Or auto-detect from the resource group
./deploy/setup-easyauth.sh --resource-group rg-az-scout

# Full — also add VS Code redirect URIs for interactive MCP login
./deploy/setup-easyauth.sh \
  --resource-group rg-az-scout \
  --enable-mcp \
  --enable-vscode
```

Already-configured items (App Registration, secret, scopes, etc.) are detected and skipped.

### Options reference

| Flag | What it does |
|---|---|
| `--app-url URL` | Container App URL — enables redirect URI configuration |
| `--resource-group RG` | Auto-detect `--app-url` from an existing Container App deployment |
| `--app-name NAME` | Display name for the App Registration (default: `az-scout`) |
| `--enable-mcp` | Exposes an API scope, pre-authorizes the Azure CLI, and grants admin consent (steps 7a–7c below) |
| `--enable-vscode` | Adds VS Code redirect URIs and enables public client flows for interactive OAuth login |
| `--rotate-secret` | Forces creation of a new client secret even when one already exists |
| `--quiet` | Suppresses informational output |

The sections below document each manual step for reference or troubleshooting.

---

## Manual steps

### Prerequisites

- Azure CLI (`az`) authenticated with permissions to create App Registrations
- An already-deployed az-scout Container App (see [main README](https://github.com/az-scout/az-scout/blob/main/README.md#deploy-to-azure-container-app))
- Your Container App URL (e.g. `https://az-scout.<env>.<region>.azurecontainerapps.io`)

### 1. Set variables

```bash
# Your Container App FQDN (from the deployment output)
APP_URL="https://az-scout.<env>.<region>.azurecontainerapps.io"

# Display name for the App Registration
APP_NAME="az-scout"
```

### 2. Create the App Registration

```bash
APP_ID=$(az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "${APP_URL}/.auth/login/aad/callback" \
  --enable-id-token-issuance true \
  --query appId -o tsv)

echo "Client ID: $APP_ID"
```

> **Note:** `--enable-id-token-issuance true` is required — Container Apps EasyAuth uses the `id_token` implicit grant flow.

### 3. Create a client secret

```bash
APP_SECRET=$(az ad app credential reset \
  --id "$APP_ID" \
  --display-name "az-scout-easyauth" \
  --query password -o tsv)

echo "Client Secret: $APP_SECRET"
```

> **Important:** Save this secret immediately — it cannot be retrieved later.

### 4. Create the Service Principal (Enterprise Application)

The Service Principal is the identity object in your tenant that controls user access:

```bash
az ad sp create --id "$APP_ID"
```

### 5. Deploy with EasyAuth enabled

```bash
az deployment group create \
  -g rg-az-scout \
  -f deploy/main.bicep \
  -p readerSubscriptionIds='["SUB_ID_1","SUB_ID_2"]' \
  -p enableAuth=true \
  -p authClientId="$APP_ID" \
  -p authClientSecret="$APP_SECRET"
```

### 6. Restrict access to specific users (optional)

By default, any user in your Entra ID tenant can sign in. To restrict access to specific users or groups:

#### Enable assignment requirement

```bash
SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

az ad sp update --id "$SP_OBJECT_ID" \
  --set appRoleAssignmentRequired=true
```

#### Assign a user

```bash
USER_OBJECT_ID=$(az ad user show --id user@example.com --query id -o tsv)

az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_OBJECT_ID/appRoleAssignments" \
  --body "{
    \"principalId\": \"$USER_OBJECT_ID\",
    \"resourceId\": \"$SP_OBJECT_ID\",
    \"appRoleId\": \"00000000-0000-0000-0000-000000000000\"
  }"
```

The `appRoleId` of all-zeros is the built-in "Default Access" role.

#### Assign a group

```bash
GROUP_OBJECT_ID=$(az ad group show --group "My Group" --query id -o tsv)

az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_OBJECT_ID/appRoleAssignments" \
  --body "{
    \"principalId\": \"$GROUP_OBJECT_ID\",
    \"resourceId\": \"$SP_OBJECT_ID\",
    \"appRoleId\": \"00000000-0000-0000-0000-000000000000\"
  }"
```

### Troubleshooting

| Error | Fix |
|---|---|
| `AADSTS700054: response_type 'id_token' is not enabled` | Run `az ad app update --id $APP_ID --set web/implicitGrantSettings/enableIdTokenIssuance=true` |
| `AADSTS700016: application was not found` | Verify `authClientId` matches the App Registration and it's in the correct tenant |
| `AADSTS50105: admin has not granted consent` | Assignment is required but the user is not assigned — see step 6 |
| "Assignment required?" toggle is greyed out in the portal | Use the CLI command in step 6 instead |
| `Resource does not exist` when querying the SP | Create it first with `az ad sp create --id $APP_ID` |
| `AADSTS65001: The user or administrator has not consented to use the application` | The App Registration must expose an API and pre-authorize the Azure CLI — see [step 7](#7-connect-mcp-clients-through-easyauth) |
| VS Code asks "Enter an existing client ID" with redirect URIs | Enter your az-scout App Registration client ID, then add those redirect URIs and enable public client flows — see the "VS Code Copilot (recommended – interactive login)" section below |
| 401 Unauthorized with valid token | Ensure the `openIdIssuer` does **not** end with `/v2.0` — the Azure CLI issues v1 tokens. Use `https://login.microsoftonline.com/<TENANT_ID>/` |
| 403 Forbidden with valid token | Remove `defaultAuthorizationPolicy.allowedApplications` from the auth config if empty, or explicitly add the Azure CLI app ID (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`) |

### 7. Connect MCP clients through EasyAuth

When EasyAuth is enabled, the MCP endpoint (`/mcp`) is also protected. Browser-based access handles login automatically via redirects, but programmatic MCP clients (VS Code Copilot, Claude Desktop, etc.) must pass a bearer token in the request headers.

#### Expose an API and pre-authorize the Azure CLI

Before you can obtain tokens with `az account get-access-token`, your App Registration must expose an API scope and pre-authorize the Azure CLI as a client application.

##### a. Add an Application ID URI and a `user_impersonation` scope

```bash
# Set the Application ID URI
az ad app update --id "$APP_ID" \
  --identifier-uris "api://$APP_ID"

# Get the object ID (different from appId)
APP_OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

# Generate a unique ID for the scope
SCOPE_ID=$(uuidgen)

# Add the user_impersonation scope
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --body "{
    \"api\": {
      \"oauth2PermissionScopes\": [{
        \"adminConsentDescription\": \"Access az-scout\",
        \"adminConsentDisplayName\": \"Access az-scout\",
        \"id\": \"$SCOPE_ID\",
        \"isEnabled\": true,
        \"type\": \"User\",
        \"userConsentDescription\": \"Access az-scout on your behalf\",
        \"userConsentDisplayName\": \"Access az-scout\",
        \"value\": \"user_impersonation\"
      }]
    }
  }"

echo "Scope ID: $SCOPE_ID"
```

##### b. Pre-authorize the Azure CLI

The Azure CLI has a well-known App ID: `04b07795-8ddb-461a-bbee-02f9e1bf7b46`. Pre-authorizing it allows `az account get-access-token` to work without interactive consent:

```bash
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --body "{
    \"api\": {
      \"preAuthorizedApplications\": [{
        \"appId\": \"04b07795-8ddb-461a-bbee-02f9e1bf7b46\",
        \"delegatedPermissionIds\": [\"$SCOPE_ID\"]
      }]
    }
  }"
```

> **Tip:** You can verify the configuration in the Azure Portal under **Entra ID > App registrations > az-scout > Expose an API**. You should see `user_impersonation` listed with the Azure CLI as an authorized client application.

##### c. Grant admin consent for the Azure CLI

The pre-authorization above tells Entra ID *which* scopes the Azure CLI may request, but a **delegated permission grant** (admin consent) is still required so that users are not prompted for interactive consent:

```bash
# Get the Azure CLI's service principal object ID in your tenant
CLI_SP_ID=$(az ad sp show --id 04b07795-8ddb-461a-bbee-02f9e1bf7b46 --query id -o tsv)

# Get your app's service principal object ID
APP_SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

# Create an OAuth2 permission grant (admin consent for all users)
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
  --body "{
    \"clientId\": \"$CLI_SP_ID\",
    \"consentType\": \"AllPrincipals\",
    \"resourceId\": \"$APP_SP_ID\",
    \"scope\": \"user_impersonation\"
  }"
```

> **Note:** This requires the `DelegatedPermissionGrant.ReadWrite.All` or `Directory.ReadWrite.All` permission. If you are a tenant admin this works out of the box.

#### Obtain a token

Use the Azure CLI to get an access token using the Application ID URI as the resource:

```bash
TOKEN=$(az account get-access-token \
  --resource "api://$APP_ID" \
  --query accessToken -o tsv)
```

> **Note:** Tokens are short-lived (typically 1 hour). You will need to refresh the token periodically.

#### VS Code Copilot (recommended – interactive login)

VS Code can handle Microsoft Entra ID login interactively via the MCP OAuth2 protocol.
The az-scout app includes OAuth2 proxy routes (`/authorize`, `/token`, `/.well-known/oauth-authorization-server`) that redirect to Entra ID — these are automatically excluded from EasyAuth validation by the Bicep deployment.

##### a. Register VS Code redirect URIs

```bash
# Add VS Code redirect URIs
az ad app update --id "$APP_ID" \
  --public-client-redirect-uris \
    "http://localhost" \
    "https://vscode.dev/redirect"

# Enable public client flows (required for desktop OAuth)
az ad app update --id "$APP_ID" \
  --is-fallback-public-client true
```

##### b. Create a client secret for VS Code

You can reuse the one created in [step 3](#3-create-a-client-secret), or create a dedicated one:

```bash
VSCODE_SECRET=$(az ad app credential reset \
  --id "$APP_ID" \
  --display-name "az-scout-vscode" \
  --query password -o tsv)

echo "VS Code Client Secret: $VSCODE_SECRET"
```

##### c. Configure the MCP server

Create a `.vscode/mcp.json` in your workspace:

```jsonc
{
  "servers": {
    "az-scout": {
      "type": "streamableHttp",
      "url": "https://az-scout.<env>.<region>.azurecontainerapps.io/mcp",
      "headers": {
        "Authorization": "Bearer ${microsoft_entra_id:<APP_ID>}"
      }
    }
  }
}
```

Replace `<APP_ID>` with your App Registration's client ID. When the MCP server starts, VS Code will prompt for:

1. **Client ID** — enter your App Registration's client ID (`$APP_ID`)
2. **Client Secret** — enter the secret from step 3 or the one created above

VS Code then opens a browser for interactive Entra ID login. Tokens are managed and refreshed automatically.

> **How it works:** VS Code discovers the OAuth2 metadata from `/.well-known/oauth-authorization-server`, which points `/authorize` and `/token` to the app's proxy routes. These routes redirect to Entra ID for the actual OAuth2 flow (PKCE). EasyAuth validates the resulting bearer token on `/mcp`.

#### VS Code Copilot (manual token)

If you prefer not to use the interactive flow, you can paste a token manually:

```jsonc
{
  "inputs": [
    {
      "type": "promptString",
      "id": "az-scout-token",
      "description": "Bearer token (run: az account get-access-token --resource api://<APP_ID> --query accessToken -o tsv)",
      "password": true
    }
  ],
  "servers": {
    "az-scout": {
      "type": "streamableHttp",
      "url": "https://az-scout.<env>.<region>.azurecontainerapps.io/mcp",
      "headers": {
        "Authorization": "Bearer ${input:az-scout-token}"
      }
    }
  }
}
```

To refresh an expired token, restart the MCP server (`MCP: List Servers` → restart) and paste a fresh token.

#### Claude Desktop / generic MCP clients

Add a `headers` block to your MCP client configuration:

```json
{
  "mcpServers": {
    "az-scout": {
      "url": "https://az-scout.<env>.<region>.azurecontainerapps.io/mcp",
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      }
    }
  }
}
```

Replace `<TOKEN>` with the output of `az account get-access-token --resource api://<APP_ID> --query accessToken -o tsv`.

#### Verify the token

You can test that your token works before configuring the MCP client:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://az-scout.<env>.<region>.azurecontainerapps.io/api/tenants"
```

A successful response confirms the token is valid and EasyAuth accepts it.

### Concepts

- **App Registration** — defines *what* your application is (client ID, redirect URIs, secrets). Found under **Entra ID > App registrations**.
- **Enterprise Application (Service Principal)** — controls *who* can access it (user assignments, conditional access). Auto-created when you run `az ad sp create`. Found under **Entra ID > Enterprise applications**.
- **EasyAuth** — Azure's built-in authentication middleware. It intercepts requests before they reach your app, handling login/logout/token validation at the platform level. No code changes needed.
