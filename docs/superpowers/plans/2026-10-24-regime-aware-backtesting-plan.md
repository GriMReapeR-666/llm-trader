# Part 4: Regime-Aware Backtesting Engine — Implementation Plan

**Goal:** Implement the institutional regime-aware backtesting engine (`core/regime_backtester.py`) supporting multi-period partitioning (IS/VAL/OOS/WFO), 6-regime classification, 15-metric performance evaluation, and rich CLI reporting.

---

## Task 1: Regime Classification & Multi-Period Partitioning Engine (TDD)
- **Files to create/modify:**
  - `core/regime_backtester.py`
  - `tests/test_regime_backtester.py`
- **Steps:**
  1. Write failing tests in `tests/test_regime_backtester.py` for `RegimeClassifier` (`BULL`, `BEAR`, `SIDEWAYS`, `HIGH_VOL`, `LOW_VOL`, `STRESS`) and `MultiPeriodSplitter` (IS 50%, VAL 25%, OOS 25%, rolling WFO splits).
  2. Implement `RegimeClassifier` using rolling returns, ATR volatility, and drawdown peak drops.
  3. Implement `MultiPeriodSplitter`.
  4. Verify all tests pass (GREEN).

## Task 2: 15-Metric Quantitative Evaluation & Numba Simulation Engine (TDD)
- **Files to create/modify:**
  - `core/regime_backtester.py`
  - `tests/test_regime_backtester.py`
- **Steps:**
  1. Write failing tests for 15-metric computation: `PnLDistribution` (mean, std, skew, kurt, VaR 95, CVaR 95), `StrategyRegimeMetrics` (Sharpe, Sortino, Calmar, MaxDD, Win Rate, Win/Loss ratio, Expectancy, Turnover, Exposure, Trade Duration, Slippage-adjusted Return).
  2. Implement `compute_15_metrics()` and `simulate_regime_backtest()`.
  3. Verify all tests pass (GREEN).

## Task 3: Regime-Aware Backtest Pipeline & Rich CLI Visualizer
- **Files to create/modify:**
  - `core/regime_backtester.py`
  - `mt5_trader.py`
  - `tests/test_regime_backtester.py`
- **Steps:**
  1. Implement `RegimeBacktestPipeline.run_backtest(spec, dataset)`.
  2. Add formatted ASCII tables displaying IS/VAL/OOS/Regime performance breakdown.
  3. Wire up CLI command in `mt5_trader.py`: `quant regime-backtest --symbol <SYM> --timeframe <TF> --bars <N>`.
  4. Verify end-to-end via CLI and test suite.
