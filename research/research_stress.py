"""Offline sequence and cost stress tests for labelled journal R-multiples."""
from __future__ import annotations

import random
import math
from statistics import median
from typing import Any, Dict, Iterable, List
import numpy as np

try:
    from research.research_validation import labelled_dataset
except ImportError:
    from research.research_validation import labelled_dataset


def _equity_stats(r_values: Iterable[float], risk_fraction: float) -> Dict[str, float]:
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    for r in r_values:
        equity *= max(0.0, 1.0 + risk_fraction * float(r))
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 1.0)
    return {"ending_equity_multiple": equity, "max_drawdown_fraction": max_drawdown,
            "ruin_threshold_hit": float(equity <= 0.5)}


def monte_carlo(r_values: List[float], *, paths: int = 1000, seed: int = 7,
                risk_fraction: float = 0.0025) -> Dict[str, Any]:
    if not isinstance(r_values, list) or not r_values:
        return {"status": "INSUFFICIENT_DATA", "paths": 0, "reason": "R-value sample is empty or malformed", "execution_authority": False}
    try:
        finite_values = [float(value) for value in r_values]
    except (TypeError, ValueError, OverflowError):
        return {"status": "INVALID_INPUT", "paths": 0, "reason": "R-values must be numeric and finite", "execution_authority": False}
    if not all(math.isfinite(value) for value in finite_values):
        return {"status": "INVALID_INPUT", "paths": 0, "reason": "R-values must be numeric and finite", "execution_authority": False}
    if (not isinstance(paths, int) or isinstance(paths, bool) or paths <= 0
            or not isinstance(seed, int) or isinstance(seed, bool)):
        return {"status": "INVALID_INPUT", "paths": 0, "reason": "paths and seed must be integers", "execution_authority": False}
    try:
        fraction = float(risk_fraction)
    except (TypeError, ValueError, OverflowError):
        return {"status": "INVALID_INPUT", "paths": 0, "reason": "risk_fraction must be finite and positive", "execution_authority": False}
    if not math.isfinite(fraction) or fraction <= 0 or fraction >= 1:
        return {"status": "INVALID_INPUT", "paths": 0, "reason": "risk_fraction must be finite and in (0, 1)", "execution_authority": False}
    rng = random.Random(seed)
    outcomes = []
    for _ in range(paths):
        sample = [rng.choice(finite_values) for _ in range(len(finite_values))]
        outcomes.append(_equity_stats(sample, fraction))
    equities = sorted(item["ending_equity_multiple"] for item in outcomes)
    drawdowns = sorted(item["max_drawdown_fraction"] for item in outcomes)
    return {"status": "READY_FOR_REVIEW", "paths": paths, "seed": seed,
            "risk_fraction": fraction, "trades_per_path": len(finite_values),
            "ending_equity_median": median(equities),
            "ending_equity_p05": equities[max(0, int(paths * .05) - 1)],
            "ending_equity_p95": equities[min(paths - 1, int(paths * .95))],
            "max_drawdown_median": median(drawdowns),
            "max_drawdown_p95": drawdowns[min(paths - 1, int(paths * .95))],
            "ruin_probability": sum(item["ruin_threshold_hit"] for item in outcomes) / paths,
            "execution_authority": False}


def stress_report(path: str = "trading_state.db", *, minimum_samples: int = 100,
                  cost_buffers_r: Iterable[float] = (0.0, 0.05, 0.10)) -> Dict[str, Any]:
    if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
            or minimum_samples < 100):
        return {"status": "INVALID_INPUT", "sample_size": 0,
                "minimum_samples": 100,
                "requested_minimum_samples": minimum_samples,
                "reason": "minimum_samples must be an integer >= 100",
                "scenarios": [], "execution_authority": False}
    try:
        buffers = [float(value) for value in cost_buffers_r]
    except (TypeError, ValueError, OverflowError):
        return {"status": "INVALID_INPUT", "sample_size": 0,
                "minimum_samples": minimum_samples,
                "reason": "cost buffers must be finite non-negative numbers",
                "scenarios": [], "execution_authority": False}
    if not buffers or any(not math.isfinite(value) or value < 0 for value in buffers):
        return {"status": "INVALID_INPUT", "sample_size": 0,
                "minimum_samples": minimum_samples,
                "reason": "cost buffers must be finite non-negative numbers",
                "scenarios": [], "execution_authority": False}
    dataset = labelled_dataset(path)
    rows = dataset.get("rows", [])
    r_values = [float(row["r_multiple"]) for row in rows if row.get("r_multiple") is not None]
    report: Dict[str, Any] = {"sample_size": len(r_values), "minimum_samples": minimum_samples,
                              "point_in_time": dataset.get("point_in_time", False),
                              "cost_model": dataset.get("cost_model"), "execution_authority": False}
    if len(r_values) < minimum_samples:
        report.update({"status": "INSUFFICIENT_DATA", "reason": "Need correctly labelled, reconciled rows before stress results are decision evidence.", "scenarios": []})
        return report
    scenarios = []
    for buffer in buffers:
        adjusted = [r - buffer for r in r_values]
        scenarios.append({"cost_buffer_r": float(buffer), "monte_carlo": monte_carlo(adjusted)})
    report.update({"status": "READY_FOR_REVIEW", "scenarios": scenarios,
                   "reason": "Stress output is descriptive; it cannot promote a strategy or alter live risk."})
    return report


def stress_remove_best_trades(
    r_values: List[float],
    *,
    drop_pct: float = 0.05,
    minimum_trades: int = 20,
) -> Dict[str, Any]:
    """Break-it stress test: remove the best N% of trades and test if edge survives."""
    if not isinstance(r_values, list) or len(r_values) < minimum_trades:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": f"Sample size {len(r_values) if isinstance(r_values, list) else 0} below minimum {minimum_trades}",
            "execution_authority": False,
        }
    try:
        clean = [float(x) for x in r_values if math.isfinite(float(x))]
    except (TypeError, ValueError):
        return {"status": "INVALID_INPUT", "reason": "Non-numeric values", "execution_authority": False}

    if len(clean) < minimum_trades:
        return {"status": "INSUFFICIENT_DATA", "reason": "Not enough finite trades", "execution_authority": False}

    sorted_trades = sorted(clean, reverse=True)
    k_drop = max(1, int(math.ceil(len(sorted_trades) * drop_pct)))
    dropped = sorted_trades[:k_drop]
    remaining = sorted_trades[k_drop:]

    pre_exp = float(np.mean(clean))
    post_exp = float(np.mean(remaining)) if remaining else 0.0
    wins = [r for r in remaining if r > 0]
    losses = [r for r in remaining if r < 0]
    win_rate = len(wins) / len(remaining) if remaining else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    mc_res = monte_carlo(remaining, paths=1000) if remaining else {}

    return {
        "status": "READY_FOR_REVIEW",
        "original_trade_count": len(clean),
        "trades_dropped": k_drop,
        "dropped_values": dropped,
        "remaining_trade_count": len(remaining),
        "pre_trim_expectancy_r": round(pre_exp, 4),
        "post_trim_expectancy_r": round(post_exp, 4),
        "post_trim_win_rate": round(win_rate, 4),
        "post_trim_profit_factor": round(profit_factor, 4),
        "is_robust_to_trim": post_exp > 0.0 and profit_factor > 1.0,
        "monte_carlo_trimmed": mc_res,
        "execution_authority": False,
    }

