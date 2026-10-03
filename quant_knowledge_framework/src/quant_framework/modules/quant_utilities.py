"""
Quant Utilities Modules
=======================
Shared utility functions used across all quant domains.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple

from quant_framework.modules.interface import quant_module, quant_tool, QuantModule, registry


@quant_module(
    id="quant_utilities.expectancy",
    name="R-Multiple and Expectancy Calculator",
    version="1.1.0",
    category="quant_utilities",
    tags=["expectancy", "r-multiple", "ev", "risk management"],
)
class ExpectancyCalculator(QuantModule):
    """
    Calculates trading expectancy and R-multiples.
    """

    @quant_tool(
        module_id="quant_utilities.expectancy",
        name="quant_utilities.expectancy.calculate",
        description="Calculate trading expectancy from trade outcomes",
        parameters={
            "win_rate": {"type": "float", "description": "Win rate (0-1)"},
            "avg_win_r": {"type": "float", "description": "Average win in R units"},
            "avg_loss_r": {"type": "float", "description": "Average loss in R units (positive number)"},
        },
        returns={
            "ev": {"type": "float", "description": "Expected value per trade"},
            "ev_per_dollar": {"type": "float", "description": "EV per dollar risked"},
            "profitable": {"type": "bool", "description": "Whether strategy is profitable"},
        },
    )
    def calculate(self, win_rate: float, avg_win_r: float, avg_loss_r: float) -> Dict:
        if not 0 <= win_rate <= 1 or avg_loss_r <= 0:
            return {"error": "Invalid parameters"}
        p_loss = 1 - win_rate
        ev = (win_rate * avg_win_r) - (p_loss * avg_loss_r)
        return {
            "ev": float(ev),
            "ev_per_dollar": float(ev),
            "profitable": bool(ev > 0),
            "win_rate": float(win_rate),
            "avg_win_r": float(avg_win_r),
            "avg_loss_r": float(avg_loss_r),
        }

    @quant_tool(
        module_id="quant_utilities.expectancy",
        name="quant_utilities.expectancy.r_multiple",
        description="Calculate R-multiple for a trade",
        parameters={
            "entry": {"type": "float", "description": "Entry price"},
            "exit": {"type": "float", "description": "Exit price"},
            "stop": {"type": "float", "description": "Stop loss price"},
        },
        returns={
            "r_multiple": {"type": "float", "description": "Profit/loss in R units (P&L / initial risk)"},
            "dollar_profit": {"type": "float", "description": "Absolute per-unit P&L (exit - entry)"},
        },
    )
    def r_multiple(self, entry: float, exit: float, stop: float) -> Dict:
        """
        R-multiple: P&L measured in units of initial risk (|entry - stop|).
        `dollar_profit` is the absolute per-unit P&L, (exit - entry); v1.x
        erroneously reported the R-multiple in this field.
        """
        if entry == stop:
            return {"error": "Entry cannot equal stop"}
        risk = abs(entry - stop)
        r = (exit - entry) / risk
        return {"r_multiple": float(r), "dollar_profit": float(exit - entry)}


@quant_module(
    id="quant_utilities.drawdown",
    name="Drawdown and Streak Analyzer",
    version="1.0.0",
    category="quant_utilities",
    tags=["drawdown", "streak", "risk", "maxdd", "cagr"],
)
class DrawdownAnalyzer(QuantModule):
    """
    Analyzes drawdowns, streaks, and recovery metrics.
    """

    @quant_tool(
        module_id="quant_utilities.drawdown",
        name="quant_utilities.drawdown.analyze",
        description="Analyze drawdowns and streaks from equity curve",
        parameters={
            "equity_curve": {"type": "array", "description": "Equity curve values"},
        },
        returns={
            "max_drawdown": {"type": "float"},
            "max_drawdown_duration": {"type": "int"},
            "calmar_ratio": {"type": "float"},
        },
    )
    def analyze(self, equity_curve: List[float]) -> Dict:
        equity = np.array(equity_curve, dtype=float)
        if len(equity) < 2:
            return {"error": "Insufficient equity curve data"}

        cummax = np.maximum.accumulate(equity)
        drawdowns = (cummax - equity) / cummax
        max_dd = float(np.max(drawdowns))

        in_drawdown = drawdowns > 0
        drawdown_periods = []
        current_period = 0
        for dd in in_drawdown:
            if dd:
                current_period += 1
            else:
                if current_period > 0:
                    drawdown_periods.append(current_period)
                current_period = 0
        max_dd_duration = max(drawdown_periods) if drawdown_periods else 0

        n_years = len(equity) / 252
        cagr = (equity[-1] / equity[0]) ** (1 / n_years) - 1 if n_years > 0 else 0.0
        calmar = cagr / max_dd if max_dd > 0 else 0.0

        return {
            "max_drawdown": float(max_dd),
            "max_drawdown_duration": int(max_dd_duration),
            "calmar_ratio": float(calmar),
            "cagr": float(cagr),
        }
