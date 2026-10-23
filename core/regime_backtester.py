"""Regime-Aware Backtesting Engine — Multi-Period Partitioning, 6-Regime Classifier & 15-Metric Suite."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
import polars as pl

from core.strategy_universe import (
    StrategySpec,
    StrategyUniverseGenerator,
    calculate_fast_metrics_numba,
    simulate_positions_numba,
)
from core.diagnostics_engine import run_purged_walk_forward

log = logging.getLogger("RegimeBacktester")


@dataclass
class PnLDistribution:
    mean_return: float
    std_return: float
    skewness: float
    kurtosis: float
    var_95: float
    cvar_95: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyRegimeMetrics:
    total_return_pct: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_pct: float
    profit_factor: float
    win_rate_pct: float
    win_loss_ratio: float
    expectancy: float
    turnover: float
    exposure_pct: float
    avg_trade_duration_bars: float
    slippage_adjusted_return_pct: float
    pnl_distribution: PnLDistribution

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PeriodSplits:
    is_range: Tuple[int, int]
    val_range: Tuple[int, int]
    oos_range: Tuple[int, int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeBacktestReport:
    strategy_id: str
    symbol: str
    timeframe: str
    total_bars: int
    overall_metrics: StrategyRegimeMetrics
    is_metrics: StrategyRegimeMetrics
    val_metrics: StrategyRegimeMetrics
    oos_metrics: StrategyRegimeMetrics
    regime_breakdown: Dict[str, StrategyRegimeMetrics]
    walk_forward_efficiency: float
    wfe_passed: bool
    stress_survival_passed: bool
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RegimeClassifier:
    """Canonical 6-regime classifier: BULL, BEAR, SIDEWAYS, HIGH_VOL, LOW_VOL, STRESS."""

    def __init__(self, trend_window: int = 20, vol_window: int = 20):
        self.trend_window = trend_window
        self.vol_window = vol_window

    def classify(self, df: pl.DataFrame) -> np.ndarray:
        if df.height == 0 or "close" not in df.columns:
            return np.empty(0, dtype=object)

        prices = df["close"].to_numpy().astype(np.float64)
        n = len(prices)
        labels = np.full(n, "SIDEWAYS", dtype=object)

        if n < self.trend_window + 5:
            return labels

        rets = np.zeros(n, dtype=np.float64)
        rets[1:] = np.diff(prices) / np.maximum(prices[:-1], 1e-8)

        # Rolling slope / momentum (% change over trend_window)
        w_trend = self.trend_window
        slopes = np.zeros(n, dtype=np.float64)
        for i in range(w_trend, n):
            denom = prices[i - w_trend]
            slopes[i] = (prices[i] - denom) / denom if denom > 0 else 0.0

        # Rolling volatility
        w_vol = self.vol_window
        vols = np.zeros(n, dtype=np.float64)
        for i in range(w_vol, n):
            vols[i] = np.std(rets[i - w_vol : i])

        active_vols = vols[w_vol:]
        p25 = float(np.percentile(active_vols, 25)) if len(active_vols) > 0 else 0.0
        p75 = float(np.percentile(active_vols, 75)) if len(active_vols) > 0 else 0.0

        # Peak and drawdown for stress detection
        peak = np.maximum.accumulate(prices)
        dd = (peak - prices) / np.maximum(peak, 1e-8)

        for i in range(n):
            # 1. Stress: severe drawdown >= 3% or sharp 3-bar shock <= -3%
            short_drop = (prices[i] - prices[i - 3]) / prices[i - 3] if i >= 3 else 0.0
            if dd[i] >= 0.03 or short_drop <= -0.03:
                labels[i] = "STRESS"
            elif vols[i] >= p75 and p75 > 0:
                labels[i] = "HIGH_VOL"
            elif vols[i] <= p25 and p25 > 0 and i >= w_vol:
                labels[i] = "LOW_VOL"
            elif slopes[i] > 0.005:
                labels[i] = "BULL"
            elif slopes[i] < -0.005:
                labels[i] = "BEAR"
            else:
                labels[i] = "SIDEWAYS"

        return labels


class MultiPeriodSplitter:
    """Partitions time series into In-Sample, Validation, Out-of-Sample, and Purged WFO splits."""

    def __init__(self, is_ratio: float = 0.50, val_ratio: float = 0.25, oos_ratio: float = 0.25):
        self.is_ratio = is_ratio
        self.val_ratio = val_ratio
        self.oos_ratio = oos_ratio

    def split(self, n_bars: int) -> PeriodSplits:
        is_end = int(n_bars * self.is_ratio)
        val_end = is_end + int(n_bars * self.val_ratio)
        return PeriodSplits(
            is_range=(0, is_end),
            val_range=(is_end, val_end),
            oos_range=(val_end, n_bars),
        )

    def get_wfo_splits(self, n_bars: int, n_folds: int = 4, purge_bars: int = 10) -> List[Dict[str, Any]]:
        test_size = max(5, (n_bars - purge_bars) // (n_folds + 1))
        train_size = test_size
        step = (n_bars - train_size - purge_bars - test_size) // max(1, n_folds - 1) if n_folds > 1 else 0

        folds: List[Dict[str, Any]] = []
        for k in range(n_folds):
            train_start = k * step
            train_end = train_start + train_size
            test_start = train_end + purge_bars
            test_end = (test_start + test_size) if k < n_folds - 1 else n_bars
            test_end = min(n_bars, test_end)
            if test_start < test_end:
                folds.append({
                    "fold": k,
                    "train_range": (train_start, train_end),
                    "purge_range": (train_end, test_start),
                    "test_range": (test_start, test_end),
                })
        return folds


def compute_15_metrics(
    equity: np.ndarray,
    returns: np.ndarray,
    signals: Optional[np.ndarray] = None,
    spread_bps: float = 2.0,
    slippage_bps: float = 1.0,
) -> StrategyRegimeMetrics:
    """Computes the full 15-factor institutional quantitative metrics suite."""
    n = len(returns)
    if n == 0 or len(equity) == 0:
        pnl_zero = PnLDistribution(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return StrategyRegimeMetrics(
            total_return_pct=0.0,
            annualized_volatility=0.0,
            sharpe=0.0,
            sortino=0.0,
            calmar=0.0,
            max_drawdown_pct=0.0,
            profit_factor=0.0,
            win_rate_pct=0.0,
            win_loss_ratio=0.0,
            expectancy=0.0,
            turnover=0.0,
            exposure_pct=0.0,
            avg_trade_duration_bars=0.0,
            slippage_adjusted_return_pct=0.0,
            pnl_distribution=pnl_zero,
        )

    # 1. Total return
    total_ret = float((equity[-1] - equity[0]) / equity[0]) if equity[0] > 0 else 0.0
    total_return_pct = round(total_ret * 100.0, 2)

    # 2. Annualized volatility
    std_r = float(np.std(returns)) if n > 1 else 0.0
    ann_vol = round(float(std_r * np.sqrt(252.0) * 100.0), 2)

    # 3-7. Fast metrics: Sharpe, Sortino, Calmar, Win Rate, Profit Factor, MaxDD
    sr, sortino, calmar, win_rate, pf, max_dd_pct = calculate_fast_metrics_numba(equity, returns)

    # 8. Win rate %
    win_rate_pct = round(float(win_rate * 100.0), 2)

    # 9. Win/loss ratio
    wins = returns[returns > 0]
    losses = np.abs(returns[returns < 0])
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 1e-4
    win_loss_ratio = round(avg_win / max(avg_loss, 1e-6), 2)

    # 10. Mathematical Expectancy
    win_p = float(len(wins) / n) if n > 0 else 0.0
    loss_p = 1.0 - win_p
    expectancy = round(float((win_p * avg_win) - (loss_p * avg_loss)), 6)

    # 11. Turnover
    turnover = float(round(np.mean(np.abs(np.diff(signals))), 4)) if signals is not None and len(signals) > 1 else 0.0

    # 12. Exposure %
    exposure_pct = float(round(np.mean(signals != 0) * 100.0, 2)) if signals is not None and len(signals) > 0 else 0.0

    # 13. Average Trade Duration (bars)
    durations = []
    if signals is not None and len(signals) > 0:
        c_dur = 0
        for s in signals:
            if s != 0:
                c_dur += 1
            else:
                if c_dur > 0:
                    durations.append(c_dur)
                    c_dur = 0
        if c_dur > 0:
            durations.append(c_dur)
    avg_trade_duration_bars = float(round(np.mean(durations), 1)) if durations else 0.0

    # 14. Slippage-adjusted Return %
    friction_cost = turnover * (slippage_bps + spread_bps) * 1e-4 * n
    slippage_adjusted_return_pct = round(max(-100.0, (total_ret - friction_cost) * 100.0), 2)

    # 15. P&L Distribution Profile
    mean_r = float(np.mean(returns))
    skew = float(np.mean(((returns - mean_r) / (std_r + 1e-8)) ** 3)) if std_r > 1e-8 else 0.0
    kurt = float(np.mean(((returns - mean_r) / (std_r + 1e-8)) ** 4)) if std_r > 1e-8 else 0.0
    var_95 = float(round(np.percentile(returns, 5), 4))
    under_var = returns[returns <= var_95]
    cvar_95 = float(round(np.mean(under_var), 4)) if len(under_var) > 0 else var_95

    pnl_dist = PnLDistribution(
        mean_return=round(mean_r, 6),
        std_return=round(std_r, 6),
        skewness=round(skew, 2),
        kurtosis=round(kurt, 2),
        var_95=var_95,
        cvar_95=cvar_95,
    )

    return StrategyRegimeMetrics(
        total_return_pct=total_return_pct,
        annualized_volatility=ann_vol,
        sharpe=round(float(sr), 3),
        sortino=round(float(sortino), 3),
        calmar=round(float(calmar), 3),
        max_drawdown_pct=round(float(max_dd_pct * 100.0), 2),
        profit_factor=round(float(pf), 2),
        win_rate_pct=win_rate_pct,
        win_loss_ratio=win_loss_ratio,
        expectancy=expectancy,
        turnover=turnover,
        exposure_pct=exposure_pct,
        avg_trade_duration_bars=avg_trade_duration_bars,
        slippage_adjusted_return_pct=slippage_adjusted_return_pct,
        pnl_distribution=pnl_dist,
    )


class RegimeBacktestPipeline:
    """Complete regime-aware backtesting pipeline integrating multi-period and regime splits."""

    def __init__(self, emit_telemetry: bool = True):
        self.emit_telemetry = emit_telemetry
        self.classifier = RegimeClassifier()
        self.splitter = MultiPeriodSplitter()
        self.generator = StrategyUniverseGenerator()

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.emit_telemetry:
            return
        try:
            import core.agent_event_bus as aeb
            emit_fn = getattr(aeb, "emit_agent_event", None)
            if emit_fn is not None:
                emit_fn(sender="RegimeBacktestPipeline", event_type=event_type, payload=payload)
        except Exception:
            pass

    def run_backtest(
        self,
        spec: StrategySpec,
        df: pl.DataFrame,
        spread_bps: float = 2.0,
        slippage_bps: float = 1.0,
        initial_capital: float = 10000.0,
    ) -> RegimeBacktestReport:
        prices = df["close"].to_numpy().astype(np.float64)
        n_bars = len(prices)

        # 1. Baseline signals & full simulation
        signals = self.generator.generate_baseline_signals(spec, df)
        eq_all, _, _, _, _ = simulate_positions_numba(
            prices, signals, spread_bps=spread_bps, slippage_bps=slippage_bps, initial_capital=initial_capital
        )
        rets_all = np.diff(eq_all) / eq_all[:-1] if len(eq_all) > 1 else np.empty(0, dtype=np.float64)
        overall_metrics = compute_15_metrics(eq_all, rets_all, signals, spread_bps, slippage_bps)

        # 2. Multi-period splits: IS / VAL / OOS
        splits = self.splitter.split(n_bars)
        is_s, is_e = splits.is_range
        val_s, val_e = splits.val_range
        oos_s, oos_e = splits.oos_range

        eq_is, _, _, _, _ = simulate_positions_numba(
            prices[is_s:is_e], signals[is_s:is_e], spread_bps=spread_bps, slippage_bps=slippage_bps
        )
        rets_is = np.diff(eq_is) / eq_is[:-1] if len(eq_is) > 1 else np.empty(0, dtype=np.float64)
        is_metrics = compute_15_metrics(eq_is, rets_is, signals[is_s:is_e], spread_bps, slippage_bps)

        eq_val, _, _, _, _ = simulate_positions_numba(
            prices[val_s:val_e], signals[val_s:val_e], spread_bps=spread_bps, slippage_bps=slippage_bps
        )
        rets_val = np.diff(eq_val) / eq_val[:-1] if len(eq_val) > 1 else np.empty(0, dtype=np.float64)
        val_metrics = compute_15_metrics(eq_val, rets_val, signals[val_s:val_e], spread_bps, slippage_bps)

        eq_oos, _, _, _, _ = simulate_positions_numba(
            prices[oos_s:oos_e], signals[oos_s:oos_e], spread_bps=spread_bps, slippage_bps=slippage_bps
        )
        rets_oos = np.diff(eq_oos) / eq_oos[:-1] if len(eq_oos) > 1 else np.empty(0, dtype=np.float64)
        oos_metrics = compute_15_metrics(eq_oos, rets_oos, signals[oos_s:oos_e], spread_bps, slippage_bps)

        # 3. Purged Walk-Forward Efficiency (WFO)
        wfo_res = run_purged_walk_forward(spec, df, generator=self.generator, n_splits=4, purge_bars=10)
        wfe = float(wfo_res.get("wfe", 0.0))
        wfe_passed = bool(wfo_res.get("wfe_passed", False))

        # 4. Regime classification & slice breakdown
        labels = self.classifier.classify(df)
        regimes = ["BULL", "BEAR", "SIDEWAYS", "HIGH_VOL", "LOW_VOL", "STRESS"]
        regime_breakdown: Dict[str, StrategyRegimeMetrics] = {}

        for reg in regimes:
            mask = (labels == reg)
            if np.sum(mask) >= 5:
                # Approximate slice equity via masked returns
                reg_rets = rets_all[mask[1:]] if len(rets_all) == n_bars - 1 else np.empty(0)
                if len(reg_rets) > 0:
                    reg_eq = np.cumprod(1.0 + reg_rets) * initial_capital
                    reg_metrics = compute_15_metrics(reg_eq, reg_rets, signals[mask], spread_bps, slippage_bps)
                else:
                    reg_metrics = compute_15_metrics(np.array([initial_capital]), np.empty(0))
            else:
                reg_metrics = compute_15_metrics(np.array([initial_capital]), np.empty(0))
            regime_breakdown[reg] = reg_metrics

        # Stress survival test: strategy must not suffer > 25% max DD under STRESS/HIGH_VOL
        stress_dd = regime_breakdown.get("STRESS", overall_metrics).max_drawdown_pct
        stress_survival_passed = bool(stress_dd <= 25.0)

        sym_str = spec.target_symbols[0] if getattr(spec, "target_symbols", None) else getattr(spec, "symbol", "EURUSD")
        tf_str = spec.timeframes[0] if getattr(spec, "timeframes", None) else getattr(spec, "timeframe", "H1")

        # 5. Formatted ASCII Table Output
        print("\n" + "=" * 105)
        print(f"             REGIME-AWARE INSTITUTIONAL BACKTEST REPORT — {spec.strategy_id.upper()}             ")
        print("=" * 105)
        print(f"Symbol: {sym_str} | Timeframe: {tf_str} | Bars: {n_bars} | Authority: FALSE (evaluation)")
        print("-" * 105)
        print(f"{'Partition / Regime':<20} | {'Return %':>9} | {'Sharpe':>7} | {'Sortino':>7} | {'MaxDD %':>8} | {'Win Rate':>8} | {'Exp':>10} | {'VaR95':>8}")
        print("-" * 105)
        print(f"{'OVERALL (Full Period)':<20} | {overall_metrics.total_return_pct:>8.2f}% | {overall_metrics.sharpe:>7.2f} | {overall_metrics.sortino:>7.2f} | {overall_metrics.max_drawdown_pct:>7.2f}% | {overall_metrics.win_rate_pct:>7.1f}% | {overall_metrics.expectancy:>10.6f} | {overall_metrics.pnl_distribution.var_95:>8.4f}")
        print(f"{'In-Sample (50%)':<20} | {is_metrics.total_return_pct:>8.2f}% | {is_metrics.sharpe:>7.2f} | {is_metrics.sortino:>7.2f} | {is_metrics.max_drawdown_pct:>7.2f}% | {is_metrics.win_rate_pct:>7.1f}% | {is_metrics.expectancy:>10.6f} | {is_metrics.pnl_distribution.var_95:>8.4f}")
        print(f"{'Validation (25%)':<20} | {val_metrics.total_return_pct:>8.2f}% | {val_metrics.sharpe:>7.2f} | {val_metrics.sortino:>7.2f} | {val_metrics.max_drawdown_pct:>7.2f}% | {val_metrics.win_rate_pct:>7.1f}% | {val_metrics.expectancy:>10.6f} | {val_metrics.pnl_distribution.var_95:>8.4f}")
        print(f"{'Out-of-Sample (25%)':<20} | {oos_metrics.total_return_pct:>8.2f}% | {oos_metrics.sharpe:>7.2f} | {oos_metrics.sortino:>7.2f} | {oos_metrics.max_drawdown_pct:>7.2f}% | {oos_metrics.win_rate_pct:>7.1f}% | {oos_metrics.expectancy:>10.6f} | {oos_metrics.pnl_distribution.var_95:>8.4f}")
        print("-" * 105)
        for r_name in regimes:
            rm = regime_breakdown[r_name]
            print(f"{r_name:<20} | {rm.total_return_pct:>8.2f}% | {rm.sharpe:>7.2f} | {rm.sortino:>7.2f} | {rm.max_drawdown_pct:>7.2f}% | {rm.win_rate_pct:>7.1f}% | {rm.expectancy:>10.6f} | {rm.pnl_distribution.var_95:>8.4f}")
        print("-" * 105)
        print(f"Purged Walk-Forward Efficiency (WFE): {wfe:.2f} (Passed: {wfe_passed}) | Stress Survival: {stress_survival_passed}")
        print("=" * 105 + "\n")

        self._emit(
            "REGIME_BACKTEST_COMPLETED",
            {
                "strategy_id": spec.strategy_id,
                "overall_sharpe": overall_metrics.sharpe,
                "wfe": wfe,
                "wfe_passed": wfe_passed,
                "stress_survival_passed": stress_survival_passed,
            },
        )

        return RegimeBacktestReport(
            strategy_id=spec.strategy_id,
            symbol=sym_str,
            timeframe=tf_str,
            total_bars=n_bars,
            overall_metrics=overall_metrics,
            is_metrics=is_metrics,
            val_metrics=val_metrics,
            oos_metrics=oos_metrics,
            regime_breakdown=regime_breakdown,
            walk_forward_efficiency=wfe,
            wfe_passed=wfe_passed,
            stress_survival_passed=stress_survival_passed,
            execution_authority=False,
        )
