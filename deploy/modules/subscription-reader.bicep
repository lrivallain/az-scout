// ---------------------------------------------------------------------------
// Module: Subscription-scoped Reader role assignment
// ---------------------------------------------------------------------------
// Deployed at subscription scope via the parent template.

targetScope = 'subscription'

@description('Principal ID of the managed identity.')
param principalId string

@description('Role definition ID to assign (Reader by default).')
param roleDefinitionId string

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().subscriptionId, principalId, roleDefinitionId)
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalType: 'ServicePrincipal'
  }
}
