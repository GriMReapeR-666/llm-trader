# Task 1 Report: Small-Capital Compounding & Sizing Engine

**Status:** DONE
**Commit Hash:** `6d880025`
**Timestamp:** 2026-10-23T05:25:21+05:30

## Overview of Implementation
Implemented the small-capital compounding, sizing, and sequence-of-returns risk engine in `core/growth_and_ruin_engine.py` with full TDD verification in `tests/test_growth_and_ruin_engine.py`.

### Components Delivered:
1. **`SizingMode(str, Enum)`**:
   - `FIXED_FRACTIONAL`: Fixed percentage risk of current equity.
   - `VOLATILITY_TARGETING`: Dynamically scales risk percentage inversely to volatility multiplier.
   - `HALF_KELLY`: Constrained half-Kelly sizing bounded between 0.5% and 2.0%.
   - `DYNAMIC_LEVERAGE`: Gradually steps down risk fraction as drawdown approaches `kill_dd_pct`.

2. **`GrowthSimulationResult` (Dataclass)**:
   - Stores `starting_capital`, `terminal_equity`, `net_pnl`, `total_trades`, `max_drawdown`, `is_ruined`, `equity_path`, and fail-closed flag `execution_authority = False`.
   - Includes `to_dict()` helper for serialization.

3. **`SmallCapitalGrowthSimulator`**:
   - `simulate_path(...)`: Executes compounding paths trade-by-trade, enforcing ruin cutoffs, tracking high water mark and drawdown, and triggering early kill-switch halt when drawdown exceeds threshold.
   - `simulate_sequence_risk(...)`: Shuffles trade sequence over $N$ Monte Carlo permutations to measure path-dependency, percentiles (median, p5, p95), mean drawdown, and probability of drawdown over threshold.
   - `detect_equity_drift(...)`: Compares rolling window mean PnL against preceding window to detect performance degradation / regime decay exceeding threshold.

## TDD Verification
- **Test Suite:** `tests/test_growth_and_ruin_engine.py`
- **Initial Verification:** Executed tests prior to module creation, verifying expected `ModuleNotFoundError: No module named 'core.growth_and_ruin_engine'`.
- **Pass Verification:**
  - `test_small_capital_compounding_fixed_fractional`: PASSED
  - `test_small_capital_compounding_kill_switch`: PASSED
  - `test_small_capital_compounding_other_sizing_modes`: PASSED
  - `test_sequence_of_returns_risk`: PASSED
  - `test_equity_drift_detection`: PASSED
  - **Summary:** 5 passed in 11.60s.
