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

// Required: tag of the container image to deploy
param containerImageTag = 'latest'

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

// Optional: enable Entra ID authentication (EasyAuth)
// param enableAuth = true
// param authClientId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
// param authClientSecret = '<secret-from-az-ad-app-credential-reset>'
// param authTenantId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
