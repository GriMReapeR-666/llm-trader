# Gate 0 Phase 3: Real Emergency Controls & Disarm Decision Bypasses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disarm all remaining decision-bypass paths (RL mutating HOLD to BUY/SELL [01a F4], provider outage fabricating active trades [01a F6]), and verify the real emergency control stack (flatten, killswitch, journal CAS queue [C4, K5, D1]).

**Architecture:**
1. In `research/rl_action_bridge.py`, enforce strict immutability of the decision action: if the LLM or safety stack produced HOLD, RL ε-exploration cannot override it to BUY or SELL. RL only supplies advisory telemetry and missing continuous execution bounds for already-approved BUY/SELL actions.
2. In `intelligence/llm_orchestrator.py`, eliminate the outage fallback block that promoted institutional workflows to active orders at confidence 0.85 when the LLM is down. Provider outage must remain an unconditional, fail-closed safety HOLD.
3. In `operations/autonomy.py` and `ui/gui_api.py`, verify and test the compare-and-set control queue, real killswitch (store + latch + autonomy halt + intent cancellation), and real protective flatten across `TradeManager` and `RiskManager`, ensuring the control plane honestly reports queued vs applied status (D1).

**Tech Stack:** Python 3.11, pytest, SQLite, threading.

**Spec:** `docs/AUDIT_2026-10-04.md` (C4) and `docs/AUDIT_2026-10-04_DEEP.md` (01a F4, 01a F6, C4, K5, D1).

## Global Constraints
- Shell commands must be prefixed with `etk` (or `rtk`).
- Fail closed on any missing/invalid state or provider outage.
- No new external pip packages.
- Strict TDD: red -> green -> refactor -> commit.

---

### Task 1: Stop RL Exploration from Mutating HOLD to BUY/SELL (Finding 01a F4)

**Files:**
- Modify: `research/rl_action_bridge.py:210-242`
- Create: `tests/test_rl_action_bridge_safety.py`

**Interfaces:**
- Consumes: `BridgedExecutionParams`, `merge_bridge_into_decision`
- Produces: Guarantee that `merge_bridge_into_decision` never escalates a HOLD decision into BUY/SELL.

- [ ] **Step 1: Write failing test verifying HOLD cannot be overridden by RL bridge**
Create `tests/test_rl_action_bridge_safety.py`:
```python
import pytest
from rl_action_bridge import BridgedExecutionParams, merge_bridge_into_decision
from trading_safety import SCALP_MAGIC

def test_rl_bridge_never_mutates_hold_to_trade():
    """An LLM HOLD decision must NEVER be escalated to BUY or SELL by RL exploration."""
    bridged = BridgedExecutionParams(
        action="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy="scalp",
        magic=SCALP_MAGIC,
    )
    decision = {"action": "HOLD", "reasoning": "LLM decided no trade", "confidence": 0.0}
    merged = merge_bridge_into_decision(decision, bridged, fill_missing_only=True)
    assert merged["action"] == "HOLD", "RL bridge must not mutate HOLD to BUY"
    assert merged["execution_authority"] is False

def test_rl_bridge_fills_params_for_existing_buy():
    """RL bridge may fill missing entry/SL/TP when the decision is ALREADY BUY."""
    bridged = BridgedExecutionParams(
        action="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy="scalp",
        magic=SCALP_MAGIC,
    )
    decision = {"action": "BUY", "entry": None, "stop_loss": None, "take_profit": None, "confidence": 0.75}
    merged = merge_bridge_into_decision(decision, bridged, fill_missing_only=True)
    assert merged["action"] == "BUY"
    assert merged["entry"] == 1.1000
    assert merged["stop_loss"] == 1.0950
    assert merged["take_profit"] == 1.1100
    assert merged["execution_authority"] is False
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_rl_action_bridge_safety.py -v`
Expected: FAIL (because current implementation replaces `"HOLD"` with `bridged.action`).

- [ ] **Step 3: Update `merge_bridge_into_decision` in `research/rl_action_bridge.py`**
In `research/rl_action_bridge.py`:
Ensure that if `current_action in {"", "HOLD", "NONE", "NULL"}`, the action remains `"HOLD"` and no trade fields are overlaid.

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_rl_action_bridge_safety.py -v`
Expected: PASS

- [ ] **Step 5: Run existing RL tests**
Run: `etk pytest tests/test_phase4_learning_loop.py -v`
Expected: PASS

- [ ] **Step 6: Commit**
```bash
git add research/rl_action_bridge.py tests/test_rl_action_bridge_safety.py
git commit -m "fix(rl): stop RL action bridge from mutating HOLD into BUY/SELL (01a F4)"
```

---

### Task 2: Eliminate Outage Workflow Auto-Promotion (Finding 01a F6)

**Files:**
- Modify: `intelligence/llm_orchestrator.py:8628-8645`
- Create: `tests/test_provider_outage_safety.py`

**Interfaces:**
- Consumes: `LLMOrchestrator.execute_cycle`
- Produces: Strict safety HOLD during provider outages; no synthetic trade promotion.

- [ ] **Step 1: Write test verifying provider outage always produces a safety HOLD**
Create `tests/test_provider_outage_safety.py`:
Test that when `get_decision` returns `deterministic_fallback=True` / HOLD, having an active `institutional_workflow` in `toon` does NOT promote it to an active trade.

- [ ] **Step 2: Run test to verify behavior**
Run: `etk pytest tests/test_provider_outage_safety.py -v`

- [ ] **Step 3: Remove the institutional workflow promotion block during outages**
In `intelligence/llm_orchestrator.py`:
Remove lines 8628-8645 so provider outage remains an unconditional safety HOLD.

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_provider_outage_safety.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add intelligence/llm_orchestrator.py tests/test_provider_outage_safety.py
git commit -m "fix(safety): eliminate institutional workflow auto-promotion during LLM outages (01a F6)"
```

---

### Task 3: Verify and Test Real Flatten, Killswitch & Control Plane Journal (Findings C4, K5, D1)

**Files:**
- Modify/Verify: `operations/autonomy.py`, `ui/gui_api.py`, `intelligence/llm_orchestrator.py`
- Create: `tests/test_emergency_controls_execution.py`

**Interfaces:**
- Consumes: `apply_operator_control`, `AutonomyManager`, `TradingStore`, `RiskManager`, `TradeManager`
- Produces: Verified real execution of emergency controls with honest status reporting.

- [ ] **Step 1: Write comprehensive tests for `apply_operator_control`**
Create `tests/test_emergency_controls_execution.py`:
- Test 1: `killswitch` action:
  - sets `TradingStore.set_kill_switch(True)`
  - halts `AutonomyManager.halt(...)`
  - engages `KillSwitchLatch.apply_market_data(halt_flag=True)`
  - cancels pending order intents via `operations`
  - returns `{"status": "applied", ...}`
- Test 2: `flatten` action:
  - triggers `RiskManager.trigger_emergency_flatten(...)`
  - calls `TradeManager.enforce_protective_flatten()`
  - returns `{"status": "applied", ...}` (or `"queued"` if no TradeManager attached yet)
- Test 3: Fail-closed on missing/failing dependencies:
  - returns `{"status": "failed", "errors": [...]}` when required store or risk components error out.
- Test 4: GUI API `/api/control/{action}` responds with status `"queued"` or `"applied"`, and only emits `CONTROL_ISSUED` (never false `CONTROL_EXECUTED`).

- [ ] **Step 2: Run tests to verify they pass**
Run: `etk pytest tests/test_emergency_controls_execution.py -v`
Expected: PASS

- [ ] **Step 3: Run control plane journal tests**
Run: `etk pytest tests/test_control_plane_journal.py -v`
Expected: PASS

- [ ] **Step 4: Commit**
```bash
git add operations/autonomy.py ui/gui_api.py intelligence/llm_orchestrator.py tests/test_control_plane_journal.py tests/test_emergency_controls_execution.py
git commit -m "fix(control): verify real flatten/killswitch execution, CAS queue, and honest status (C4, K5, D1)"
```
