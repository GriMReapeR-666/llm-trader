# Operational Readiness, Trade Safety, and API Authentication Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the fail-open readiness check, add volume normalization edge diagnostics in trade management, implement token-based authentication on the GUI/TUI control API, and commit the complete verified changes to GitHub.

**Architecture:**
- **Readiness Fail-Closed Gate (`operations/readiness_report.py`)**: Replace the import fallback that returned `valid: True, status: ABSENT` on missing `strategy_trial_ledger` with fail-closed error evidence (`valid: False, status: MODULE_IMPORT_ERROR`). Ensure trial ledger chain verification explicitly treats missing required modules as blockers.
- **Volume Normalization Edge Logging (`core/trade_manager.py`)**: In `_normalize_volume`, when requested volume is greater than 0 but normalizes to 0.0 (due to broker minimum lot or step constraint), log an explicit warning detailing the truncation reason (`symbol`, `requested`, `minimum`, `step`) so micro-account partial-exit drops are audible rather than silent.
- **Control API & WebSocket Authentication Middleware (`ui/gui_api.py`)**: Implement an authentication dependency / middleware supporting `GUI_API_TOKEN` / `GUI_API_KEY`. Protect mutating and sensitive endpoints (`/api/control/*`, `/api/config/*`, `/api/internal/*`) and WebSocket connections (`/ws/harness-events`), rejecting unauthorized requests with 401 / WebSocket close code 4001, while allowing backward-compatible unauthenticated access only when no token is explicitly configured in local development.
- **End-to-End Verification and Push**: Run all corresponding unit and component tests (`test_readiness_report.py`, `test_trade_manager.py`, `test_gui_api_graph.py`), verify all pass, and push semantic commits to GitHub (`origin/main`).

**Tech Stack:** Python 3.13, FastAPI, WebSockets, SQLite3, pytest, Git.

**Spec:** Senior Developer Analysis Recommendations & Operational Findings:
1. Ensure the operational readiness check never returns a false positive or silently passes if ledger modules are missing or unimportable.
2. Ensure volume normalization edge cases (micro lots rounding to 0.0) produce visible diagnostic logging rather than silent execution drops.
3. Close the security gap on the Web/TUI API server (`gui_api.py`) by adding token authentication for control endpoints and WebSocket connections.

## Global Constraints
- Shell commands MUST use the `rtk` (or `etk`) token-killer CLI proxy on PowerShell (Windows).
- Never touch, modify, or leak `.env`, `accounts.json`, or `.bak` secret files.
- All file references in responses must be markdown clickable links with the `file:///` scheme.
- All tests must pass before committing.

---

### Task 1: Fail-Closed Readiness Gate for Module Imports and Ledgers

**Files:**
- Modify: `operations/readiness_report.py:61-70, 223-238`
- Test: `tests/test_readiness_report.py`

**Interfaces:**
- Consumes: `operations/readiness_report.py`
- Produces: `_verify_chain_cpcv()` returning `{"valid": False, "status": "MODULE_IMPORT_ERROR", "error": str}` if import fails.
- Produces: `_chain_gate()` reporting `valid: False` and appending a blocker if any ledger module failed to import.

- [ ] **Step 1: Write failing test in `tests/test_readiness_report.py`**

```python
def test_readiness_fails_closed_when_strategy_ledger_module_import_fails(tmp_path, monkeypatch):
    import operations.readiness_report as rr
    # Simulate failed import fallback
    monkeypatch.setattr(rr, "_verify_chain_cpcv", lambda: {
        "valid": False, "status": "MODULE_IMPORT_ERROR", "error": "simulated missing module"
    })
    path = str(tmp_path / "readiness_missing_module.db")
    rr.TradingStore(path)
    result = rr.build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    gate = result["gates"]["trial_ledger_chain"]
    assert gate["valid"] is False
    assert gate["status"] == "COMPROMISED"
    assert any("trial-ledger hash chain compromised or unreadable" in b for b in result["blockers"])
```

- [ ] **Step 2: Run test to verify current behavior**

Run: `rtk pytest tests/test_readiness_report.py -k test_readiness_fails_closed_when_strategy_ledger_module_import_fails -v`

- [ ] **Step 3: Update `operations/readiness_report.py`**

Replace:
```python
try:
    from strategy_trial_ledger import verify_chain_readonly as _verify_chain_cpcv
except ImportError:
    try:
        from core.strategy_trial_ledger import verify_chain_readonly as _verify_chain_cpcv
    except ImportError:
        def _verify_chain_cpcv():
            return {"valid": True, "status": "ABSENT"}
```
With:
```python
try:
    from strategy_trial_ledger import verify_chain_readonly as _verify_chain_cpcv
except ImportError:
    try:
        from core.strategy_trial_ledger import verify_chain_readonly as _verify_chain_cpcv
    except ImportError as _cpcv_err:
        def _verify_chain_cpcv():
            return {"valid": False, "status": "MODULE_IMPORT_ERROR", "error": str(_cpcv_err)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_readiness_report.py -v`
Expected: 8/8 PASS

- [ ] **Step 5: Commit**

```bash
rtk git add operations/readiness_report.py tests/test_readiness_report.py
rtk git commit -m "fix(readiness): fail-closed trial ledger chain gate when module import fails"
```

---

### Task 2: Volume Normalization Truncation Logging in `core/trade_manager.py`

**Files:**
- Modify: `core/trade_manager.py:669-715`
- Test: `tests/test_trade_manager_volume_normalization.py`

**Interfaces:**
- Consumes: `core/trade_manager.py:TradeManager._normalize_volume`
- Produces: Logged warning via `logger.warning(...)` when requested volume is positive but normalized output is `0.0`.

- [ ] **Step 1: Write failing test in `tests/test_trade_manager_volume_normalization.py`**

```python
import logging
from unittest.mock import MagicMock
from core.trade_manager import TradeManager

def test_normalize_volume_logs_warning_on_micro_lot_zero_drop(caplog):
    tm = TradeManager(store=MagicMock())
    tm.paper_broker = MagicMock()
    # Requested volume 0.001 is below minimum 0.01
    with caplog.at_level(logging.WARNING):
        vol = tm._normalize_volume("EURUSD", 0.001)
    assert vol == 0.0
    assert any("below minimum" in record.message or "rounds to 0.0" in record.message for record in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_trade_manager_volume_normalization.py -v`
Expected: FAIL because no warning is logged.

- [ ] **Step 3: Update `core/trade_manager.py`**

In `_normalize_volume`:
```python
        if requested_value > 0 and (normalized < minimum - 1e-12 or normalized <= 0):
            logging.getLogger("TradeManager").warning(
                "Volume normalization for %s: requested %.4f is below minimum %.4f (step %.4f, max %.4f) -> normalized to 0.0",
                symbol, requested_value, minimum, step, maximum
            )
            return 0.0
        return normalized if normalized >= minimum - 1e-12 and normalized > 0 else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_trade_manager_volume_normalization.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
rtk git add core/trade_manager.py tests/test_trade_manager_volume_normalization.py
rtk git commit -m "fix(trade-manager): log warning on micro-lot volume truncation to 0.0"
```

---

### Task 3: Token Authentication Middleware on `ui/gui_api.py`

**Files:**
- Modify: `ui/gui_api.py:1-120`
- Test: `tests/test_gui_api_auth.py`

**Interfaces:**
- Consumes: `GUI_API_TOKEN` / `GUI_API_KEY` environment variable.
- Produces: `verify_api_token(request: Request)` dependency and WebSocket token validation.
- Endpoints protected: `/api/control/*`, `/api/config/*`, `/api/internal/*`, `/ws/harness-events`.
- Behavior:
  - If `GUI_API_TOKEN` is unset or empty: permit requests (backward-compatible local mode).
  - If `GUI_API_TOKEN` is set: require matching token in `Authorization: Bearer <token>`, `X-API-Key: <token>`, or `?token=<token>`.
  - On failure: HTTP 401 Unauthorized / WebSocket close 4001.

- [ ] **Step 1: Write tests in `tests/test_gui_api_auth.py`**

```python
import pytest
from fastapi.testclient import TestClient
from ui.gui_api import app

def test_api_control_allows_access_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("GUI_API_TOKEN", raising=False)
    monkeypatch.delenv("GUI_API_KEY", raising=False)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200

def test_api_control_blocks_unauthorized_when_token_configured(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    resp = client.post("/api/control/start", json={"symbol": "EURUSD"})
    assert resp.status_code == 401

def test_api_control_allows_authorized_token(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    resp = client.post("/api/control/start", json={"symbol": "EURUSD"}, headers={"Authorization": "Bearer secret-test-token-123"})  # secret-scan: allow
    assert resp.status_code in [200, 400, 422]  # Authenticated passed gate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_gui_api_auth.py -v`

- [ ] **Step 3: Implement auth helper & dependencies in `ui/gui_api.py`**

Add `verify_api_auth` dependency checking header / token parameter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/test_gui_api_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
rtk git add ui/gui_api.py tests/test_gui_api_auth.py
rtk git commit -m "feat(security): token-based authentication on control API and WebSockets"
```

---

### Task 4: Push All Fixes to GitHub

**Files:**
- Repository: `https://github.com/GriMReapeR-666/llm-trader.git`
- Branch: `main`

- [ ] **Step 1: Verify git status is clean**

Run: `rtk git status`

- [ ] **Step 2: Push commits to origin main**

Run: `rtk git push origin main`

- [ ] **Step 3: Confirm remote tracking status**

Run: `rtk git log -n 5 --oneline`
