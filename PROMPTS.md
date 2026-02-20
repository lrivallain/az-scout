# Prompt examples

Example natural-language prompts you can use with an AI agent (Claude, Copilot, etc.) connected to the az-scout MCP server.

## Discovery

- Which Azure tenants can I access? Am I authenticated to all of them?
- List my Azure subscriptions in the Contoso LRI tenant.
- Which Azure regions support Availability Zones?

## Zone mappings

- Compare the zone mappings for my two production subscriptions in West Europe. Will zone 1 land on the same physical datacenter?
- Show me the logical-to-physical zone mapping for all my subscriptions in France Central.
- I have three subscriptions. In which region do they all share the same physical zone for logical zone 2?

## SKU availability & quota

- Is Standard_D4s_v5 available in all zones in France Central? Do I have enough vCPU quota to deploy 8 instances?
- Show me all VMs with 4–8 vCPUs and at least 16 GB RAM available in Sweden Central.
- Which D-series v5 SKUs are unrestricted in East US zone 3?
- How much vCPU quota do I have left for the Dv5 family in West Europe?

## Pricing

- Give me the full pricing breakdown for Standard_E8s_v5 in North Europe — PayGo, Spot, Reserved and Savings Plan.
- Compare the hourly cost of Standard_D4s_v5 vs Standard_D4as_v5 in West Europe, including Spot pricing.
- What is the cheapest 4-vCPU VM I can run in France Central with at least 16 GB RAM? Show prices in EUR.

## Spot placement

- What are the Spot placement scores for Standard_D2s_v3 and Standard_E4s_v5 in East US?
- Which of these SKUs has the best chance of getting a Spot VM in West Europe: D2s_v5, D4s_v5, E2s_v5?

## Multi-step planning

- I need to deploy Standard_DC1s_v3 in UK South across two subscriptions. Check zone mappings and SKU availability to recommend which zones to target so both subscriptions land on the same physical datacenter.
- Find a 4-vCPU VM available in all three zones of France Central with Spot pricing under $0.05/hr and enough quota to run 10 instances.
- I want to deploy a GPU VM in Sweden Central. List available N-series SKUs, check my quota, and show pricing for the cheapest option.
- Compare D4s_v5 availability, zone mappings, quota and pricing between West Europe and North Europe for my subscription, then recommend the best region.
- I'm planning a multi-region deployment across France Central and West Europe. For each region, find all E-series VMs with 8+ vCPUs, check zone availability and restrictions, and summarize which SKUs are available everywhere.
