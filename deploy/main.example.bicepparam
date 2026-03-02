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

// Optional: disable persistent storage (enabled by default)
// param enablePersistentStorage = false
// param dataShareName = 'az-scout-data'

// Optional: enable VNet integration (locks down storage via private endpoint)
// param enableVnet = true
// param vnetAddressPrefix = '10.0.0.0/16'
// param infrastructureSubnetPrefix = '10.0.0.0/23'
// param privateEndpointSubnetPrefix = '10.0.2.0/27'

// Optional: enable Entra ID authentication (EasyAuth)
// param enableAuth = true
// param authClientId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
// param authClientSecret = '<secret-from-az-ad-app-credential-reset>'
// param authTenantId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
