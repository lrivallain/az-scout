---
description: "Deploy az-scout to Azure Container Apps (ACA), with deployment assets and EasyAuth guidance maintained in docs/."
---

# Azure Container Apps (ACA) Deployment

This page centralizes ACA deployment from the `deploy/` folder and keeps the original assets as the source of truth.

## Deployment assets

- Main Bicep template: [deploy/main.bicep](https://github.com/az-scout/az-scout/blob/main/deploy/main.bicep)
- Example parameters: [deploy/main.example.bicepparam](https://github.com/az-scout/az-scout/blob/main/deploy/main.example.bicepparam)
- Generated ARM JSON: [deploy/main.json](https://github.com/az-scout/az-scout/blob/main/deploy/main.json)
- Subscription reader module: [deploy/modules/subscription-reader.bicep](https://github.com/az-scout/az-scout/blob/main/deploy/modules/subscription-reader.bicep)
- Spot score module: [deploy/modules/subscription-spot-score.bicep](https://github.com/az-scout/az-scout/blob/main/deploy/modules/subscription-spot-score.bicep)
- Portal UI definition: [deploy/createUiDefinition.json](https://github.com/az-scout/az-scout/blob/main/deploy/createUiDefinition.json)
- EasyAuth automation script: [deploy/setup-easyauth.sh](https://github.com/az-scout/az-scout/blob/main/deploy/setup-easyauth.sh)

## EasyAuth guide

See the [EasyAuth guide](../deployment/easyauth.md) for a complete walkthrough of Entra ID authentication configuration.
