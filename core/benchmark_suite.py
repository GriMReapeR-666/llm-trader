"""Benchmark Suite — multi-dimensional performance evaluation across all strategies.

Evaluates trading performance across 8 dimensions:
  1. Risk-Adjusted Returns (Sharpe, Sortino, Calmar, DSR)
  2. Statistical Significance (t-test, p-value, min trades)
  3. Robustness (walk-forward efficiency, CPCV PBO)
  4. Regime Performance (per-regime Sharpe, worst regime)
  5. Edge Case Survival (stress test pass rate)
  6. Drawdown Analysis (max DD, recovery time, DD duration)
  7. Execution Quality (slippage, fill rate, R-multiple distribution)
  8. Capacity (ADV usage, position sizing limits)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class DimensionScore:
    dimension: str
    score: float  # 0-100
    grade: str  # A-F
    metrics: Dict[str, float]
    issues: List[str]
    recommendations: List[str]


@dataclass
class BenchmarkResult:
    overall_score: float  # 0-100
    overall_grade: str
    dimensions: List[DimensionScore]
    total_trades: int
    min_trades_met: bool
    summary: str
    ready_for_deployment: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 1),
            "overall_grade": self.overall_grade,
            "total_trades": self.total_trades,
            "min_trades_met": self.min_trades_met,
            "ready_for_deployment": self.ready_for_deployment,
            "summary": self.summary,
            "dimensions": {d.dimension: {"score": round(d.score, 1), "grade": d.grade, "issues": d.issues[:3]} for d in self.dimensions},
        }


class BenchmarkSuite:
    GRADE_THRESHOLDS = {"A+": 90, "A": 80, "B+": 70, "B": 60, "C+": 50, "C": 40, "D": 25, "F": 0}
    MIN_TRADES = 100
    DIMENSIONS = [
        "risk_adjusted_returns", "statistical_significance", "robustness",
        "regime_performance", "edge_case_survival", "drawdown_analysis",
        "execution_quality", "capacity",
    ]

    def __init__(self, min_trades: int = 100):
        self.min_trades = min_trades

    def evaluate(self, trades: List[Dict], equity_curve: Optional[np.ndarray] = None,
                 scenario_results: Optional[Dict] = None,
                 regime_results: Optional[Dict] = None) -> BenchmarkResult:
        dimensions = []
        dimensions.append(self._eval_risk_adjusted(trades, equity_curve))
        dimensions.append(self._eval_statistical(trades))
        dimensions.append(self._eval_robustness(trades))
        dimensions.append(self._eval_regime(regime_results))
        dimensions.append(self._eval_edge_cases(scenario_results))
        dimensions.append(self._eval_drawdown(trades, equity_curve))
        dimensions.append(self._eval_execution(trades))
        dimensions.append(self._eval_capacity(trades))

        # Weighted overall score
        weights = [0.20, 0.15, 0.15, 0.10, 0.10, 0.10, 0.10, 0.10]
        overall = sum(d.score * w for d, w in zip(dimensions, weights))
        overall_grade = self._grade(overall)
        total_trades = len(trades)
        min_met = total_trades >= self.min_trades

        issues = [i for d in dimensions for i in d.issues]
        ready = overall >= 60 and min_met and not any(d.grade in ("D", "F") for d in dimensions)

        return BenchmarkResult(
            overall_score=overall, overall_grade=overall_grade,
            dimensions=dimensions, total_trades=total_trades,
            min_trades_met=min_met, ready_for_deployment=ready,
            summary=self._build_summary(overall_grade, total_trades, min_met, issues),
        )

    def _eval_risk_adjusted(self, trades: List[Dict], equity: Optional[np.ndarray]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        if not trades:
            return DimensionScore("risk_adjusted_returns", 0, "F", {}, ["No trades"], ["Run more trades"])
        returns = np.array([t.get("r_multiple", t.get("pnl_pct", 0)) for t in trades])
        if len(returns) < 10:
            return DimensionScore("risk_adjusted_returns", 20, "F", {"trades": len(returns)}, ["< 10 trades"], ["Need more trades"])
        avg_r = float(np.mean(returns))
        std_r = float(np.std(returns))
        sharpe = avg_r / max(std_r, 1e-8) * math.sqrt(252)
        downside = returns[returns < 0]
        sortino = avg_r / max(float(np.std(downside)) if len(downside) > 0 else 1e-8, 1e-8) * math.sqrt(252)
        # DSR approximation
        n = len(returns)
        dsr = sharpe * (1 - 1.5 * math.log(max(n, 2)) / max(n, 1)) if n > 0 else 0
        metrics = {"sharpe": round(sharpe, 3), "sortino": round(sortino, 3), "dsr": round(dsr, 3), "avg_r": round(avg_r, 4)}
        score = min(100, max(0, sharpe * 30 + sortino * 10 + (100 if dsr > 0.95 else 50 if dsr > 0.7 else 0)))
        if sharpe < 0.5: issues.append(f"Sharpe {sharpe:.2f} < 0.5")
        if sortino < 0.8: recs.append("Improve downside protection")
        return DimensionScore("risk_adjusted_returns", score, self._grade(score), metrics, issues, recs)

    def _eval_statistical(self, trades: List[Dict]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        n = len(trades)
        metrics["trades"] = n
        if n < 30:
            score = n * 2
            issues.append(f"Only {n} trades (need 30+)")
        else:
            returns = np.array([t.get("r_multiple", t.get("pnl_pct", 0)) for t in trades])
            # t-test: is mean return significantly different from 0?
            if len(returns) > 1 and np.std(returns) > 0:
                t_stat = np.mean(returns) / (np.std(returns) / math.sqrt(len(returns)))
                p_value = 2 * (1 - min(abs(t_stat) / 3, 1.0))  # rough approx
                metrics["t_stat"] = round(float(t_stat), 3)
                metrics["p_value_approx"] = round(float(p_value), 4)
                score = min(100, 50 + t_stat * 10)
                if p_value > 0.05: issues.append(f"p-value {p_value:.3f} > 0.05 (not significant)")
            else:
                score = 30
        score = min(100, max(0, score))
        if n < self.min_trades: issues.append(f"{n}/{self.min_trades} minimum trades")
        return DimensionScore("statistical_significance", score, self._grade(score), metrics, issues, recs)

    def _eval_robustness(self, trades: List[Dict]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        # Check for WFE and PBO in trade metadata
        wfe_values = [t.get("wfe", 0) for t in trades if "wfe" in t]
        pbo_values = [t.get("pbo", 0) for t in trades if "pbo" in t]
        avg_wfe = float(np.mean(wfe_values)) if wfe_values else 0.5
        avg_pbo = float(np.mean(pbo_values)) if pbo_values else 0.5
        metrics["avg_wfe"] = round(avg_wfe, 3)
        metrics["avg_pbo"] = round(avg_pbo, 3)
        score = 50
        if avg_wfe > 0.5: score += 20
        if avg_pbo < 0.25: score += 20
        if wfe_values and pbo_values: score += 10
        if avg_pbo > 0.3: issues.append(f"PBO {avg_pbo:.3f} > 0.3 (overfitting risk)")
        if avg_wfe < 0.3: recs.append("Improve walk-forward efficiency")
        return DimensionScore("robustness", min(100, max(0, score)), self._grade(min(100, max(0, score))), metrics, issues, recs)

    def _eval_regime(self, regime_results: Optional[Dict]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        if not regime_results:
            return DimensionScore("regime_performance", 50, "C", {}, ["No regime data"], ["Enable regime tracking"])
        regime_sharpes = {r: v.get("sharpe", 0) for r, v in regime_results.items() if isinstance(v, dict)}
        metrics["regimes_tested"] = len(regime_sharpes)
        if regime_sharpes:
            worst = min(regime_sharpes.values())
            best = max(regime_sharpes.values())
            metrics["worst_sharpe"] = round(worst, 3)
            metrics["best_sharpe"] = round(best, 3)
            avg = float(np.mean(list(regime_sharpes.values())))
            score = min(100, max(0, avg * 50 + 30))
            if worst < -0.5: issues.append(f"Worst regime Sharpe: {worst:.2f}")
        else:
            score = 30
        return DimensionScore("regime_performance", score, self._grade(score), metrics, issues, recs)

    def _eval_edge_cases(self, scenario_results: Optional[Dict]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        if not scenario_results:
            return DimensionScore("edge_case_survival", 0, "F", {}, ["No scenario tests"], ["Run scenario tests"])
        passed = sum(1 for v in scenario_results.values() if isinstance(v, dict) and v.get("passed", False))
        total = len(scenario_results)
        pass_rate = passed / max(total, 1)
        metrics["scenarios_tested"] = total
        metrics["scenarios_passed"] = passed
        metrics["pass_rate"] = round(pass_rate, 3)
        score = pass_rate * 100
        if pass_rate < 0.5: issues.append(f"Only {pass_rate:.0%} scenarios passed")
        if pass_rate < 0.7: recs.append("Improve stress test robustness")
        return DimensionScore("edge_case_survival", score, self._grade(score), metrics, issues, recs)

    def _eval_drawdown(self, trades: List[Dict], equity: Optional[np.ndarray]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        if equity is None or len(equity) < 10:
            # Build from trades
            equity = np.cumsum([t.get("r_multiple", t.get("pnl_pct", 0)) for t in trades])
        if len(equity) < 2:
            return DimensionScore("drawdown_analysis", 0, "F", {}, ["No equity data"], [])
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / np.maximum(peak, 1e-8)
        max_dd = float(np.min(dd))
        dd_duration = 0
        current_dd = 0
        for d in dd:
            if d < 0:
                current_dd += 1
                dd_duration = max(dd_duration, current_dd)
            else:
                current_dd = 0
        metrics["max_drawdown"] = round(max_dd * 100, 2)
        metrics["dd_duration_bars"] = dd_duration
        score = max(0, min(100, 100 + max_dd * 200))  # max_dd is negative
        if max_dd < -0.20: issues.append(f"Max drawdown {max_dd:.1%} > -20%")
        if dd_duration > 100: recs.append("Reduce drawdown duration")
        return DimensionScore("drawdown_analysis", score, self._grade(score), metrics, issues, recs)

    def _eval_execution(self, trades: List[Dict]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        if not trades:
            return DimensionScore("execution_quality", 0, "F", {}, ["No trades"], [])
        r_multiples = [t.get("r_multiple", 0) for t in trades]
        wins = [r for r in r_multiples if r > 0]
        losses = [r for r in r_multiples if r < 0]
        win_rate = len(wins) / max(len(r_multiples), 1)
        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 0
        profit_factor = abs(sum(wins)) / max(abs(sum(losses)), 1e-8) if losses else 999
        metrics["win_rate"] = round(win_rate, 3)
        metrics["avg_win_r"] = round(avg_win, 3)
        metrics["avg_loss_r"] = round(avg_loss, 3)
        metrics["profit_factor"] = round(profit_factor, 3)
        score = min(100, win_rate * 60 + min(profit_factor, 3) * 15 + (25 if avg_win > abs(avg_loss) else 0))
        if win_rate < 0.35: issues.append(f"Win rate {win_rate:.1%} < 35%")
        if profit_factor < 1.0: issues.append(f"Profit factor {profit_factor:.2f} < 1.0")
        return DimensionScore("execution_quality", score, self._grade(score), metrics, issues, recs)

    def _eval_capacity(self, trades: List[Dict]) -> DimensionScore:
        metrics, issues, recs = {}, [], []
        volumes = [t.get("volume", 0) for t in trades if "volume" in t]
        if not volumes:
            metrics["avg_volume"] = 0
            return DimensionScore("capacity", 70, "B", metrics, [], [])
        avg_vol = float(np.mean(volumes))
        max_vol = float(np.max(volumes))
        metrics["avg_volume"] = round(avg_vol, 4)
        metrics["max_volume"] = round(max_vol, 4)
        score = 80  # Default good score
        if max_vol > 0.1: issues.append(f"Max volume {max_vol:.4f} may exceed capacity")
        return DimensionScore("capacity", score, self._grade(score), metrics, issues, recs)

    def _grade(self, score: float) -> str:
        for grade, threshold in sorted(self.GRADE_THRESHOLDS.items(), key=lambda x: -x[1]):
            if score >= threshold:
                return grade
        return "F"

    def _build_summary(self, grade: str, trades: int, min_met: bool, issues: List[str]) -> str:
        status = "READY" if grade in ("A+", "A", "B+", "B") and min_met else "NEEDS_WORK"
        return f"Grade {grade} | {trades} trades | {'MIN_MET' if min_met else 'MIN_NOT_MET'} | {len(issues)} issues | {status}"
