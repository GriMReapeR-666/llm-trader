# Task 4 Brief: Complete Pipeline Integration, Event Bus Telemetry & Verification

**Files:**
- Modify: `tests/test_diagnostics_engine.py`

**Requirements:**
1. Implement `test_full_diagnostics_pipeline_end_to_end()` in `tests/test_diagnostics_engine.py`:
   - Runs full 1,000 bar multi-strategy simulation through Phase 1, Phase 2, and Phase 3.
   - Verifies telemetry emission of `DIAGNOSTIC_PHASE1_COMPLETED`, `DIAGNOSTIC_PHASE2_COMPLETED`, and `DIAGNOSTIC_PHASE3_COMPLETED`.
2. Run all tests with `rtk pytest tests/test_diagnostics_engine.py -o addopts="" -v`.
3. Update knowledge graph: `rtk graphify update .`.
4. Commit with `rtk git commit -m "test(diagnostics): add full end-to-end pipeline integration test and event verification"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-diagnostics-evaluation-engine/task-4-report.md`.
