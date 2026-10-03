# Task 1 Addendum: Repository Grounding & Graphify AST Provenance

**Author session**: resumed agent (Cline), 2026-09-22 00:0x-00:3x
**Base**: Task 1 was already implemented and committed by the parallel session (`fbef192`),
Tasks 2-6 committed through `9eb3aa5`, Task 7 in flight (`task-7-brief.md`).
**Scope of this addendum**: an audit of the Task 1 artifact plus two correctness fixes.

---

## 1. Audit finding (why this addendum exists)

A probe of `visualizer/data/knowledge_graph_payload.json` showed that
**53 of the 106 declared `code_realization.file_path` values did not exist in this repository**:

- 34 had no file with a matching name anywhere in the tree
  (`intelligence/meta_adaptive_loop.py`, `core/kelly_position_sizer.py`,
  `core/circuit_breakers.py`, `operations/shadow_deploy.py`,
  `research/fisher_information.py`, ...),
- 18 existed under a different directory (`core/trading_memory.py` ->
  `intelligence/trading_memory.py`),
- 1 was ambiguous (`intelligence/regime_detection.py`).

The Task 1 test suite passed anyway because the test's own `EXPECTED_KEY_CODE_FILES`
list contained the same two non-existent paths, so the assertion was satisfied by the
data defect rather than by the repository. This violates Global Constraint 3
("Exact grounding in `mt5_trader.py`, `trading_knowledge_base/`, `quant_knowledge_framework/`
and `graphify-out/`") and would have rendered fabricated file paths in the Studio drawer.

## 2. Fixes applied

### 2.1 Repository Grounding Pass (`scripts/generate_knowledge_graph_data.py`)
- `DECLARED_PATH_ALIASES`: 53 declared-path -> real-file mappings, each verified against the
  implementing class/function symbol (`deflated_sharpe_test` in `core/overfit_detector.py`,
  `TradingPermissionGate` in `core/trading_permissions.py`, `CrashRecoveryManager` in
  `core/crash_recovery.py`, `DualVelocityExecutor` in `operations/dual_velocity.py`,
  `NewsTradingGate` in `core/news_trading.py`, `SeasonalPatternDetector` in
  `core/news_regime_correlation.py`, `SafeAlphaEvaluator` in `core/alpha_discovery.py`,
  `BayesianWalkForwardSandbox`, `DeterministicPostValidator`, `LiveExecutionSafetySupervisor`, ...).
- Resolution order: exact path -> alias -> unique basename -> `grounded: false` with a
  `grounding_note` (fail-loud, never silently fabricated).
- `declared_path` preserves the original registry name for provenance.
- `loc` is now the **measured** line count of the resolved file (was invented).
- `graphify_symbols` records the AST symbol count of that file from `graphify-out/graph.json`.

### 2.2 Graphify ingest (Task 1 Step 3 requirement, previously unimplemented)
- `append_graphify_call_edges()` reads `graphify-out/graph.json` (26,158 nodes / 47,222 links)
  and emits `code_calls` edges between curated nodes whose grounded files have real AST call
  links (`weight` = call-site count, `provenance: "graphify-out/graph.json"`).
- Capped at the 120 strongest pairs, only for pairs not already connected, so the canvas stays legible.
- `callers` / `dependencies` are re-derived from the final edge set, so they now reflect real topology.

### 2.3 Test hardening (`tests/test_knowledge_graph_data.py`, 7 -> 11 tests)
- `EXPECTED_KEY_CODE_FILES` corrected to real files (`core/meta_adaptive_engine.py`,
  `research/alpha_miner/regime_detector.py`).
- New: `test_file_paths_are_grounded_in_repository` (every grounded node must cite an existing
  file, every ungrounded node must explain itself, zero ungrounded allowed),
  `test_repointed_paths_record_their_declared_origin`,
  `test_real_loc_and_graphify_symbol_provenance`, `test_graphify_ast_provenance_edges`.

## 3. Verification evidence

```
rtk python scripts/generate_knowledge_graph_data.py
  Nodes: 109 | Edges: 332 | Subsystems: 12 | Lifecycle Stages: 7
  Grounded nodes: 109/109 | Repointed paths: 53 | Graphify AST call edges: 117

rtk pytest tests/test_knowledge_graph_data.py -v   ->  11 passed
payload SHA256 7430279D...179DF reproduced by a second consecutive run  ->  DETERMINISTIC-OK
```

## 4. Impact on Task 7 (bundler)

The payload edge count changed **215 -> 332** (117 graphify-derived). Node count (109),
subsystems (12) and lifecycle stages (7) are unchanged, and no field was removed, so
Tasks 2-6 consumers are unaffected.

`tests/test_standalone_bundle.py` hard-coded `assert len(data["edges"]) == 215`; it now
compares the bundled `window.GRAPH_DATA` against `visualizer/data/knowledge_graph_payload.json`
(`PAYLOAD_PATH` constant added). Re-run the bundler after this change so the inlined dataset
matches the current payload.
