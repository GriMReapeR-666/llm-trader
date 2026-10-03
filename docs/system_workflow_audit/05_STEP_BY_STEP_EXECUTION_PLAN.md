# Autonomous Trading System Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge backtesting outputs, live broker scanner feeds, and alternative data into the Command Deck API, ensuring 100% autonomous operation, complete UI observability, and zero unpopulated screens.

**Architecture:** Refactor `ui/gui_api.py` data ingestion paths to pull from `backtest_report.json` and active MT5 client sessions; wire `economic_dynamics_catalog` fallback; verify TDD test suite and ensure all 30 endpoints return live, non-empty telemetry.

**Tech Stack:** Python 3.13, FastAPI, Uvicorn, Bun, TypeScript, OpenTUI, MetaTrader 5, SQLite.

**Spec:** `docs/system_workflow_audit/01_COMPREHENSIVE_SYSTEM_AUDIT_AS_IS_VS_TO_BE.md`

## Global Constraints
- Prefix all shell commands with `rtk` (or `etk`).
- Shell is `pwsh` on Windows.
- Maintain fail-closed safety: never fabricate fake trading signals or bypass risk limits.
- Zero boilerplate; keep diffs minimal and precise.

---

### Task 1: Wire `/api/backtests` Endpoint to Real Backtest Reports

**Files:**
- Modify: `ui/gui_api.py:1186-1209`
- Test: `tests/test_api_endpoints.py` (or verify via `scratch/probe_all_endpoints.py`)

**Interfaces:**
- Consumes: `backtest_report.json` and `auto_quant_tools_state.json`
- Produces: `List[Dict]` for OpenTUI `BacktestScreen.tsx` with keys `id`, `strategyId`, `period`, `status`, `profitFactor`, `sharpe`, `sortino`, `calmar`, `maxDD`, `winRate`, `totalTrades`, `expectancyR`, `equityCurve`, `monthlyReturns`.

- [ ] **Step 1: Write test verifying `/api/backtests` returns non-empty list**
```python
def test_backtests_endpoint_returns_data():
    from ui.gui_api import _backtests
    res = _backtests()
    assert isinstance(res, list)
    assert len(res) >= 1
    assert "sharpe" in res[0]
    assert "equityCurve" in res[0]
```

- [ ] **Step 2: Run test to verify failure**
Run: `rtk python -c "from ui.gui_api import _backtests; res = _backtests(); assert len(res) > 0"`
Expected: FAIL (`AssertionError`)

- [ ] **Step 3: Update `_backtests()` in `ui/gui_api.py`**
Incorporate `backtest_report.json` parsing when `_aq.get("backtests")` is empty, formatting fields to match `BacktestScreen.tsx` requirements.

- [ ] **Step 4: Run test to verify it passes**
Run: `rtk python -c "from ui.gui_api import _backtests; res = _backtests(); assert len(res) > 0; print('Passed, items:', len(res))"`
Expected: PASS

---

### Task 2: Inject Authenticated MT5 Client into `/api/scanner`

**Files:**
- Modify: `ui/gui_api.py:1467-1490`
- Test: `tests/test_scanner_fail_closed.py`

**Interfaces:**
- Consumes: `MT5Client` from `mt5_trader`
- Produces: Live ranked opportunity rows with `score`, `spread`, `volatility`, and setup `reasons`.

- [ ] **Step 1: Test current scanner endpoint output**
Run: `rtk python -c "from ui.gui_api import _scanner; print('Current scanner:', _scanner())"`
Expected: Output `[]` (empty due to `client=None`)

- [ ] **Step 2: Update `_scanner()` in `ui/gui_api.py`**
Reuse the existing initialized `_mt5_client` or instantiate `MT5Client(_cfg)` with login check so that if MT5 is online, real live prices and spreads are scanned.

- [ ] **Step 3: Verify scanner returns active opportunities when MT5 is connected**
Run: `rtk python -c "from ui.gui_api import _scanner; rows = _scanner(); print('Scanner rows:', len(rows))"`
Expected: Live items returned with real spreads and scores.

---

### Task 3: Resolve `economic_dynamics_catalog` Import in Alternative Data

**Files:**
- Modify: `core/auto_quant_tools.py` / `data/`
- Test: `scratch/probe_all_endpoints.py`

**Interfaces:**
- Consumes: Economic indicator catalog
- Produces: `health: "healthy"` in `alternative_data_health` status.

- [ ] **Step 1: Locate `economic_dynamics_catalog` references**
Run: `rtk grep "economic_dynamics_catalog" core/ data/`

- [ ] **Step 2: Add graceful fallback or correct module path**
Ensure that FRED, CFTC, and Alternative Free-Stack degrade gracefully without throwing `ModuleNotFoundError` string in the health detail.

- [ ] **Step 3: Verify clean health reporting**
Run: `rtk python -c "from core.auto_quant_tools import get_tool_state; print(get_tool_state().get('last_results', {}).get('alternative_data_health', {}))"`
Expected: Clean status without missing module errors.

---

### Task 4: Populate Model Registry & Active Tasks Endpoints

**Files:**
- Modify: `ui/gui_api.py:1280-1295` and `ui/gui_api.py:1927-1940`
- Test: `scratch/probe_all_endpoints.py`

**Interfaces:**
- Consumes: `model_registry.py` and `operations/agent_operations.py`
- Produces: JSON lists for Command Deck Model Management and Agent Tasks screens.

- [ ] **Step 1: Update `_tasks()` to query active `agent_runs`**
Query `agent_runs` table from `trading_state.db` and return active tasks and recent execution phases.

- [ ] **Step 2: Update `_model_registry()` to list registered models**
Query `list_models()` from `model_registry` and return neural/tabular model artifacts (`rl_model.json`, `ppo_position_mgmt.json`, `kronos_mcp`).

- [ ] **Step 3: Verify endpoints return non-empty datasets**
Run: `rtk python -c "from ui.gui_api import _tasks, _model_registry; print('Tasks:', len(_tasks()), 'Models:', len(_model_registry()))"`
Expected: Non-empty datasets returned.

---

### Task 5: End-to-End System Verification & OpenTUI Telemetry Validation

**Files:**
- Verify: `START_DECK.bat`
- Verify: All 30 Command Deck endpoints
- Verify: OpenTUI screen rendering (`BacktestScreen`, `StrategiesScreen`, `PositionsScreen`)

- [ ] **Step 1: Run comprehensive endpoint probe**
Run: `rtk python scratch/probe_all_endpoints.py`
Expected: 100% of endpoints return HTTP 200 with populated datasets.

- [ ] **Step 2: Update Graphify Knowledge Graph**
Run: `graphify update .` to index all new architecture documentation, ensuring the project's knowledge graph reflects the complete operational workflow.
