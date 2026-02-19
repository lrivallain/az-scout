// ---------------------------------------------------------------------------
// Module: Subscription-scoped role for Spot Placement Scores
// ---------------------------------------------------------------------------
// Assigns the "Virtual Machine Contributor" built-in role to the managed
// identity so it can POST to the Spot Placement Scores endpoint.
//
// A custom role with a narrower scope is not possible because Azure does not
// expose a granular action for this endpoint.

targetScope = 'subscription'

@description('Principal ID of the managed identity.')
param principalId string

// Built-in "Virtual Machine Contributor" role definition ID
var vmContributorRoleId = '9980e02c-c2be-4d73-94e8-173b1dc7cf3c'

// Assign the role to the managed identity
resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().subscriptionId, principalId, vmContributorRoleId)
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', vmContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}
