"""Sustainability / CVaR reward helpers (Phase D) — offline research only.

Never grants execution_authority. Used to mix mean episode return with a
lower-tail CVaR objective for PPO / meta-RL batch scoring, and to score
agreement between AccountGrowthController throttles and degradation.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def cvar(
    values: Sequence[float],
    *,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Lower-tail Conditional Value at Risk of a return sample (research metric)."""
    if not _finite(alpha) or not (0.0 < float(alpha) <= 1.0):
        return {
            "status": "INVALID",
            "reason": "alpha must be in (0, 1]",
            "cvar": None,
            "execution_authority": False,
        }
    cleaned = [float(v) for v in values if _finite(v)]
    if not cleaned:
        return {
            "status": "INSUFFICIENT_DATA",
            "cvar": None,
            "n": 0,
            "execution_authority": False,
        }
    sorted_vals = sorted(cleaned)
    n_tail = max(1, int(math.ceil(float(alpha) * len(sorted_vals))))
    tail = sorted_vals[:n_tail]
    return {
        "status": "OK",
        "cvar": float(sum(tail) / len(tail)),
        "alpha": float(alpha),
        "n": len(cleaned),
        "n_tail": n_tail,
        "execution_authority": False,
        "kind": "descriptive_cvar",
    }


def cvar_mixture_objective(
    rewards: Sequence[float],
    *,
    alpha: float = 0.05,
    mean_weight: float = 0.5,
    cvar_weight: Optional[float] = None,
) -> Dict[str, Any]:
    """Mixture objective: λ·mean(R) + (1−λ)·CVaR_α(R).

    When ``cvar_weight`` is set it overrides ``1 - mean_weight``.
    """
    cleaned = [float(v) for v in rewards if _finite(v)]
    if not cleaned:
        return {
            "status": "INSUFFICIENT_DATA",
            "objective": 0.0,
            "mean": None,
            "cvar": None,
            "execution_authority": False,
        }
    if not _finite(mean_weight):
        return {
            "status": "INVALID",
            "reason": "mean_weight must be finite",
            "objective": 0.0,
            "execution_authority": False,
        }
    lam = max(0.0, min(1.0, float(mean_weight)))
    if cvar_weight is not None and _finite(cvar_weight):
        w_c = max(0.0, float(cvar_weight))
        total = lam + w_c
        if total <= 0:
            return {
                "status": "INVALID",
                "reason": "weights must sum > 0",
                "objective": 0.0,
                "execution_authority": False,
            }
        lam = lam / total
        w_c = w_c / total
    else:
        w_c = 1.0 - lam

    mean_r = sum(cleaned) / len(cleaned)
    cvar_out = cvar(cleaned, alpha=alpha)
    cvar_r = float(cvar_out.get("cvar") or 0.0)
    objective = lam * mean_r + w_c * cvar_r
    return {
        "status": "OK",
        "objective": float(objective),
        "mean": float(mean_r),
        "cvar": cvar_r,
        "mean_weight": float(lam),
        "cvar_weight": float(w_c),
        "alpha": float(alpha),
        "n": len(cleaned),
        "execution_authority": False,
        "kind": "cvar_mixture_objective",
    }


def throttle_agreement_reward(
    *,
    degradation: Optional[float],
    throttle_applied: bool,
    degrade_threshold: float = 0.25,
    agreement_bonus: float = 0.05,
    miss_penalty: float = 0.05,
) -> Dict[str, Any]:
    """Reward QuantitativeCoach when throttle matches degradation signal."""
    if degradation is None or not _finite(degradation):
        return {
            "status": "NO_SIGNAL",
            "value": 0.0,
            "execution_authority": False,
            "kind": "throttle_agreement",
        }
    if not all(_finite(v) for v in (degrade_threshold, agreement_bonus, miss_penalty)):
        return {
            "status": "INVALID",
            "value": 0.0,
            "execution_authority": False,
            "kind": "throttle_agreement",
        }
    should_throttle = float(degradation) >= float(degrade_threshold)
    if should_throttle and throttle_applied:
        value = float(agreement_bonus)
        reason = "throttle_when_degraded"
    elif should_throttle and not throttle_applied:
        value = -float(miss_penalty)
        reason = "missed_throttle_on_degradation"
    elif (not should_throttle) and throttle_applied:
        # Mild: over-throttle is safer than under-throttle; small penalty.
        value = -0.5 * float(miss_penalty)
        reason = "throttle_without_degradation"
    else:
        value = 0.0
        reason = "no_throttle_needed"
    return {
        "status": "OK",
        "value": float(value),
        "should_throttle": should_throttle,
        "throttle_applied": bool(throttle_applied),
        "degradation": float(degradation),
        "reason": reason,
        "execution_authority": False,
        "kind": "throttle_agreement",
    }
