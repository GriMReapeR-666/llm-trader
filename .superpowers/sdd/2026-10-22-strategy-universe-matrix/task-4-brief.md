# Task 4 Brief: Parallel Universe Diagnostics Runner & Telemetry Event Emission

**Files:**
- Modify: `core/strategy_universe.py`
- Modify/Test: `tests/test_strategy_universe.py`

**Requirements:**
1. Implement `UniverseEvaluationReport` dataclass:
   - `total_strategies: int`
   - `evaluated_at: float`
   - `results: List[Dict[str, Any]]`
   - `execution_authority: bool = False`
   - `to_dict() -> Dict[str, Any]`
2. Implement `evaluate_strategy_universe(specs: List[StrategySpec], dataset: pl.DataFrame, spread_bps: float = 2.0, slippage_bps: float = 1.0, initial_capital: float = 10000.0, emit_telemetry: bool = True) -> UniverseEvaluationReport`:
   - Runs simulation across all specs using Numba kernels on dataset.
   - Computes return, sharpe, sortino, calmar, win rate, profit factor, max drawdown, latency ms.
   - If `emit_telemetry` is True and `core.agent_event_bus.emit_agent_event` is available, emit event `STRATEGY_UNIVERSE_EVALUATED` per strategy.
3. Add `test_evaluate_strategy_universe()` to `tests/test_strategy_universe.py`.
4. Test failure, implementation, test pass.
5. Commit with `rtk git commit -m "feat(universe): add evaluate_strategy_universe with event bus telemetry"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-strategy-universe-matrix/task-4-report.md`.
