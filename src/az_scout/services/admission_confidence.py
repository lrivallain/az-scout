# Re-export from canonical location for backward compatibility.
from az_scout.scoring.admission_confidence import (  # noqa: F401
    ADMISSION_WEIGHTS,
    DISCLAIMERS,
    AdmissionConfidenceResult,
    SignalBreakdown,
    compute_admission_confidence,
)

__all__ = [
    "ADMISSION_WEIGHTS",
    "DISCLAIMERS",
    "AdmissionConfidenceResult",
    "SignalBreakdown",
    "compute_admission_confidence",
]
