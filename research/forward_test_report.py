"""Backtest-versus-demo discrepancy reporting.

The report is intentionally descriptive and fail-closed.  It cannot call MT5,
approve a model, or declare a demo result representative when either side has
no reconciled, cost-aware observations.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Optional

from operations.journal_audit import qualified_metrics


MIN_FORWARD_SAMPLE = 100


def _read(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _backtest_summary(value: Dict[str, Any]) -> Dict[str, Any]:
    metrics = value.get("qualified_metrics") if isinstance(value.get("qualified_metrics"), dict) else value
    trade_quality = metrics.get("trade_quality", {}) if isinstance(metrics, dict) else {}
    portfolio = metrics.get("portfolio_quality", {}) if isinstance(metrics, dict) else {}

    def integer(raw: Any) -> Optional[int]:
        try:
            if isinstance(raw, bool):
                return None
            parsed = int(raw)
            return parsed if parsed >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    def number(raw: Any) -> Optional[float]:
        try:
            parsed = float(raw)
            return parsed if math.isfinite(parsed) else None
        except (TypeError, ValueError, OverflowError):
            return None

    sample = integer(metrics.get("sample_size", value.get("closed_trades", 0)))
    net_pnl = number(portfolio.get("closed_net_pnl", value.get("net_pnl", 0)))
    expectancy_currency = number(trade_quality.get("expectancy_currency", value.get("expectancy_currency")))
    expectancy_r = number(trade_quality.get("expectancy_r", value.get("expectancy_r")))
    cost_model = metrics.get("cost_model", value.get("cost_model"))
    cost_model = str(cost_model).strip() if cost_model is not None else ""
    return {"sample_size": sample, "net_pnl": net_pnl,
            "expectancy_currency": expectancy_currency,
            "expectancy_r": expectancy_r,
            "cost_model": cost_model, "date_range": metrics.get("date_range", value.get("date_range")),
            "valid": sample is not None and net_pnl is not None and bool(cost_model)
            and cost_model.lower() not in {"unknown", "none", ""}}


def compare(backtest_report_path: str, demo_journal_path: str = "trading_state.db",
            *, minimum_demo_sample: int = MIN_FORWARD_SAMPLE) -> Dict[str, Any]:
    if (not isinstance(minimum_demo_sample, int) or isinstance(minimum_demo_sample, bool)
            or minimum_demo_sample < MIN_FORWARD_SAMPLE):
        return {"status": "INVALID_INPUT", "execution_authority": False,
                "minimum_demo_sample": minimum_demo_sample,
                "required_minimum_demo_sample": MIN_FORWARD_SAMPLE,
                "reason": "forward-test comparison requires an integer sample floor of at least 100"}
    backtest = _read(backtest_report_path)
    if backtest is None:
        return {"status": "INSUFFICIENT_DATA", "reason": "backtest report missing or malformed",
                "execution_authority": False}
    backtest_summary = _backtest_summary(backtest)
    demo = qualified_metrics(demo_journal_path)
    demo_portfolio = demo.get("portfolio_quality", {}) if isinstance(demo.get("portfolio_quality"), dict) else {}
    demo_trade = demo.get("trade_quality", {}) if isinstance(demo.get("trade_quality"), dict) else {}

    def _number(raw: Any) -> Optional[float]:
        try:
            parsed = float(raw)
            return parsed if math.isfinite(parsed) else None
        except (TypeError, ValueError, OverflowError):
            return None

    try:
        demo_sample = int(demo.get("sample_size", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        demo_sample = None
    demo_cost_model = str(demo.get("cost_model") or "").strip()
    demo_summary = {"sample_size": demo_sample,
                    "net_pnl": _number(demo_portfolio.get("closed_net_pnl", 0)),
                    "expectancy_currency": _number(demo_trade.get("expectancy_currency")),
                    "expectancy_r": _number(demo_trade.get("expectancy_r")),
                    "cost_model": demo_cost_model, "date_range": demo.get("date_range"),
                    "valid": demo_sample is not None and demo_sample >= 0
                    and bool(demo_cost_model) and demo_cost_model.lower() not in {"unknown", "none"}}
    if (not backtest_summary.get("valid") or not demo_summary.get("valid")
            or (backtest_summary.get("sample_size") or 0) < MIN_FORWARD_SAMPLE
            or (demo_summary.get("sample_size") or 0) < minimum_demo_sample):
        return {"status": "INSUFFICIENT_DATA", "backtest": backtest_summary, "demo": demo_summary,
                "minimum_demo_sample": minimum_demo_sample, "execution_authority": False,
                "reason": "Both sides need at least 100 reconciled, cost-aware observations with valid numeric metrics; this report cannot promote a strategy."}
    def delta(field: str) -> Optional[float]:
        left, right = backtest_summary.get(field), demo_summary.get(field)
        return float(right) - float(left) if left is not None and right is not None else None
    cost_models_match = backtest_summary["cost_model"] == demo_summary["cost_model"]
    return {"status": "READY_FOR_OPERATOR_REVIEW", "backtest": backtest_summary, "demo": demo_summary,
            "discrepancy": {"net_pnl": delta("net_pnl"), "expectancy_currency": delta("expectancy_currency"),
                             "expectancy_r": delta("expectancy_r")},
            "cost_model_comparison": {"match": cost_models_match,
                                       "requires_operator_review": not cost_models_match},
            "execution_authority": False,
            "reason": "Discrepancies require cost-model and regime review; no automatic promotion is performed."}
