# Task 1 Brief: Small-Capital Compounding & Sizing Engine

**Files:**
- Create: `core/growth_and_ruin_engine.py`
- Test: `tests/test_growth_and_ruin_engine.py`

**Requirements:**
1. Implement `SizingMode(str, Enum)`: `FIXED_FRACTIONAL`, `VOLATILITY_TARGETING`, `HALF_KELLY`, `DYNAMIC_LEVERAGE`.
2. Implement `GrowthSimulationResult` dataclass:
   - `starting_capital`, `terminal_equity`, `net_pnl`, `total_trades`, `max_drawdown`, `is_ruined`, `equity_path`, `execution_authority = False`, `to_dict()`.
3. Implement `SmallCapitalGrowthSimulator`:
   - `simulate_path(starting_capital, trade_rs, sizing_mode, base_risk_pct, kill_dd_pct, vol_multipliers) -> GrowthSimulationResult`
   - `simulate_sequence_risk(starting_capital, trade_rs, n_permutations) -> Dict[str, float]`
   - `detect_equity_drift(pnls, window=20, degrade_threshold=0.25) -> Tuple[bool, float]`
4. Add tests for fixed fractional, kill switch, sequence risk, and drift detection in `tests/test_growth_and_ruin_engine.py`.
5. Run tests with `rtk pytest tests/test_growth_and_ruin_engine.py -o addopts="" -v`.
6. Commit with `rtk git commit -m "feat(growth): implement SmallCapitalGrowthSimulator and sequence risk engine"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-23-small-capital-growth-monte-carlo/task-1-report.md`.
