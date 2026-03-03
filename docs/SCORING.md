# Deployment Confidence Scoring

> Canonical implementation in
> [`src/az_scout/scoring/deployment_confidence.py`](../src/az_scout/scoring/deployment_confidence.py).

The Deployment Confidence Score is a composite heuristic (0–100) that
estimates the likelihood of successfully deploying a VM SKU in a given
Azure region and subscription.  It is computed **server-side only** — the
frontend displays the score but never recomputes it.

## Score types

| Type | Signals | When used |
|---|---|---|
| **Basic** | quotaPressure, zones, restrictionDensity, pricePressure | Default — shown in the SKU table and modal |
| **Basic + Spot** | quotaPressure, **spot**, zones, restrictionDensity, pricePressure | When the user explicitly requests Spot inclusion |
| **Blocked** | *(all available signals computed but overridden)* | When a knockout condition makes deployment impossible |

The *Basic* score excludes the Spot Placement signal because it requires
an extra Azure API call with a specific instance count.  When the user
clicks **Include Spot** in the modal, the backend fetches Spot Placement
Scores and recomputes the confidence with all five signals.

The *Blocked* type is set when one or more **knockout conditions** fire
(see below).  The score is forced to 0 and the label to "Blocked",
regardless of the weighted signal computation.

## Signals

### 1. Quota Pressure (`quotaPressure`, weight 0.25)

Non-linear, demand-adjusted utilisation bands inspired by resource
allocation research ([Protean][protean], OSDI 2020).  Uses **projected** usage
`(used + vcpus × instance_count) / limit` to determine the pressure
band, plus a hard failure check against `remaining` and fleet size.

When `instance_count` is 1 (default), the score is pure supply-side
pressure.  When the caller provides a fleet size (e.g. the Deployment
Planner or "Recalculate with Spot" flow), the score reflects the
*additional demand* the deployment would place on the quota.

| Projected usage | Normalised value | Band |
|---|---|---|
| < 60 % | 1.0 | Healthy |
| < 80 % | 0.7 | Moderate |
| < 95 % | 0.3 | Danger |
| ≥ 95 % | 0.1 | Critical |
| Can't fit fleet | 0.0 | Hard failure |

The hard failure check (`remaining < vcpus × instance_count`) immediately
returns 0.0, regardless of the projected band.  For example, deploying
10 × 16-vCPU VMs into a quota with only 90 vCPU remaining triggers an
immediate hard failure.

### 2. Spot Placement (`spot`, weight 0.35)

| Azure label | Normalised value |
|---|---|
| High | 1.0 |
| Medium | 0.6 |
| Low | 0.25 |
| RestrictedSkuNotAvailable | 0.0 |
| Unknown | *missing* (excluded) |

Maps the best per-zone Spot Placement Score (from the Azure Spot
Placement Scores API) to a 0–1 value.  Only included in the
**Basic + Spot** score type.

When all zones return `RestrictedSkuNotAvailable`, the signal is
scored at 0.0 (definitively bad) rather than treated as missing data.
Only truly unknown labels (no data) are excluded.

### 3. Zone Breadth (`zones`, weight 0.15)

```
zones_available / 3  →  0..1
```

Counts the number of availability zones where the SKU is offered and not
restricted.  Three zones yields a perfect score.

### 4. Restriction Density (`restrictionDensity`, weight 0.15)

Per-zone granularity inspired by admission control research
([Kerveros][kerveros], OSDI 2023).  Measures the fraction of zones that are *not*
restricted:

```
1.0 - (restricted_zones_count / zones_total_count)
```

| Zones | Restricted | Value |
|---|---|---|
| 3 | 0 | 1.0 |
| 3 | 1 | 0.67 |
| 3 | 2 | 0.33 |
| 3 | 3 | 0.0 |
| 0 | 0 | *missing* |

Unlike v2's binary `restrictions` signal (0 or 1), this provides a
continuous score proportional to remaining deployment options.

### 5. Price Pressure (`pricePressure`, weight 0.10)

```
1.0 - (spot_price / paygo_price)  →  capped at [0, 1]
```

A lower spot-to-PAYGO ratio means better savings potential.  If spot or
PAYGO pricing is unavailable, this signal is treated as missing.

## Weights

| Signal | Weight |
|---|---|
| quotaPressure | 0.25 |
| spot | 0.35 |
| zones | 0.15 |
| restrictionDensity | 0.15 |
| pricePressure | 0.10 |
| **Total** | **1.00** |

## Knockout conditions

Knockout conditions represent **physically impossible** deployments —
situations where the Azure ARM API would deterministically reject the
request.  Unlike low signal scores (which reduce confidence), knockouts
force the overall result to `score=0`, `label="Blocked"`,
`scoreType="blocked"`.

The knockout gate runs *after* individual signal normalisation but
*before* the weighted sum is applied to the label.  Breakdown components
are still computed and included in the response, allowing users to
understand the underlying signal values even when the deployment is
blocked.

### Knockout rules

| Condition | Reason message |
|---|---|
| `remaining < vcpus × instance_count` | "Insufficient quota: X vCPUs remaining, Y required (Z × N)" |
| `zones_available_count == 0` | "No availability zones available (all zones restricted or SKU not offered)" |

### What is NOT a knockout

- **Spot restricted** — on-demand deployment is still possible.
- **95% quota usage** — risky but not impossible (scored 0.1 by
  `quotaPressure`, not blocked).
- **Missing signal data** — absence of data is handled by
  renormalisation, not by blocking.

Knockout reasons are returned in the `knockoutReasons` array of the
response.  An empty array means no knockouts were triggered.

## Renormalisation

When one or more signals are missing (e.g. spot is excluded in the Basic
score type, or pricing data is unavailable), the weights of the remaining
signals are renormalised so they sum to 1.0:

```
effective_weight_i = weight_i / Σ(available weights)
```

This ensures the score remains on the 0–100 scale regardless of how many
signals are present.

## Label mapping

| Score range | Label |
|---|---|
| ≥ 80 | High |
| ≥ 60 | Medium |
| ≥ 40 | Low |
| < 40 | Very Low |

If fewer than 2 signals are available, the result is
`label="Unknown", score=0`.

## API endpoints

### `POST /api/deployment-confidence`

Bulk scoring endpoint.  Accepts a list of SKU names and optionally
enables Spot inclusion:

```json
{
  "subscriptionId": "...",
  "region": "westeurope",
  "currencyCode": "USD",
  "preferSpot": true,
  "instanceCount": 1,
  "skus": ["Standard_D2s_v3", "Standard_D4s_v3"],
  "includeSignals": true,
  "includeProvenance": true
}
```

When `preferSpot` is `true`, the backend fetches Spot Placement Scores
and includes them in the computation, producing a `scoreType` of
`"basic+spot"`.

### MCP tool: `get_sku_deployment_confidence`

Dedicated scoring tool.  Accepts a list of SKU names, a region, and a
subscription ID.  Optionally enables Spot inclusion (`prefer_spot=True`)
and fleet sizing (`instance_count`).  Returns per-SKU confidence results
including breakdown, signals, and knockout reasons.

## Response schema

```json
{
  "score": 72,
  "label": "Medium",
  "scoreType": "basic",
  "breakdown": {
    "components": [
      {
        "name": "quotaPressure",
        "score01": 0.7,
        "score100": 70.0,
        "weight": 0.3846,
        "contribution": 0.2692,
        "status": "used"
      }
    ],
    "weightsOriginal": {
      "quotaPressure": 0.25,
      "spot": 0.35,
      "zones": 0.15,
      "restrictionDensity": 0.15,
      "pricePressure": 0.10
    },
    "weightsUsedSum": 0.65,
    "renormalized": true
  },
  "missingSignals": ["spot"],
  "knockoutReasons": [],
  "disclaimers": [
    "This is a heuristic estimate, not a guarantee of deployment success."
  ],
  "provenance": {
    "computedAtUtc": "2026-03-02T10:30:00+00:00"
  }
}
```

## Disclaimers

Every score result includes these disclaimers:

1. This is a heuristic estimate, not a guarantee of deployment success.
2. Signals are derived from Azure APIs and may change at any time.
3. No Microsoft guarantee is expressed or implied.

## References

[protean]: https://www.usenix.org/conference/osdi20/presentation/hadary "Protean: VM Allocation Service at Scale (OSDI 2020)"
[kerveros]: https://www.usenix.org/conference/osdi23/presentation/li-jiaqi "Kerveros: Efficient and Scalable Cloud Admission Control (OSDI 2023)"
