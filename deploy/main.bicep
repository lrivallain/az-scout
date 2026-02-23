// ---------------------------------------------------------------------------
// Azure Scout – Container App deployment with optional authentication
// ---------------------------------------------------------------------------
//
// Deploys:
//   1. Log Analytics workspace
//   2. Container Apps Environment
//   3. Container App running az-scout from GHCR
//   4. User-assigned Managed Identity with Reader on target subscriptions
//   5. (Optional) Entra ID authentication (fastapi-azure-auth + MSAL.js)
//
// Usage (no auth):
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p readerSubscriptionIds='["<sub-id-1>","<sub-id-2>"]'
//
// With Entra ID (fastapi-azure-auth + MSAL.js):
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p readerSubscriptionIds='["<sub-id-1>"]' \
//     -p authMode=entra \
//     -p authClientId=<app-registration-client-id> \
//     -p authClientSecret=<secret> \
//     -p authApiScope='api://<client-id>/access_as_user'
//
// ---------------------------------------------------------------------------

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Base name used as prefix for all resources.')
param baseName string = 'az-scout'

@description('Full container image reference (e.g. "ghcr.io/lrivallain/az-scout:latest", "ghcr.io/lrivallain/az-scout:2026.2.5"). Override to use a private registry.')
param containerImage string = 'ghcr.io/lrivallain/az-scout:latest'

@description('CPU cores allocated to the container (e.g. "0.5", "1.0").')
param containerCpu string = '0.5'

@description('Memory allocated to the container (e.g. "1.0Gi").')
param containerMemory string = '1.0Gi'

@description('Minimum number of replicas (0 allows scale-to-zero).')
@minValue(0)
@maxValue(10)
param minReplicas int = 0

@description('Maximum number of replicas.')
@minValue(1)
@maxValue(10)
param maxReplicas int = 2

@description('Subscription IDs to grant Reader access to the managed identity. Pass as a JSON array.')
param readerSubscriptionIds array = []

@description('Assign Virtual Machine Contributor role for Spot Placement Scores. Set to false if you don\'t need spot scores.')
param enableSpotScoreRole bool = true

// -- Authentication parameters --

@description('Entra ID App Registration Client ID (required when authMode is "entra").')
param authClientId string = ''

@secure()
@description('Entra ID App Registration Client Secret (required when authMode is "entra").')
param authClientSecret string = ''

@description('Entra ID tenant ID for authentication. Defaults to the deployment tenant.')
param authTenantId string = tenant().tenantId

@description('Authentication mode: "entra" (fastapi-azure-auth + MSAL.js) or "none" (no authentication).')
@allowed(['none', 'entra'])
param authMode string = 'none'

@description('API scope URI exposed by the App Registration (e.g. api://<clientId>/access_as_user). Required when authMode is "entra".')
param authApiScope string = ''

// ---------------------------------------------------------------------------
// Managed Identity
// ---------------------------------------------------------------------------

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${baseName}-identity'
  location: location
}

// ---------------------------------------------------------------------------
// Reader role assignments on target subscriptions
// ---------------------------------------------------------------------------

// Role definition ID for "Reader"
var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

@description('Assign Reader role to the managed identity on each target subscription.')
module readerAssignments 'modules/subscription-reader.bicep' = [
  for (subId, i) in readerSubscriptionIds: {
    name: 'reader-${i}'
    scope: subscription(subId)
    params: {
      principalId: managedIdentity.properties.principalId
      roleDefinitionId: readerRoleId
    }
  }
]

// ---------------------------------------------------------------------------
// Spot Score Reader – custom role for Spot Placement Scores (optional)
// ---------------------------------------------------------------------------

@description('Assign Virtual Machine Contributor for Spot Placement Scores on each target subscription.')
module spotScoreRole 'modules/subscription-spot-score.bicep' = [
  for (subId, i) in readerSubscriptionIds: if (enableSpotScoreRole) {
    name: 'spot-score-${i}'
    scope: subscription(subId)
    params: {
      principalId: managedIdentity.properties.principalId
    }
  }
]

// ---------------------------------------------------------------------------
// Log Analytics + Container Apps Environment
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${baseName}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${baseName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container App
// ---------------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: baseName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: []   // GHCR public images don't need credentials
      secrets: authMode == 'entra' ? [
        {
          name: 'auth-client-secret'
          value: authClientSecret
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'az-scout'
          image: containerImage
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }
          env: union(
            [
              {
                // Tell azure-identity to use the user-assigned MI
                name: 'AZURE_CLIENT_ID'
                value: managedIdentity.properties.clientId
              }
            ],
            authMode == 'entra' ? [
              {
                name: 'AUTH_MODE'
                value: 'entra'
              }
              {
                name: 'AUTH_TENANT_ID'
                value: authTenantId
              }
              {
                name: 'AUTH_CLIENT_ID'
                value: authClientId
              }
              {
                name: 'AUTH_API_SCOPE'
                value: authApiScope
              }
              {
                name: 'AUTH_CLIENT_SECRET'
                secretRef: 'auth-client-secret'
              }
            ] : [
              {
                name: 'AUTH_MODE'
                value: 'mock'
              }
            ]
          )
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Container App FQDN (the public URL).')
output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'

@description('Managed Identity principal ID (use for additional RBAC).')
output identityPrincipalId string = managedIdentity.properties.principalId

@description('Managed Identity client ID.')
output identityClientId string = managedIdentity.properties.clientId

@description('Open the app in the browser with: az webapp browse --ids <containerAppId>')
output containerAppId string = containerApp.id

@description('Log Analytics workspace ID (for diagnostics queries).')
output logAnalyticsId string = logAnalytics.id
