# Task 4 Brief: Complete Integration Test, Event Telemetry & Verification

**Files:**
- Modify: `tests/test_growth_and_ruin_engine.py`

**Requirements:**
1. Implement `test_full_growth_and_ruin_integration` in `tests/test_growth_and_ruin_engine.py`:
   - Simulates compounding growth with `SmallCapitalGrowthSimulator`.
   - Simulates Markov regime Monte Carlo with `AdvancedMonteCarloEngine`.
   - Simulates dynamic equity evolution triggering `ACCOUNT_FLIP_TRIGGERED` on `AccountFlipController`.
   - Verifies event bus emissions through mock `core.agent_event_bus.emit_agent_event`.
2. Run full test suite: `rtk pytest tests/test_growth_and_ruin_engine.py -o addopts="" -v`.
3. Update knowledge graph: `rtk graphify update .`.
4. Commit with `rtk git commit -m "test(growth): add full integration test and event bus verification"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-23-small-capital-growth-monte-carlo/task-4-report.md`.
