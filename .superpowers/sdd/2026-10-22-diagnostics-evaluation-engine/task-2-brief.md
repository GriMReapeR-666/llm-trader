# Task 2 Brief: Phase 1 & Phase 2 Diagnostics Pipeline

**Files:**
- Modify: `core/diagnostics_engine.py`
- Modify/Test: `tests/test_diagnostics_engine.py`

**Requirements:**
1. In `core/diagnostics_engine.py`, implement `DiagnosticsPipeline`:
   - `__init__(self, emit_telemetry: bool = True)`
   - `run_phase1_quick_pass(self, specs: List[StrategySpec], dataset: pl.DataFrame, min_sharpe: float = 0.30, max_dd: float = 30.0) -> List[StrategySpec]`:
     Runs fast simulation across all candidate specs; filters out failing Sharpe/drawdown/trades; emits event `DIAGNOSTIC_PHASE1_COMPLETED`.
   - `run_phase2_tier_audit(self, survivors: List[StrategySpec], dataset: pl.DataFrame) -> List[StrategySpec]`:
     Verifies data integrity and execution feasibility under slippage for all survivors; emits event `DIAGNOSTIC_PHASE2_COMPLETED`.
2. Add `test_diagnostics_pipeline_phase1_and_phase2` in `tests/test_diagnostics_engine.py`.
3. Verify test failure first, then implementation, then test pass.
4. Run tests with `rtk pytest tests/test_diagnostics_engine.py -k test_diagnostics_pipeline_phase1_and_phase2 -o addopts="" -v`.
5. Commit with `rtk git commit -m "feat(diagnostics): add Phase 1 quick pass and Phase 2 audit pipeline"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-diagnostics-evaluation-engine/task-2-report.md`.
