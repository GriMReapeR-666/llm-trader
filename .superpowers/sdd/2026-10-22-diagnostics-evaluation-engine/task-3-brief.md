# Task 3 Brief: Phase 3 Purged Walk-Forward Optimization (WFO) Engine

**Files:**
- Modify: `core/diagnostics_engine.py`
- Modify/Test: `tests/test_diagnostics_engine.py`

**Requirements:**
1. In `core/diagnostics_engine.py`, implement `DiagnosticsPipelineReport` dataclass:
   - `evaluated_at: float`, `total_input: int`, `phase1_survivors: int`, `phase2_survivors: int`, `finalists: List[Dict[str, Any]]`, `execution_authority: bool = False`.
2. Implement `run_purged_walk_forward(spec: StrategySpec, dataset: pl.DataFrame, generator: StrategyUniverseGenerator, n_splits: int = 4, purge_bars: int = 5) -> Dict[str, Any]`:
   - Computes rolling train/test splits with purge buffer between them.
   - Calculates mean IS Sharpe, mean OOS Sharpe, and Walk-Forward Efficiency (WFE).
3. In `DiagnosticsPipeline`, implement `run_phase3_walk_forward(self, finalists: List[StrategySpec], dataset: pl.DataFrame, n_splits: int = 4, purge_bars: int = 5) -> DiagnosticsPipelineReport`:
   - Evaluates all finalists, emits event `DIAGNOSTIC_PHASE3_COMPLETED`.
4. Add `test_diagnostics_pipeline_phase3_walk_forward` in `tests/test_diagnostics_engine.py`.
5. Verify test pass and commit with `rtk git commit -m "feat(diagnostics): add Phase 3 purged walk-forward engine and pipeline report"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-diagnostics-evaluation-engine/task-3-report.md`.
