// ---------------------------------------------------------------------------
// Example parameter file for Azure Scout Container App deployment
// ---------------------------------------------------------------------------
// Copy this file to main.bicepparam and customise the values.
//
// Deploy with:
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p deploy/main.bicepparam
// ---------------------------------------------------------------------------

using 'main.bicep'

// Optional: override the container image (e.g. for a specific version or private registry)
// param containerImage = 'ghcr.io/lrivallain/az-scout:2026.2.5'

// Required: list of subscription IDs the managed identity should have Reader on
param readerSubscriptionIds = [
  // 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
  // 'yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy'
]

// Optional: override defaults
// param baseName = 'az-scout'
// param location = 'westeurope'
// param containerCpu = '0.5'
// param containerMemory = '1.0Gi'
// param minReplicas = 0
// param maxReplicas = 2

// Optional: disable the Virtual Machine Contributor role for Spot Placement Scores
// param enableSpotScoreRole = false

// Optional: enable Entra ID authentication
//
// Mode "entra" uses fastapi-azure-auth (app-level) + MSAL.js in the browser:
// param authMode = 'entra'
// param authClientId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
// param authClientSecret = '<secret-from-az-ad-app-credential-reset>'
// param authApiScope = 'api://xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/access_as_user'
// param authTenantId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
//
// Mode "easyauth" uses platform-level Container Apps authentication:
// param authMode = 'easyauth'
// param authClientId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
// param authClientSecret = '<secret-from-az-ad-app-credential-reset>'
// param authTenantId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
