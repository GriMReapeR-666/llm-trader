# Task 1 Brief: 4-Tier Evaluation Checkers

**Files:**
- Create: `core/diagnostics_engine.py`
- Test: `tests/test_diagnostics_engine.py`

**Requirements:**
1. Implement `DataIntegrityReport` and `DataIntegrityChecker`:
   - Inspects `pl.DataFrame` for empty state, missing 'close', negative/zero prices, and consecutive stale price runs.
2. Implement `SignalQualityReport` and `SignalQualityChecker`:
   - Analyzes sub-period fold Sharpe stability and regime consistency percentage.
3. Implement `ExecutionCheckReport` and `ExecutionFeasibilityChecker`:
   - Simulates 1x vs 3x slippage using `simulate_positions_numba` from `core.strategy_universe`.
   - Flags severe slippage sensitivity (>60% degradation or negative return under 3x slippage).
4. Implement `PortfolioFitReport` and `PortfolioFitChecker`:
   - Calculates Pearson correlation between strategy returns and portfolio returns; flags correlation > 0.70.
5. In `tests/test_diagnostics_engine.py`, write tests for `DataIntegrityChecker`, `SignalQualityChecker`, and `PortfolioFitChecker`.
6. Run tests with `rtk pytest tests/test_diagnostics_engine.py -o addopts="" -v`.
7. Commit with `rtk git commit -m "feat(diagnostics): implement 4-tier evaluation checkers"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-diagnostics-evaluation-engine/task-1-report.md`.
