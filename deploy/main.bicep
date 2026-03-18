// ---------------------------------------------------------------------------
// Azure Scout – Container App deployment with optional EasyAuth and OBO
// ---------------------------------------------------------------------------
//
// Deploys:
//   1. Log Analytics workspace
//   2. Container Apps Environment
//   3. Container App running az-scout from GHCR
//   4. User-assigned Managed Identity with Reader on target subscriptions
//   5. (Optional) VNet integration with network-isolated storage
//   6. (Optional) Entra ID EasyAuth via authConfigs
//   7. (Optional) OBO authentication for multi-user delegated access
//
// Usage:
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p readerSubscriptionIds='["<sub-id-1>","<sub-id-2>"]'
//
// With EasyAuth:
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p readerSubscriptionIds='["<sub-id-1>"]' \
//     -p enableAuth=true \
//     -p authClientId=<app-registration-client-id>
//
// With OBO:
//   az deployment group create \
//     -g <resource-group> \
//     -f deploy/main.bicep \
//     -p readerSubscriptionIds='["<sub-id-1>"]' \
//     -p enableObo=true \
//     -p oboClientId=<obo-client-id> \
//     -p oboClientSecret=<obo-client-secret>
// ---------------------------------------------------------------------------

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Base name used as prefix for all resources.')
param baseName string = 'az-scout'

@description('Full container image reference (e.g. "ghcr.io/az-scout/az-scout:latest", "ghcr.io/az-scout/az-scout:2026.2.5"). Override to use a private registry.')
param containerImage string = 'ghcr.io/az-scout/az-scout:latest'

@description('CPU cores allocated to the container (e.g. "0.5", "1.0").')
param containerCpu string = '0.5'

@description('Memory allocated to the container (e.g. "1.0Gi").')
param containerMemory string = '1.0Gi'

@description('Minimum number of replicas (0 allows scale-to-zero).')
@minValue(0)
@maxValue(10)
param minReplicas int = 1

@description('Maximum number of replicas.')
@minValue(1)
@maxValue(10)
param maxReplicas int = 2

@description('Subscription IDs to grant Reader access to the managed identity. Pass as a JSON array.')
param readerSubscriptionIds array = []

@description('Assign Virtual Machine Contributor role for Spot Placement Scores. Set to false if you don\'t need spot scores.')
param enableSpotScoreRole bool = true

// -- Persistent storage parameters --

@description('Enable persistent Azure Files storage. When true, a Storage Account and file share are created and mounted so that application data (plugins, caches) survives container restarts / scale-to-zero.')
param enablePersistentStorage bool = true

@description('Name of the Azure Files share used for application data.')
param dataShareName string = 'az-scout-data'

// -- VNet integration parameters --

@description('Deploy a VNet and inject the Container Apps Environment into it. When combined with enablePersistentStorage, the storage account is locked down via a private endpoint.')
param enableVnet bool = true

@description('Address space for the virtual network (must be at least /24 to accommodate the /23 infrastructure subnet).')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('CIDR range for the Container Apps infrastructure subnet (minimum /23).')
param infrastructureSubnetPrefix string = '10.0.0.0/23'

@description('CIDR range for the private endpoint subnet.')
param privateEndpointSubnetPrefix string = '10.0.2.0/27'

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

// -- OBO (On-Behalf-Of) parameters --

@description('Enable On-Behalf-Of authentication. Each user signs in with their own Microsoft account and ARM calls use their RBAC permissions. Requires a pre-created multi-tenant App Registration (see docs/deployment/obo-auth.md).')
param enableObo bool = false

@description('OBO App Registration Client ID (required when enableObo is true).')
param oboClientId string = ''

@secure()
@description('OBO App Registration Client Secret (required when enableObo is true).')
param oboClientSecret string = ''

@description('OBO App Registration home tenant ID. Defaults to the deployment tenant.')
param oboTenantId string = tenant().tenantId

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

// ---------------------------------------------------------------------------
// VNet + infrastructure subnet (optional)
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = if (enableVnet) {
  name: '${baseName}-vnet'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
  }
}

resource infraSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = if (enableVnet) {
  parent: vnet
  name: 'infrastructure'
  properties: {
    addressPrefix: infrastructureSubnetPrefix
    serviceEndpoints: [
      { service: 'Microsoft.Storage' }
    ]
    delegations: [
      {
        name: 'aca-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = if (enableVnet) {
  parent: vnet
  name: 'private-endpoints'
  properties: {
    addressPrefix: privateEndpointSubnetPrefix
  }
  dependsOn: [infraSubnet] // sequential subnet creation to avoid conflicts
}

// ---------------------------------------------------------------------------
// Storage Account + File Share for persistent data (optional)
// ---------------------------------------------------------------------------

resource dataStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (enablePersistentStorage) {
  name: replace('${baseName}data', '-', '')
  location: location
  tags: {
    SecurityControl: 'Ignore'
  }
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    networkAcls: enableVnet ? {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: [
        {
          id: infraSubnet.id
          action: 'Allow'
        }
      ]
    } : {
      defaultAction: 'Allow'
    }
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = if (enablePersistentStorage) {
  parent: dataStorage
  name: 'default'
}

resource dataShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = if (enablePersistentStorage) {
  parent: fileService
  name: dataShareName
  properties: {
    shareQuota: 1   // 1 GiB – increase if needed
  }
}

// ---------------------------------------------------------------------------
// Private endpoint + DNS for storage (when VNet is enabled)
// ---------------------------------------------------------------------------

resource storagePrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (enablePersistentStorage && enableVnet) {
  name: '${baseName}-storage-pe'
  location: location
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: '${baseName}-storage-pls'
        properties: {
          privateLinkServiceId: dataStorage.id
          groupIds: ['file']
        }
      }
    ]
  }
}

resource storageDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (enablePersistentStorage && enableVnet) {
  name: 'privatelink.file.${environment().suffixes.storage}'
  location: 'global'
}

resource storageDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (enablePersistentStorage && enableVnet) {
  parent: storageDnsZone
  name: '${baseName}-vnet-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource storageDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (enablePersistentStorage && enableVnet) {
  parent: storagePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'file'
        properties: {
          privateDnsZoneId: storageDnsZone.id
        }
      }
    ]
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
    vnetConfiguration: enableVnet ? {
      infrastructureSubnetId: infraSubnet.id
      internal: false
    } : null
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// Mount the Azure Files share into the Container Apps Environment
resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = if (enablePersistentStorage) {
  parent: containerEnv
  name: 'appdata'
  dependsOn: [storageDnsZoneGroup] // ensure private DNS is ready before mount
  properties: {
    azureFile: {
      accountName: dataStorage.name
      #disable-next-line BCP422 // safe: envStorage and dataStorage share the same condition
      accountKey: dataStorage.listKeys().keys[0].value
      shareName: dataShareName
      accessMode: 'ReadWrite'
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
      secrets: concat(
        enableAuth ? [
          {
            name: 'microsoft-provider-authentication-secret'
            value: authClientSecret
          }
        ] : [],
        enableObo ? [
          {
            name: 'obo-client-secret'
            value: oboClientSecret
          }
        ] : []
      )
    }
    template: {
      volumes: enablePersistentStorage ? [
        {
          name: 'app-data'
          storageName: envStorage.name
          storageType: 'AzureFile'
        }
      ] : []
      containers: [
        {
          name: 'az-scout'
          image: containerImage
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }
          env: concat(
            [
              {
                // Tell azure-identity to use the user-assigned MI
                name: 'AZURE_CLIENT_ID'
                value: managedIdentity.properties.clientId
              }
              {
                // Application data directory – points to mounted Azure Files
                // volume when persistent storage is enabled, else uses default
                name: 'AZ_SCOUT_DATA_DIR'
                value: enablePersistentStorage ? '/app/data' : ''
              }
            ],
            enableObo ? [
              {
                name: 'AZ_SCOUT_CLIENT_ID'
                value: oboClientId
              }
              {
                name: 'AZ_SCOUT_CLIENT_SECRET'
                secretRef: 'obo-client-secret'
              }
              {
                name: 'AZ_SCOUT_TENANT_ID'
                value: oboTenantId
              }
            ] : []
          )
          volumeMounts: enablePersistentStorage ? [
            {
              volumeName: 'app-data'
              mountPath: '/app/data'
            }
          ] : []
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
          openIdIssuer: '${environment().authentication.loginEndpoint}${authTenantId}/'
        }
        validation: {
          allowedAudiences: [
            'api://${authClientId}'
            authClientId
          ]
          defaultAuthorizationPolicy: {
            // Azure CLI app ID – required so `az account get-access-token`
            // tokens are accepted by EasyAuth for MCP/API bearer-token access.
            allowedApplications: [
              authClientId
              '04b07795-8ddb-461a-bbee-02f9e1bf7b46'   // Microsoft Azure CLI
            ]
          }
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

@description('Virtual Network ID (when VNet integration is enabled).')
output vnetId string = enableVnet ? vnet.id : ''
