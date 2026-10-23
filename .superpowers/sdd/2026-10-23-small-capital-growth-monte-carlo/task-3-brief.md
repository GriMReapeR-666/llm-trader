# Task 3 Brief: Account Flip & Tiered Drawdown Preservation Manager

**Files:**
- Modify: `core/growth_and_ruin_engine.py`
- Modify/Test: `tests/test_growth_and_ruin_engine.py`

**Requirements:**
1. Implement `RiskBucket(str, Enum)`: `CORE`, `GROWTH`, `HEDGE`.
2. Implement `AccountFlipState` dataclass:
   - `starting_capital`, `current_equity`, `high_water_mark`, `active_drawdown`, `flip_count`, `risk_multiplier`, `is_halted`, `bucket_allocations: Dict[RiskBucket, float]`, `execution_authority = False`, `to_dict()`.
3. Implement `AccountFlipController`:
   - `__init__(self, starting_capital: float = 1000.0, flip_target_mult: float = 2.0, emit_telemetry: bool = True)`
   - `update_equity(self, equity: float) -> AccountFlipState`:
     - Checks if equity >= starting_capital * flip_target_mult: triggers flip milestone, locks in baseline, resets HWM, emits `ACCOUNT_FLIP_TRIGGERED`.
     - Tiered Drawdown:
       - 10% DD: halving risk (`risk_multiplier = 0.50`), emits `DRAWDOWN_DEFENSE_TIER1`.
       - 15% DD: disables `GROWTH` bucket, cuts risk to 0.25, emits `DRAWDOWN_DEFENSE_TIER2`.
       - 20% DD: halts trading (`is_halted = True`, `risk_multiplier = 0.0`), emits `DRAWDOWN_HALT_TRIGGERED`.
4. Add `test_account_flip_controller` in `tests/test_growth_and_ruin_engine.py`.
5. Run tests with `rtk pytest tests/test_growth_and_ruin_engine.py -k test_account_flip_controller -o addopts="" -v`.
6. Commit with `rtk git commit -m "feat(preservation): implement AccountFlipController with tiered drawdown defense"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-23-small-capital-growth-monte-carlo/task-3-report.md`.
