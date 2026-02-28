"""Fragmentation heuristic – estimates resource fragmentation risk.

This is a **heuristic estimate** based on observable SKU characteristics.
It is NOT a measurement of actual Azure datacenter fragmentation.

Higher fragmentation risk means the requested VM configuration is
harder to place due to scarce large contiguous capacity blocks.

Inspired by the Protean VM allocation service (Microsoft Research, 2020).
"""

from __future__ import annotations

from typing import Literal, TypedDict

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class FragmentationResult(TypedDict):
    score: float
    label: Literal["low", "medium", "high"]
    factors: list[str]
    disclaimer: str


# ---------------------------------------------------------------------------
# Heuristic weights
# ---------------------------------------------------------------------------

_FACTORS: list[tuple[str, str]] = [
    ("gpu", "GPU workload (+0.25)"),
    ("large_vcpu", "Large vCPU count ≥64 (+0.15)"),
    ("large_memory", "Large memory ≥512 GB (+0.15)"),
    ("zonal", "Zonal deployment required (+0.15)"),
    ("rdma", "RDMA / InfiniBand required (+0.10)"),
    ("ultrassd", "UltraSSD required (+0.05)"),
    ("spot_low", "Spot placement score Low (+0.10)"),
    ("price_pressure", "Price ratio >0.85 (+0.10)"),
]

_DISCLAIMER = (
    "This is a heuristic estimate of resource fragmentation risk. "
    "It does not reflect actual Azure datacenter state."
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def estimate_fragmentation_risk(
    *,
    vcpu: int | None = None,
    memory_gb: float | None = None,
    gpu_count: int | None = None,
    require_zonal: bool = False,
    rdma: bool = False,
    ultra_ssd: bool = False,
    spot_score: str | None = None,
    price_ratio: float | None = None,
) -> FragmentationResult:
    """Estimate fragmentation risk for a VM configuration.

    All parameters are optional.  Omitted parameters contribute 0 to
    the risk score.

    Args:
        vcpu: Number of vCPUs requested.
        memory_gb: Memory in GB requested.
        gpu_count: Number of GPUs (0 = no GPU).
        require_zonal: Whether zonal deployment is required.
        rdma: Whether RDMA / InfiniBand is needed.
        ultra_ssd: Whether UltraSSD is needed.
        spot_score: Spot Placement Score label (High / Medium / Low).
        price_ratio: Spot-to-PayGo price ratio (0–1).

    Returns a ``FragmentationResult`` with score (0–1), label, contributing
    factors, and a disclaimer.
    """
    risk = 0.0
    factors: list[str] = []

    if gpu_count is not None and gpu_count > 0:
        risk += 0.25
        factors.append("gpu")

    if vcpu is not None and vcpu >= 64:
        risk += 0.15
        factors.append("large_vcpu")

    if memory_gb is not None and memory_gb >= 512:
        risk += 0.15
        factors.append("large_memory")

    if require_zonal:
        risk += 0.15
        factors.append("zonal")

    if rdma:
        risk += 0.10
        factors.append("rdma")

    if ultra_ssd:
        risk += 0.05
        factors.append("ultrassd")

    if spot_score is not None and spot_score.lower() == "low":
        risk += 0.10
        factors.append("spot_low")

    if price_ratio is not None and price_ratio > 0.85:
        risk += 0.10
        factors.append("price_pressure")

    # Clamp
    risk = max(0.0, min(1.0, risk))

    # Label
    if risk >= 0.5:
        label: Literal["low", "medium", "high"] = "high"
    elif risk >= 0.25:
        label = "medium"
    else:
        label = "low"

    return FragmentationResult(
        score=round(risk, 2),
        label=label,
        factors=factors,
        disclaimer=_DISCLAIMER,
    )


def fragmentation_to_normalized(label: str) -> float | None:
    """Map a fragmentation label to a 0–1 normalized score.

    low → 1.0, medium → 0.6, high → 0.3, unknown → None
    """
    mapping: dict[str, float] = {"low": 1.0, "medium": 0.6, "high": 0.3}
    return mapping.get(label)
