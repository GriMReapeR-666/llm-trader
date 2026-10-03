# Journal Hash-Chain Repair, Ablation Launcher, and Runtime Verifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair corrupt checkpoint hash-chain links in the live SQLite journal (`artifacts/graph_state.db`), wire the daily alternating ablation schedule into an autonomous launcher that pairs session reports, verify the 100K prompt ceiling in a bounded live paper session, and exercise the trader stop flag end-to-end to verify graceful `CrashRecovery` cleanup.

**Architecture:**
- Checkpoint journal hash repair adds an atomic `repair_chain_integrity(backup=True)` routine to `GraphStatePersistence` that preserves immutable payload data, re-hashes sequential checkpoint links from the prune anchor, creates timestamped backups, and validates zero broken chains.
- The ablation launcher wraps `artifacts/pu_ablation_schedule.py` and executes the assigned UTC arm (`no_llm` vs `with_llm`) with process isolation, logs the run metadata, and automatically collates the paired comparison markdown report.
- The prompt ceiling verification runs a live bounded paper session (`--paper --loops 2`) verifying that serialized prompt payloads to the LLM remain strictly $\le 100,000$ characters and result in zero `PARSE_ERROR` responses.
- The stop flag verification spawns an active trader instance, creates the configured stop flag file while active, and verifies that the trader exits with code 0, retiring the flag and executing complete `CrashRecovery` shutdown hooks.

**Tech Stack:** Python 3.13, SQLite3, pytest, PowerShell, psutil, JSON, MT5 Python API.

**Spec:** Direct user requirements:
1. Repair the broken hash-chain rows in the live journal now instead of waiting for retention to rotate past them, with a backup and full verification.
2. Wire the alternating ablation schedule into a launcher so it actually runs one arm per day and pairs the session reports automatically.
3. Run a bounded live paper session to confirm the prompt ceiling keeps every request under 100K serialized and eliminates the PARSE_ERROR class.
4. Exercise the stop flag end-to-end against a running trader to prove it reaches full CrashRecovery cleanup instead of a hard terminate.

## Global Constraints
- Shell commands MUST use the `rtk` (or `etk`) token-killer CLI prefix on PowerShell (Windows).
- Live trading remains strictly forbidden; use `--paper` mode for simulation and execution tests.
- All file references in responses must be markdown clickable links with the `file:///` scheme.
- Zero irreversible data loss: always backup database files prior to mutation.

---

### Task 1: Checkpoint Hash-Chain Repair in `core/graph_state_persistence.py`

**Files:**
- Modify: `core/graph_state_persistence.py:540-580`
- Test: `tests/test_graph_state_persistence.py`
- Target Database: `artifacts/graph_state.db`

**Interfaces:**
- Produces: `GraphStatePersistence.repair_chain_integrity(backup: bool = True) -> Dict[str, Any]` returning `{"repaired": int, "backup_path": str, "valid_after": bool}`.

- [ ] **Step 1: Write unit test in `tests/test_graph_state_persistence.py` for chain repair**

```python
def test_repair_chain_integrity_fixes_broken_chain(tmp_path):
    db_file = str(tmp_path / "broken_chain.db")
    p = GraphStatePersistence(db_file)
    for c in range(1, 6):
        p.save_checkpoint(c, {"val": c}, status=CheckpointStatus.COMPLETED)
    with p._connect() as con:
        con.execute("UPDATE checkpoints SET hash_chain='corrupt_hash' WHERE id=3")
    assert p.verify_chain_integrity()["valid"] is False
    assert p.verify_chain_integrity()["broken_at"] == 3

    report = p.repair_chain_integrity(backup=True)
    assert report["valid_after"] is True
    assert report["repaired"] >= 1
    assert os.path.exists(report["backup_path"])

    check = p.verify_chain_integrity()
    assert check["valid"] is True
    assert check["broken_at"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk python -m pytest tests/test_graph_state_persistence.py -k test_repair_chain_integrity_fixes_broken_chain`
Expected: FAIL with `AttributeError: 'GraphStatePersistence' object has no attribute 'repair_chain_integrity'`

- [ ] **Step 3: Implement `repair_chain_integrity` in `core/graph_state_persistence.py`**

Add method to `GraphStatePersistence`:
```python
    def repair_chain_integrity(self, backup: bool = True) -> Dict[str, Any]:
        """Recompute hash-chain links from the trust root to repair broken chains.

        Creates a timestamped backup before mutation when backup=True.
        """
        import shutil
        backup_path = ""
        if backup and os.path.exists(self.db_path):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = f"{self.db_path}.bak_{stamp}"
            shutil.copy2(self.db_path, backup_path)

        with self._connect() as con:
            rows = con.execute(
                "SELECT id, cycle_num, status, payload_json, hash_chain "
                "FROM checkpoints ORDER BY id ASC"
            ).fetchall()
            anchor_row = con.execute(
                "SELECT value FROM metadata WHERE key='chain_anchor_hash'"
            ).fetchone()
            anchor_id_row = con.execute(
                "SELECT value FROM metadata WHERE key='chain_anchor_id'"
            ).fetchone()

            if not rows:
                return {"repaired": 0, "backup_path": backup_path, "valid_after": True}

            first_id = rows[0]["id"]
            if first_id == 1:
                prev_hash = "GENESIS"
            elif anchor_row is not None and anchor_id_row is not None:
                prev_hash = anchor_row["value"]
            else:
                prev_hash = rows[0]["hash_chain"]

            repaired_count = 0
            for i, row in enumerate(rows):
                if i == 0 and prev_hash == row["hash_chain"]:
                    continue
                expected = self._compute_hash(prev_hash, row["payload_json"])
                if row["hash_chain"] != expected:
                    con.execute(
                        "UPDATE checkpoints SET hash_chain=? WHERE id=?",
                        (expected, row["id"]),
                    )
                    repaired_count += 1
                prev_hash = expected

            con.commit()

        status = self.verify_chain_integrity()
        return {
            "repaired": repaired_count,
            "backup_path": backup_path,
            "valid_after": bool(status.get("valid", False)),
            "details": status,
        }
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `rtk python -m pytest tests/test_graph_state_persistence.py -k test_repair_chain_integrity_fixes_broken_chain`
Expected: PASS (1 passed)

- [ ] **Step 5: Execute repair on the live `artifacts/graph_state.db` and verify integrity**

Run: `rtk python -c "from core.graph_state_persistence import GraphStatePersistence; p = GraphStatePersistence('artifacts/graph_state.db'); print(p.repair_chain_integrity(backup=True)); print(p.verify_chain_integrity())"`
Expected: `valid_after: True`, `broken_at: None`, backup file created.

---

### Task 2: Alternating Ablation Launcher & Automated Report Pairing

**Files:**
- Create: `artifacts/pu_ablation_launcher.py`
- Test: `tests/test_ablation_launcher.py`
- Outputs: `artifacts/pu_ablation_pair_report.md`

**Interfaces:**
- Consumes: `artifacts/pu_ablation_schedule.py::resolve`
- Produces: `artifacts/pu_ablation_launcher.py` executable CLI (`--dry-run`, `--once`, `--pair-only`) and automated markdown summary.

- [ ] **Step 1: Write test in `tests/test_ablation_launcher.py` for launcher and pairing logic**

```python
def test_ablation_launcher_schedule_resolution():
    from artifacts.pu_ablation_launcher import get_today_launcher_config
    cfg = get_today_launcher_config(today=date(2026, 10, 2))
    assert cfg["arm"] == "no_llm"
    assert "PU_ABLATION" in cfg["env"]

    cfg_base = get_today_launcher_config(today=date(2026, 10, 3))
    assert cfg_base["arm"] == "with_llm"
    assert "PU_ABLATION" not in cfg_base["env"]

def test_pair_report_generation(tmp_path):
    from artifacts.pu_ablation_launcher import generate_paired_report
    report_file = tmp_path / "pair_report.md"
    generate_paired_report(
        ablation_meta={"arm": "no_llm", "decisions": {"HOLD": 19, "BUY": 0}, "requests": 0, "session": 10},
        baseline_meta={"arm": "with_llm", "decisions": {"HOLD": 3, "BUY": 14, "SELL": 1}, "requests": 15, "session": 11},
        output_path=str(report_file),
    )
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Paired Ablation Analysis" in content
    assert "19 HOLD" in content
    assert "14 BUY" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk python -m pytest tests/test_ablation_launcher.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'artifacts.pu_ablation_launcher'`

- [ ] **Step 3: Implement `artifacts/pu_ablation_launcher.py`**

Include:
1. `get_today_launcher_config(today=None)`: calls `resolve()` from `pu_ablation_schedule.py`.
2. `generate_paired_report(ablation_meta, baseline_meta, output_path)`: formats markdown table comparing ablation vs baseline with divergence metrics.
3. `run_daily_arm(loops=24, dry_run=False)`: launches `mt5_trader.py llm-trade --paper ...` with appropriate env flags, captures report, pairs with opposite arm's latest report, and updates `pu_ablation_pair_report.md`.
4. CLI parser supporting `--once`, `--dry-run`, `--pair-only`.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk python -m pytest tests/test_ablation_launcher.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify CLI launcher in dry-run mode and pair-only mode**

Run: `rtk python artifacts/pu_ablation_launcher.py --pair-only`
Expected: Successfully generates updated `artifacts/pu_ablation_pair_report.md` without errors.

---

### Task 3: Bounded Live Paper Session for Prompt Ceiling & Parse Error Verification

**Files:**
- Target: `mt5_trader.py` with `intelligence/llm_orchestrator.py`
- Database: `trading_state.db` (inspect table `llm_requests`)

**Verification Protocol:**
- [ ] **Step 1: Check existing `llm_requests` table schema and historical prompt lengths**

Run: `rtk python -c "import sqlite3; con=sqlite3.connect('trading_state.db'); print(con.execute('PRAGMA table_info(llm_requests)').fetchall()); print(con.execute('SELECT id, model, error_class, length(prompt), length(raw_response) FROM llm_requests ORDER BY id DESC LIMIT 5').fetchall())"`

- [ ] **Step 2: Launch a 2-loop live paper session with LLM enabled**

Command:
`rtk python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe M15 --model meta/llama-3.2-11b-vision-instruct --loops 2 --show-metrics`
Wait for execution completion.

- [ ] **Step 3: Query newly generated `llm_requests` rows to prove serialized size $\le 100,000$ and 0 `PARSE_ERROR`**

Run: `rtk python -c "import sqlite3; con=sqlite3.connect('trading_state.db'); rows = con.execute('SELECT id, timestamp, status, error_class, length(prompt), length(raw_response) FROM llm_requests ORDER BY id DESC LIMIT 10').fetchall(); [print(r) for r in rows]; assert all(r[4] <= 100000 for r in rows), 'Prompt exceeded 100K!'; assert all(r[3] != 'PARSE_ERROR' for r in rows), 'PARSE_ERROR detected!'"`
Expected: Every request $\le 100,000$ characters and zero `PARSE_ERROR`.

---

### Task 4: End-to-End Stop Flag Verification Against Running Trader

**Files:**
- Target: `mt5_trader.py` (`_stop_file_path`, `_stop_file_requested`, `_acknowledge_stop_file`)
- Stop Flag Location: `artifacts/trader.stop`
- Process: Background `mt5_trader.py llm-trade --paper ... --loops 10`

**Verification Protocol:**
- [ ] **Step 1: Ensure clean state (remove any leftover `artifacts/trader.stop`)**

Run: `rtk python -c "import os; [os.remove(f) for f in ['artifacts/trader.stop'] if os.path.exists(f)]"`

- [ ] **Step 2: Launch trader in background with `--loops 10`**

Launch `python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO --loops 10` with task tracking.

- [ ] **Step 3: Create the stop flag file with reason while trader is running**

Run: `rtk python -c "with open('artifacts/trader.stop', 'w') as f: f.write('Operator requested graceful wind-down test\n'); print('Stop flag written')"`

- [ ] **Step 4: Monitor trader output until termination**

Verify logs show:
1. `Stop flag requested — winding down gracefully after ...`
2. Retirement of the stop flag (`os.path.exists('artifacts/trader.stop') == False`).
3. Execution of full `CrashRecovery` shutdown:
   - `Crash recovery: graceful shutdown initiated`
   - `Post-session scheduler report archived: scheduler_tuning_report_*.json`
   - `Crash recovery: shutdown complete`
   - `CrashRecovery shutdown snapshot saved (graceful exit)`
   - `Ψ Guardian thread stopped`
4. Exit code 0 (clean exit, not crash, not SIGKILL).

---

### Task 5: System Knowledge Graph Sync and Verification Sign-off

- [ ] **Step 1: Run full test suite across touched test files**

Run: `rtk python -m pytest tests/test_graph_state_persistence.py tests/test_ablation_launcher.py tests/test_trader_stop_flag.py tests/test_request_budget.py -v`
Expected: 100% PASS.

- [ ] **Step 2: Update graphify knowledge graph**

Run: `rtk graphify update .`

- [ ] **Step 3: Present final verification report**
