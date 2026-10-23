# Task 2 Brief: High-Speed Numba `@njit` Indicator & Simulation Kernels

**Files:**
- Modify: `core/strategy_universe.py`
- Modify/Test: `tests/test_strategy_universe.py`

**Requirements:**
1. Implement the following Numba-accelerated kernels with `@nb.njit(fastmath=True)`:
   - `compute_ema_numba(prices: np.ndarray, period: int) -> np.ndarray`:
     Recursive exponential moving average with alpha = 2.0 / (period + 1.0).
   - `compute_bollinger_bands_numba(prices: np.ndarray, period: int, num_std: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]`:
     Computes (upper, mid, lower) rolling mean and standard deviation bands.
   - `simulate_positions_numba(prices: np.ndarray, signals: np.ndarray, spread_bps: float = 2.0, slippage_bps: float = 1.0, initial_capital: float = 10000.0) -> Tuple[np.ndarray, np.ndarray, int, float, float]`:
     Vectorized position state machine with transaction costs (spread_bps + slippage_bps), returning `(equity, trade_pnls, trade_count, total_return, max_dd)`.
   - `calculate_fast_metrics_numba(equity: np.ndarray, returns: np.ndarray) -> Tuple[float, float, float, float, float, float]`:
     Computes annualized Sharpe, Sortino, Calmar, Win Rate, Profit Factor, and Max Drawdown.
2. In `tests/test_strategy_universe.py`, write `test_numba_indicators_and_simulation()` verifying all 4 functions on sample arrays.
3. Verify test failure first, then add implementation in `core/strategy_universe.py`, verify test pass.
4. Run tests with `rtk pytest tests/test_strategy_universe.py -k test_numba_indicators_and_simulation -v`.
5. Commit with `rtk git commit -m "feat(universe): implement high-speed Numba indicators, position simulation, and metrics"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-strategy-universe-matrix/task-2-report.md`.
