"""Mathematical profitability foundation (advisory only).

Aggregates expectancy, walk-forward, stress ruin, Kelly cap, and sample/edge
gates into one operator/research report. Never grants execution authority and
never raises live risk by itself.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

try:
    from research_validation import (
        MAX_PROMOTION_STRESS_RUIN,
        MIN_PROMOTION_EXPECTANCY_R,
        MIN_PROMOTION_PROFIT_FACTOR,
        promotion_gate,
        setup_metrics,
    )
except ImportError:
    from research.research_validation import (
        MAX_PROMOTION_STRESS_RUIN,
        MIN_PROMOTION_EXPECTANCY_R,
        MIN_PROMOTION_PROFIT_FACTOR,
        promotion_gate,
        setup_metrics,
    )

try:
    from sample_gate import report as sample_report
except ImportError:
    from research.sample_gate import report as sample_report


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def advisory_kelly_cap(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the tightest fractional Kelly suggestion across setup metrics."""
    caps: List[float] = []
    details: List[Dict[str, Any]] = []
    for row in metrics or []:
        kelly = row.get("kelly") or {}
        if not isinstance(kelly, dict):
            continue
        fraction = kelly.get("fractional_kelly", kelly.get("kelly_fraction"))
        try:
            value = float(fraction)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        # Hard safety: never advise more than 1% from Kelly alone.
        capped = min(value, 0.01)
        caps.append(capped)
        details.append({
            "setup_id": row.get("setup_id"),
            "symbol": row.get("symbol"),
            "strategy": row.get("strategy"),
            "fractional_kelly": value,
            "advisory_cap": capped,
        })
    tightest = min(caps) if caps else None
    return {
        "advisory_risk_fraction_cap": tightest,
        "setups": details,
        "limitation": "Kelly is an upper bound only; never raises operator/base risk alone.",
        "execution_authority": False,
    }


def degradation_signals(metrics: List[Dict[str, Any]], *, min_expectancy: float = MIN_PROMOTION_EXPECTANCY_R) -> List[Dict[str, Any]]:
    """Flag setups whose expectancy no longer clears the research floor."""
    signals: List[Dict[str, Any]] = []
    for row in metrics or []:
        expectancy = _finite(row.get("expectancy_r"))
        if expectancy <= min_expectancy:
            signals.append({
                "setup_id": row.get("setup_id"),
                "symbol": row.get("symbol"),
                "strategy": row.get("strategy"),
                "expectancy_r": expectancy,
                "status": "DEGRADED_OR_FLAT",
                "execution_authority": False,
            })
    return signals


def live_risk_bound(
    *,
    operator_base: float,
    growth_advisory: Optional[float] = None,
    tiny_live_cap: Optional[float] = None,
    kelly_cap: Optional[float] = None,
) -> Dict[str, Any]:
    """Compose fail-closed live risk fraction as a min of independent caps."""
    try:
        base = float(operator_base)
    except (TypeError, ValueError, OverflowError):
        return {
            "status": "INVALID",
            "reasons": ["operator_base must be finite"],
            "execution_authority": False,
        }
    if not math.isfinite(base) or base <= 0:
        return {
            "status": "INVALID",
            "reasons": ["operator_base must be positive and finite"],
            "execution_authority": False,
        }
    candidates = {"operator_base": base}
    for name, value in (
        ("growth_advisory", growth_advisory),
        ("tiny_live_cap", tiny_live_cap),
        ("kelly_cap", kelly_cap),
    ):
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return {
                "status": "INVALID",
                "reasons": [f"{name} must be finite"],
                "execution_authority": False,
            }
        if not math.isfinite(number) or number < 0:
            return {
                "status": "INVALID",
                "reasons": [f"{name} must be non-negative and finite"],
                "execution_authority": False,
            }
        candidates[name] = number
    bound = min(candidates.values())
    return {
        "status": "OK",
        "live_risk_fraction": bound,
        "components": candidates,
        "binding_cap": min(candidates, key=candidates.get),
        "execution_authority": False,
        "limitation": "Bound is descriptive for operators; RiskController still owns sizing.",
    }


def report(
    path: str = "trading_state.db",
    *,
    operator_base: float = 0.01,
    growth_advisory: Optional[float] = None,
    tiny_live_cap: Optional[float] = None,
    minimum_closed_per_setup: int = 100,
) -> Dict[str, Any]:
    """Aggregate profitability evidence without granting order permission."""
    metrics = setup_metrics(path)
    gate = promotion_gate(path, minimum_closed_per_setup=minimum_closed_per_setup)
    sample = sample_report(path, minimum_samples=minimum_closed_per_setup)
    kelly = advisory_kelly_cap(metrics)
    degradation = degradation_signals(metrics)
    risk_bound = live_risk_bound(
        operator_base=operator_base,
        growth_advisory=growth_advisory,
        tiny_live_cap=tiny_live_cap,
        kelly_cap=kelly.get("advisory_risk_fraction_cap"),
    )
    eligible = list(gate.get("eligible_setups") or gate.get("eligible") or [])
    return {
        "status": "READY_FOR_REVIEW" if gate.get("eligible_count") else "BLOCKED",
        "path": path,
        "floors": {
            "min_expectancy_r": MIN_PROMOTION_EXPECTANCY_R,
            "min_profit_factor": MIN_PROMOTION_PROFIT_FACTOR,
            "max_stress_ruin": MAX_PROMOTION_STRESS_RUIN,
        },
        "setup_metrics": [
            {k: v for k, v in row.items() if k != "kelly"}
            for row in metrics
        ],
        "promotion_gate": {
            "eligible_count": gate.get("eligible_count", len(eligible)),
            "status": gate.get("status"),
            "eligible_setups": eligible,
            "configuration_error": gate.get("configuration_error"),
        },
        "sample_gate": {
            "status": sample.get("status"),
            "sample_size": sample.get("sample_size"),
            "reason": sample.get("reason"),
        },
        "kelly_cap": kelly,
        "degradation": degradation,
        "live_risk_bound": risk_bound,
        "execution_authority": False,
        "limitation": (
            "Descriptive research aggregate only. Does not approve orders, "
            "raise risk, or bypass readiness/tiny-live gates."
        ),
    }
