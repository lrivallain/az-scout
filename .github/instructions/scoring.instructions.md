---
description: "Deployment Confidence Score formulas, weights, and invariants. USE WHEN editing src/az_scout/scoring/."
applyTo: "src/az_scout/scoring/**,tests/test_deployment_confidence.py"
---

# Scoring (Deployment Confidence)

`src/az_scout/scoring/deployment_confidence.py` is the **single source of truth**
for the Deployment Confidence Score. The web UI, MCP server, and every plugin
must use `compute_deployment_confidence` — no client-side recomputation.

## Signals & weights (must sum to 1.0)

| Signal | Weight | Meaning |
|---|---|---|
| `quotaPressure` | 0.25 | Demand-adjusted, non-linear quota pressure |
| `spot` | 0.35 | Azure Spot Placement Score label |
| `zones` | 0.15 | Available (non-restricted) AZ breadth |
| `restrictionDensity` | 0.15 | Fraction of zones not restricted |
| `pricePressure` | 0.10 | Spot-to-PAYGO price ratio |

If you change a weight, **update every weight** so the sum stays exactly 1.0
and update the doc strings in the module header.

## Score types

- **Basic** — `spot` excluded, remaining four weights renormalized. Default for SKU listings.
- **Basic + Spot** — all five signals; activated by passing `spot_score_label` to `signals_from_sku`.
- **Blocked** — set when `_check_knockouts` returns reasons (overrides score to 0).

## Knockouts (force score = 0, label = "Blocked")

- `quota_remaining_vcpu < vcpus × instance_count`
- `zones_available_count == 0`

Adding a knockout requires updating: `_check_knockouts`, the module docstring,
`docs/scoring.md`, and tests in `test_deployment_confidence.py`.

## Label thresholds (top-down, first match wins)

```
>= 80 → High
>= 60 → Medium
>= 40 → Low
<  40 → Very Low
< MIN_SIGNALS available → Unknown (score = 0)
```

## Invariants (never violate)

1. **Determinism**: same inputs → same outputs (excluding `provenance.computedAtUtc`).
2. **Renormalization**: missing signals are excluded; remaining weights are
   renormalized so `weight_effective_i = weight_i / Σ(available weights)`.
3. **Disclaimers always included**: every result returns the full `DISCLAIMERS` list.
4. **Pydantic models are public API**: don't rename or remove fields without a
   `PLUGIN_API_VERSION` bump and a CHANGELOG migration note.
5. **MIN_SIGNALS = 2**: below this, return `label="Unknown"` (or `"Blocked"` if knockouts apply).

## Don't

- Don't compute a score in JS or in plugin code. Always call `compute_deployment_confidence`.
- Don't bypass `signals_from_sku` for SKU inputs — it normalizes raw API shapes.
- Don't make any field non-optional on `DeploymentSignals` — every signal must remain
  independently optional so partial inputs still produce a meaningful score.
- Don't hide a signal silently when it's "definitively bad" (e.g. spot
  `RestrictedSkuNotAvailable` is normalized to `0.0`, not `None`).

## Tests

- `tests/test_deployment_confidence.py` covers every normalizer, every knockout,
  and the renormalization edge cases.
- For any formula change, add a test asserting the new score for a representative input
  before changing the formula (red → green discipline).
