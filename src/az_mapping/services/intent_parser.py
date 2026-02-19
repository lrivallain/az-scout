"""Intent parser – deterministic derivation of technical requirements from intent.

Pure function: no I/O, no side effects, no Azure calls.
"""

import logging

from az_mapping.models.deployment_plan import (
    DeploymentIntentRequest,
    DerivedRequirements,
)

logger = logging.getLogger(__name__)


def derive_requirements(intent: DeploymentIntentRequest) -> DerivedRequirements:
    """Derive technical requirements from a deployment intent.

    Rules (deterministic):
    - requireZonal=True  → minZones=3
    - otherwise          → minZones=1
    - preferSpot=True OR urgency="now"  → requiresSpotScore=True
    - instanceCount > 0                 → requiresQuotaCheck=True
    - maxHourlyBudget set OR preferSpot OR currencyCode != "USD"
                                        → requiresPriceCheck=True
    """
    sku_constraints = intent.skuConstraints
    pricing = intent.pricing
    timing = intent.timing

    # --- minZones ---
    require_zonal = sku_constraints.requireZonal if sku_constraints else None
    min_zones: int = 3 if require_zonal else 1

    # --- requiresSpotScore ---
    prefer_spot = pricing.preferSpot if pricing else False
    urgency = timing.urgency if timing else "this_week"
    requires_spot_score = prefer_spot or urgency == "now"

    # --- requiresQuotaCheck ---
    requires_quota_check = intent.scale.instanceCount > 0

    # --- requiresPriceCheck ---
    max_budget_set = pricing is not None and pricing.maxHourlyBudget is not None
    currency_non_default = pricing is not None and pricing.currencyCode != "USD"
    requires_price_check = max_budget_set or prefer_spot or currency_non_default

    return DerivedRequirements(
        minZones=min_zones,  # type: ignore[arg-type]
        requiresSpotScore=requires_spot_score,
        requiresQuotaCheck=requires_quota_check,
        requiresPriceCheck=requires_price_check,
    )
