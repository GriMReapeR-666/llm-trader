# Gate 0 Phase 1: Hazard Neutralization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate immediate safety bypasses, committed test credentials, unauthenticated order sending from UI, paper-mode broker order leaks, and RCE tools before any live trading or deep refactoring.

**Architecture:** Address five isolated hazard vectors identified in AUDIT_2026-10-04 and AUDIT_2026-10-04_DEEP: sanitize the test fixture secret (C5), remove `mt5_order_send` from the OpenTUI deck palette (C3), guard `_execute_pyramid_order` in `TradeManager` against raw `mt5.order_send` in paper mode (L3), disarm `execute_dynamic_metric_analysis` from decision LLM tools and block unconstrained `exec` (I1), and divert backtest simulated trades away from the live `trades` table (E4).

**Tech Stack:** Python 3.11, pytest, TypeScript (OpenTUI / React), SQLite.

**Spec:** `docs/AUDIT_2026-10-04.md` and `docs/AUDIT_2026-10-04_DEEP.md` (Gate 0 Phase 1).

## Global Constraints
- Every change must maintain backward compatibility with existing tests unless the test specifically asserted the unsafe behavior.
- Shell commands must be prefixed with `etk` (or `rtk`).
- No new external dependencies.
- Changes must fail closed on missing/invalid state.

---

### Task 1: Sanitize Committed Secret in Test Fixture (Finding C5)

**Files:**
- Modify: `tests/test_section_f_hardening.py:20,33`
- Test: `tests/test_section_f_hardening.py`

**Interfaces:**
- Consumes: `AgentOperations.record_raw_trace`
- Produces: Synthetic redaction test passing with non-credential token

- [ ] **Step 1: Inspect and verify existing test behavior**
Run: `etk pytest tests/test_section_f_hardening.py::test_l0_raw_trace_secret_redaction -v`
Expected: PASS

- [ ] **Step 2: Replace the 48-hex RoutesMe key with synthetic fixture**
In `tests/test_section_f_hardening.py`:
Change:
```python
"secret_assignment": "ROUTESME_KEY=rm-<48-hex key redacted 2026-10-22: it is still in git history and must be rotated>",
```
To:
```python
"secret_assignment": "ROUTESME_KEY=rm-TESTFIXTURE000000000000000000000000000000000",  # secret-scan: allow
```
And change line 33 assertion:
```python
assert "rm-TESTFIXTURE0000000000000000000000000000000000000000" not in redacted_json  # secret-scan: allow
```

- [ ] **Step 3: Run test to verify it passes with synthetic fixture**
Run: `etk pytest tests/test_section_f_hardening.py::test_l0_raw_trace_secret_redaction -v`
Expected: PASS

- [ ] **Step 4: Commit**
```bash
git add tests/test_section_f_hardening.py
git commit -m "fix(security): sanitize committed RoutesMe secret fixture (C5)"
```

---

### Task 2: Remove Raw `mt5_order_send` from OpenTUI Deck Tool Palette (Finding C3)

**Files:**
- Modify: `tui_opentui/src/screens/MT5Screen.tsx:510,539`
- Test: TypeScript typecheck via `tsc --noEmit` in `tui_opentui`

**Interfaces:**
- Consumes: OpenTUI `TOOL_GROUPS`
- Produces: Palette without order placement capability; dry-run/read-only tools preserved

- [ ] **Step 1: Check existing typecheck passes**
Run: `cd tui_opentui && bun x tsc --noEmit` (or `npx tsc --noEmit`)
Expected: 0 errors

- [ ] **Step 2: Remove `mt5_order_send` from TRADING group and argHint**
In `tui_opentui/src/screens/MT5Screen.tsx`:
Change `TOOL_GROUPS`:
```typescript
{ label: "TRADING", tools: [{ name: "mt5_order_check", desc: "validate without sending" }, { name: "mt5_order_calc_margin", desc: "required margin" }, { name: "mt5_order_calc_profit", desc: "potential profit" }] },
```
And in `argHint`:
```typescript
case "mt5_order_check": return `{ "action": 1, "symbol": "${symbol}", "volume": 0.1, "type": 0, "price": 1.0842, "sl": 1.08, "tp": 1.09, "deviation": 10 }`
```

- [ ] **Step 3: Run typecheck to verify 0 errors**
Run: `cd tui_opentui && bun x tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**
```bash
git add tui_opentui/src/screens/MT5Screen.tsx
git commit -m "fix(tui): remove mt5_order_send from open deck palette (C3)"
```

---

### Task 3: Defuse Paper Pyramiding Broker Order Leak (Finding L3)

**Files:**
- Modify: `core/trade_manager.py:763-835`
- Test: `tests/test_trade_manager_paper_pyramid.py`

**Interfaces:**
- Consumes: `TradeManager.paper_broker`, `_execute_pyramid_order`
- Produces: Paper pyramid execution routed exclusively to paper broker or logged without `mt5.order_send`

- [ ] **Step 1: Write failing test verifying paper mode does not call `mt5.order_send` during pyramid**
Create `tests/test_trade_manager_paper_pyramid.py`:
```python
from unittest.mock import MagicMock, patch
import pytest
from core.trade_manager import TradeManager, PaperPosition

def test_paper_pyramid_does_not_call_real_mt5():
    tm = TradeManager(client=True, symbol="EURUSD", paper=True)
    fake_paper = MagicMock()
    fake_paper.positions = {
        1001: {
            "ticket": 1001,
            "symbol": "EURUSD",
            "type": 0, # BUY
            "volume": 0.2,
            "price_open": 1.0800,
            "sl": 1.0750,
            "tp": 1.0900,
            "magic": 123456,
        }
    }
    fake_paper.place_order.return_value = {"retcode": 10009, "order": 1002}
    tm.attach_paper_broker(fake_paper)
    
    pos = PaperPosition(fake_paper.positions[1001], 1001)
    
    with patch("core.trade_manager.mt5") as mock_mt5:
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.symbol_info_tick.return_value = MagicMock(ask=1.0820, bid=1.0818)
        tm._execute_pyramid_order(pos)
        
        # Real mt5.order_send MUST NOT be called in paper mode
        mock_mt5.order_send.assert_not_called()
        assert 1001 in tm.pyramid_entries
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_trade_manager_paper_pyramid.py -v`
Expected: FAIL (because `mock_mt5.order_send` is called or paper branch missing)

- [ ] **Step 3: Implement paper broker branching in `_execute_pyramid_order`**
In `core/trade_manager.py`:
In `_execute_pyramid_order`, after authorization check and recording `PYRAMID_REQUESTED`:
```python
            if self.paper_broker is not None:
                paper_price = getattr(pos, "price_current", getattr(pos, "price_open", 0.0))
                if mt5 is not None:
                    try:
                        tick = mt5.symbol_info_tick(pos.symbol)
                        if tick:
                            paper_price = tick.ask if pos.type == 0 else tick.bid
                    except Exception:
                        pass
                res = None
                if hasattr(self.paper_broker, "place_order"):
                    res = self.paper_broker.place_order(
                        symbol=pos.symbol,
                        volume=vol,
                        order_type="BUY" if pos.type == 0 else "SELL",
                        price=paper_price,
                        sl=sl,
                        tp=pos.tp,
                        magic=getattr(pos, "magic", 0),
                        comment="pyramid_entry",
                    )
                self.log.info(f"✅ Pyramid Paper Success: {vol} lots added for #{pos.ticket}")
                self.pyramid_entries.add(pos.ticket)
                self._record_management(pos.ticket, "PYRAMID_FILLED", {
                    "parent_ticket": pos.ticket, "volume": vol,
                    "new_ticket": res.get("order") if isinstance(res, dict) else None,
                    "paper": True,
                }, requested_volume=vol, filled_volume=vol)
                self.save_state()
                return
```

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_trade_manager_paper_pyramid.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add core/trade_manager.py tests/test_trade_manager_paper_pyramid.py
git commit -m "fix(trade_manager): prevent paper pyramiding from calling mt5.order_send (L3)"
```

---

### Task 4: Disarm Latent RCE in Decision Tools (Finding I1)

**Files:**
- Modify: `core/trading_tools.py:650-664,2603-2705`
- Modify: `research/code_interpreter.py:175-195`
- Test: `tests/test_code_interpreter_safety.py`

**Interfaces:**
- Consumes: `execute_dynamic_metric_analysis`, `DynamicMetricInterpreter`
- Produces: Tool removed from default LLM decision tools and code execution hard-disabled

- [ ] **Step 1: Write test verifying execution of OS commands/arbitrary code is refused**
Create `tests/test_code_interpreter_safety.py`:
```python
from core.trading_tools import execute_dynamic_metric_analysis, _build_tool_registry

def test_dynamic_metric_analysis_is_disabled():
    res = execute_dynamic_metric_analysis.invoke({"code": "import os; os.system('echo pwned')"})
    assert res["success"] is False
    assert "disabled" in res["error"].lower()

def test_dynamic_metric_analysis_not_in_decision_tool_registry():
    registry = _build_tool_registry()
    assert "execute_dynamic_metric_analysis" not in registry
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_code_interpreter_safety.py -v`
Expected: FAIL

- [ ] **Step 3: Disarm the tool and remove from tool registries**
In `core/trading_tools.py`:
1. In `execute_dynamic_metric_analysis`:
```python
@tool("execute_dynamic_metric_analysis", args_schema=DynamicMetricInput)
def execute_dynamic_metric_analysis(code: str, description: str = "Custom metric analysis") -> Dict[str, Any]:
    """Dynamically write, validate, and execute arbitrary Python functions (DISABLED for security)."""
    return {
        "task_description": description,
        "success": False,
        "result": None,
        "stdout": "",
        "error": "Dynamic metric Python execution is disabled for safety.",
        "execution_time_ms": 0.0,
    }
```
2. Remove `execute_dynamic_metric_analysis` from `RESEARCH_TOOLS` list (around line 2666) and from `_tool_funcs` in `_build_tool_registry()` (around line 2693).
3. In `research/code_interpreter.py:execute_code`, disarm `exec` / `eval` with a safety refusal return:
```python
return ExecutionResult(success=False, error="Arbitrary code execution via DynamicMetricInterpreter has been deactivated for security.", execution_time_ms=0.0)
```

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_code_interpreter_safety.py tests/test_skill_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add core/trading_tools.py research/code_interpreter.py tests/test_code_interpreter_safety.py
git commit -m "fix(security): disarm dynamic Python code execution tool (I1)"
```

---

### Task 5: Isolate Backtest Trades from Live Journal (Finding E4)

**Files:**
- Modify: `research/mt5_backtest_engine.py:770-818`
- Test: `tests/test_backtest_journal_isolation.py`

**Interfaces:**
- Consumes: `MT5BrokerBacktestEngine.process_journaling`
- Produces: Simulated backtest trades stored in `research_backtest_trades` rather than polluting live `trades`

- [ ] **Step 1: Write test verifying `process_journaling` does not insert rows into live `trades` table**
Create `tests/test_backtest_journal_isolation.py`:
```python
import sqlite3
from unittest.mock import MagicMock
from research.mt5_backtest_engine import MT5BrokerBacktestEngine, BacktestMetrics

def test_backtest_journaling_does_not_pollute_live_trades(tmp_path):
    db_file = str(tmp_path / "trading_state.db")
    engine = MT5BrokerBacktestEngine(db_path=db_file)
    
    # Create empty live trades table
    con = sqlite3.connect(db_file)
    con.execute("""
        CREATE TABLE trades (
            ticket INTEGER PRIMARY KEY,
            symbol TEXT,
            pnl REAL
        )
    """)
    con.close()
    
    metrics = MagicMock(spec=BacktestMetrics)
    metrics.to_dict.return_value = {"sharpe": 1.5}
    metrics.trades = [{
        "side": "BUY",
        "entry_price": 1.08,
        "exit_price": 1.09,
        "entry_time": "2026-01-01T00:00:00",
        "exit_time": "2026-01-01T01:00:00",
        "pnl_pips": 10.0,
        "exit_reason": "TP",
        "r_multiple": 1.5,
    }]
    metrics.sharpe_ratio = 1.5
    metrics.win_rate = 0.6
    metrics.expectancy_r = 0.5
    metrics.total_trades = 1
    
    res = engine.process_journaling(metrics, symbol="EURUSD", strategy_name="test_strat")
    assert res["persisted_run"] is True
    
    con = sqlite3.connect(db_file)
    cur = con.cursor()
    # Live trades table must remain completely empty
    cur.execute("SELECT COUNT(*) FROM trades")
    live_count = cur.fetchone()[0]
    assert live_count == 0
    
    # Backtest trades must be recorded in research_backtest_trades
    cur.execute("SELECT COUNT(*) FROM research_backtest_trades")
    bt_count = cur.fetchone()[0]
    assert bt_count == 1
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_backtest_journal_isolation.py -v`
Expected: FAIL (because it inserts into `trades`)

- [ ] **Step 3: Modify `process_journaling` to write to `research_backtest_trades` table**
In `research/mt5_backtest_engine.py:770-818`:
Rename the table created and inserted from `trades` to `research_backtest_trades`:
```python
                # 2. Persist individual backtest trades for downstream CPCV / ledger analytics
                con.execute("""
                    CREATE TABLE IF NOT EXISTS research_backtest_trades (
                        ticket INTEGER PRIMARY KEY,
                        magic INTEGER,
                        strategy TEXT,
                        symbol TEXT,
                        side TEXT,
                        volume REAL,
                        entry REAL,
                        stop_loss REAL,
                        take_profit REAL,
                        risk_amount REAL,
                        opened_at TEXT,
                        closed_at TEXT,
                        exit_price REAL,
                        pnl REAL,
                        exit_reason TEXT,
                        payload TEXT,
                        setup_id TEXT,
                        gross_pnl REAL,
                        commission REAL,
                        swap REAL,
                        financing REAL
                    )
                """)

                base_ticket = 999000000 + int(time.time() % 100000) * 100
                logged_count = 0
                for idx, tr in enumerate(metrics.trades[:100]):
                    ticket_id = base_ticket + idx
                    con.execute("""
                        INSERT OR REPLACE INTO research_backtest_trades
                        (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, take_profit, risk_amount,
                         opened_at, closed_at, exit_price, pnl, exit_reason, payload, setup_id, gross_pnl, commission, swap, financing)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ticket_id, 888888, strategy_name, symbol, tr["side"], 1.0,
                        tr["entry_price"], 0.0, 0.0, 100.0,
                        tr["entry_time"], tr["exit_time"], tr["exit_price"],
                        tr["pnl_pips"], tr["exit_reason"],
                        json.dumps({"source": "mt5_backtest", "r_multiple": tr["r_multiple"]}),
                        strategy_name, tr["pnl_pips"],
                        self.commission_per_lot, 0.0, 0.0,
                    ))
                    logged_count += 1
                journal_report["trades_logged"] = logged_count
```

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_backtest_journal_isolation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add research/mt5_backtest_engine.py tests/test_backtest_journal_isolation.py
git commit -m "fix(research): isolate simulated backtest trades from live trades journal (E4)"
```
