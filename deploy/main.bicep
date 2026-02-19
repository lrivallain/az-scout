// ---------------------------------------------------------------------------
// Azure Scout – Container App deployment with optional EasyAuth
// ---------------------------------------------------------------------------
//
// Deploys:
//   1. Log Analytics workspace
//   2. Container Apps Environment
//   3. Container App running az-scout from GHCR
//   4. User-assigned Managed Identity with Reader on target subscriptions
//   5. (Optional) Entra ID EasyAuth via authConfigs
//
// Usage:
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p containerImageTag=latest \
//     -p readerSubscriptionIds='["<sub-id-1>","<sub-id-2>"]'
//
// With EasyAuth:
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p containerImageTag=latest \
//     -p readerSubscriptionIds='["<sub-id-1>"]' \
//     -p enableAuth=true \
//     -p authClientId=<app-registration-client-id>
// ---------------------------------------------------------------------------

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Base name used as prefix for all resources.')
param baseName string = 'az-scout'

@description('Container image tag to deploy (e.g. "latest", "2026.2.5").')
param containerImageTag string = 'latest'

@description('Full container image reference. Override to use a private registry.')
param containerImage string = 'ghcr.io/lrivallain/az-scout:${containerImageTag}'

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

// -- EasyAuth parameters --

@description('Enable Entra ID authentication (EasyAuth) on the Container App.')
param enableAuth bool = false

@description('Entra ID App Registration Client ID (required when enableAuth is true).')
param authClientId string = ''

@secure()
@description('Entra ID App Registration Client Secret (required when enableAuth is true).')
param authClientSecret string = ''

@description('Entra ID tenant ID for authentication. Defaults to the deployment tenant.')
param authTenantId string = tenant().tenantId

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
      secrets: enableAuth ? [
        {
          name: 'microsoft-provider-authentication-secret'
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
          env: [
            {
              // Tell azure-identity to use the user-assigned MI
              name: 'AZURE_CLIENT_ID'
              value: managedIdentity.properties.clientId
            }
          ]
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
// EasyAuth (optional) – Entra ID authentication
// ---------------------------------------------------------------------------

resource authConfig 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (enableAuth) {
  parent: containerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    identityProviders: {
      azureActiveDirectory: {
        registration: {
          clientId: authClientId
          clientSecretSettingName: 'microsoft-provider-authentication-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${authTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${authClientId}'
          ]
        }
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
