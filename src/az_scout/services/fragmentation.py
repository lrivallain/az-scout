# Re-export from canonical location for backward compatibility.
from az_scout.scoring.fragmentation import (  # noqa: F401
    FragmentationResult,
    estimate_fragmentation_risk,
    fragmentation_to_normalized,
)

__all__ = [
    "FragmentationResult",
    "estimate_fragmentation_risk",
    "fragmentation_to_normalized",
]
