## What and why

<!-- Link the roadmap item(s): docs/audit/ROADMAP.md IDs, e.g. `P1-C3`, `CC-F1` -->

## Checklist
- [ ] Tests written first (a failing test, or a characterization test for refactors)
- [ ] Known bugs stay as `xfail(strict=True, reason="<roadmap ID>")`; fixed ones have their marker removed
- [ ] `python -m pytest -n auto` green locally; no tracked files modified by the run
- [ ] `graphify update .` run after code changes (AGENTS.md)
- [ ] Roadmap checkbox ticked in `docs/audit/ROADMAP.md`
- [ ] No live-trading behaviour changed outside the phase plan (`docs/audit/PLAN_PHASE_1_2.md`)
