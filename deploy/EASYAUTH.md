# Enable Entra ID Authentication (EasyAuth)

This guide walks through creating an Entra ID App Registration and configuring EasyAuth on your az-scout Container App.

## Prerequisites

- Azure CLI (`az`) authenticated with permissions to create App Registrations
- An already-deployed az-scout Container App (see [main README](../README.md#deploy-to-azure-container-app))
- Your Container App URL (e.g. `https://az-scout.<env>.<region>.azurecontainerapps.io`)

## 1. Set variables

```bash
# Your Container App FQDN (from the deployment output)
APP_URL="https://az-scout.<env>.<region>.azurecontainerapps.io"

# Display name for the App Registration
APP_NAME="az-scout"
```

## 2. Create the App Registration

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

## 3. Create a client secret

```bash
APP_SECRET=$(az ad app credential reset \
  --id "$APP_ID" \
  --display-name "az-scout-easyauth" \
  --query password -o tsv)

echo "Client Secret: $APP_SECRET"
```

> **Important:** Save this secret immediately — it cannot be retrieved later.

## 4. Create the Service Principal (Enterprise Application)

The Service Principal is the identity object in your tenant that controls user access:

```bash
az ad sp create --id "$APP_ID"
```

## 5. Deploy with EasyAuth enabled

```bash
az deployment group create \
  -g rg-az-scout \
  -f deploy/main.bicep \
  -p containerImageTag=latest \
  -p readerSubscriptionIds='["SUB_ID_1","SUB_ID_2"]' \
  -p enableAuth=true \
  -p authClientId="$APP_ID" \
  -p authClientSecret="$APP_SECRET"
```

## 6. Restrict access to specific users (optional)

By default, any user in your Entra ID tenant can sign in. To restrict access to specific users or groups:

### Enable assignment requirement

```bash
SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

az ad sp update --id "$SP_OBJECT_ID" \
  --set appRoleAssignmentRequired=true
```

### Assign a user

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

### Assign a group

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

## Troubleshooting

| Error | Fix |
|---|---|
| `AADSTS700054: response_type 'id_token' is not enabled` | Run `az ad app update --id $APP_ID --set web/implicitGrantSettings/enableIdTokenIssuance=true` |
| `AADSTS700016: application was not found` | Verify `authClientId` matches the App Registration and it's in the correct tenant |
| `AADSTS50105: admin has not granted consent` | Assignment is required but the user is not assigned — see step 6 |
| "Assignment required?" toggle is greyed out in the portal | Use the CLI command in step 6 instead |
| `Resource does not exist` when querying the SP | Create it first with `az ad sp create --id $APP_ID` |

## Concepts

- **App Registration** — defines *what* your application is (client ID, redirect URIs, secrets). Found under **Entra ID > App registrations**.
- **Enterprise Application (Service Principal)** — controls *who* can access it (user assignments, conditional access). Auto-created when you run `az ad sp create`. Found under **Entra ID > Enterprise applications**.
- **EasyAuth** — Azure's built-in authentication middleware. It intercepts requests before they reach your app, handling login/logout/token validation at the platform level. No code changes needed.
