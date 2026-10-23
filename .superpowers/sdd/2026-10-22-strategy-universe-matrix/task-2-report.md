# Task 2 Report: High-Speed Numba `@njit` Indicator & Simulation Kernels

- **Status**: DONE
- **Commit Hash**: `d105c9f` (`d105c9fbf3eea2e80c1c70d299b45030a0b24125`)
- **Files Modified**:
  - `core/strategy_universe.py`
  - `tests/test_strategy_universe.py`

## Implementation Details
1. **Numba-Accelerated Kernels (`@nb.njit(fastmath=True)`)**:
   - `compute_ema_numba(prices: np.ndarray, period: int) -> np.ndarray`:
     Recursive exponential moving average calculation with smoothing factor `alpha = 2.0 / (period + 1.0)` and empty array safety.
   - `compute_bollinger_bands_numba(prices: np.ndarray, period: int, num_std: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]`:
     Computes rolling window mean and standard deviation returning `(upper, mid, lower)` bands with lookback window indexing.
   - `simulate_positions_numba(prices: np.ndarray, signals: np.ndarray, spread_bps: float = 2.0, slippage_bps: float = 1.0, initial_capital: float = 10000.0) -> Tuple[np.ndarray, np.ndarray, int, float, float]`:
     High-speed vectorized position execution state machine modeling long (+1), short (-1), and flat (0) signals. Applies transaction costs (`spread_bps` + `slippage_bps`) on entry/exit transitions, tracks peak-to-trough drawdown, and returns `(equity, trade_pnls, trade_count, total_return, max_dd)`.
   - `calculate_fast_metrics_numba(equity: np.ndarray, returns: np.ndarray) -> Tuple[float, float, float, float, float, float]`:
     Calculates institutional-grade risk/return metrics: annualized Sharpe ratio, downside-risk Sortino ratio, Calmar ratio (return-to-max-drawdown), Win Rate, Profit Factor, and Max Drawdown with zero-division guards.

## TDD Verification & Test Summary
1. **Red Phase**:
   - Added `test_numba_indicators_and_simulation` in `tests/test_strategy_universe.py`.
   - Ran `rtk pytest tests/test_strategy_universe.py -k test_numba_indicators_and_simulation -v`.
   - Verified failure: `ImportError: cannot import name 'compute_ema_numba' from 'core.strategy_universe'`.
2. **Green Phase**:
   - Implemented the 4 `@nb.njit(fastmath=True)` kernels in `core/strategy_universe.py`.
   - Included edge-case tests (empty price arrays, single-element arrays).
   - Ran `rtk pytest tests/test_strategy_universe.py -k test_numba_indicators_and_simulation -v`:
     - `test_numba_indicators_and_simulation`: PASSED
   - Ran full test suite: `rtk pytest tests/test_strategy_universe.py -v`:
     - 3 passed in 1.48s (`test_taxonomy_dimensions`, `test_strategy_spec_creation`, `test_numba_indicators_and_simulation`).
