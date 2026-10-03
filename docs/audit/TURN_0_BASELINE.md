# TURN 0 — Bootstrap & Baseline Audit
**Project:** Autonomous LLM Trader System
**Branch:** `backup/plugin-dedup-24` @ `9118580`
**Date:** 2026-09-09
**Auditor scope:** Does the project run at all? (compile, test suite, readiness gates, builds, repo hygiene)

---

## 1. Verdict Summary

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1 | Python entrypoint compile (`mt5_trader.py`, 211KB) | ✅ PASS | `py_compile` clean |
| 2 | pytest collection (607 test files) | ✅ PASS | No collection errors |
| 3 | Full test suite run | ⚠️ **99.7% (2,985/2,993 passed, 8 failed)** | Failures cluster on position sizing + graph engine — see §2a |
| 4 | `journal readiness-status` CLI | ✅ PASS (responds) | Status = `BLOCKED` (fail-closed, see §3) |
| 5 | `rtk` token optimizer | ✅ PRESENT | `C:\Users\User\.cargo\bin\rtk.exe` |
| 6 | pytest / Python environment | ✅ PASS | pytest 8.4.1 |
| 7 | Build pipeline (`scripts/build.py`) | ✅ PASS | All build, knowledge-graph & simplicity gates passed |
| 8 | TUI build (`tui_opentui && bun run build:rtk`) | ✅ PASS | TypeScript zero errors; graphify graph rebuilt (20,356 nodes) |
| 9 | Repo hygiene | ⚠️ WARN | See §5 |

## 2. Test Suite Baseline
- Test files discovered: **607** | Tests collected: **2,993** (README claims "154 test suites / 730+ tests" — significant count mismatch, investigate in Turn 11)
- Collection: clean, no import-time errors
- Full run (`rtk python -m pytest tests -q`): **8 FAILED / ~2,985 PASSED → ≈ 99.7% pass rate**
- All 8 failures reproduce deterministically on re-run (`--lf`)

### 2a. Failure Cluster Analysis (feeds Turns 1 & 4/5)

**Cluster A — Position sizing returns 0.0 (⚠️ CRITICAL for equity compounding):**
| Test | Failure |
|---|---|
| `test_llm_trader.py::TestRiskManager::test_position_sizing` | `AssertionError: 0.0 != 0.1` — RiskManager sizes **zero** lots |
| `test_risk_management_hardening.py::…test_calc_position_size_with_cvar_and_meta_health` | `assert 0.0 > 0.0` — CVaR/meta-health path sizes zero |
| `test_phase1_remediation.py::…test_paper_mode_position_trailing_stop_and_exit` | `KeyError: 100000` — account balance key lookup broken |

If live sizing hits the same path as these tests, **no trades can open (or worse, sizing math is wrong)** — this is the #1 candidate blocking the compounding loop. Escalate to **Turn 1**.

**Cluster B — Deliberative graph rejects valid buy signals:**
| Test | Failure |
|---|---|
| `test_deliberative_graph.py::…with_buy_signal` | `assert 'REJECTED' == 'EXECUTED'` |
| `test_graph_engine_facade.py::…deliberative_and_reflection` | `assert 'REJECTED' == 'EXECUTED'` |
| `test_graph_paper_simulation.py::…paper_trading_simulation` | `assert 'REJECTED' == 'EXECUTED'` |
| `test_graph_engine_facade.py::…human_in_the_loop` | `ValueError: Thread 'hitl_thread_001' is not in an interruptible state` (LangGraph HITL interrupt bug) |

Possible shared root cause with Cluster A (validator rejects when size = 0). Escalate to **Turn 5**.

**Cluster C — Plugin stub regression (from the plugin-dedup-24 branch):**
| Test | Failure |
|---|---|
| `test_extracted_module_plugins.py::TestExtractedAlphaSourcePlugin::test_gamma_regime_stub` | `assert 'NEUTRAL' == 'unknown'` — regime stub contract changed during dedup |

## 3. Readiness / Fail-Closed Gates (`python mt5_trader.py journal readiness-status`)

**Overall status: `BLOCKED` — `execution_authority: false`** ✅ (correct fail-closed behavior; system refuses live trading without current approvals)

Blockers reported (all are safety gates working as designed, not defects):
1. Tiny-live rollout approval is absent, expired, or evidence-invalid
   - Prior approval existed (`APPROVED` 2026-08-27) but **expired 2026-08-28** — approvals are 24h TTL
2. Credential/trace security review incomplete
   - MT5 env credentials `PRESENT` in environment; `values_redacted: true`; credential rotation not completed
3. Learned/adaptive research-influence not approved (evidence sample 150/100 ready, but approval expired)

Positive signals: research influence gate returns `BLOCKED` with explicit reasons; readiness report explicitly states *"this report never grants order permission"*; operator approval chain with evidence hash + sample-size minimums is implemented.

## 4. Build Pipeline
- `python scripts/build.py`: ✅ **PASS** — "ALL BUILD, KNOWLEDGE GRAPH & SIMPLICITY GATES PASSED" (includes graphify AST update + ponytail simplicity gates; one React `act(...)` test warning in stderr — cosmetic, logged for Turn 10)
- `tui_opentui && bun run build:rtk`: ✅ **PASS** — `rtk tsc --noEmit`: "TypeScript: No errors found"; graphify graph rebuilt: **20,356 nodes / 36,618 edges / 1,132 communities** (`graphify-out/graph.json`, `graph.html`, `GRAPH_REPORT.md`)

## 5. Repo Hygiene (⚠️ WARN)

Counts of loose files at repo root (not in `data/`, `artifacts/`, or `tests/`):
| Category | Count | Concern |
|---|---|---|
| `test_*.json` artifacts | 25 | Stale test-run outputs polluting root (e.g. `test_decay_*.json`, `test_edge_*.json`) |
| `test_*.py` scratch scripts | 12 | Untracked ad-hoc test scripts mixed with real suite |
| `*.db` / `*.db-*` files | 12 | Live state DBs (`trading_state.db` 5.4MB, WAL/SHM, `agent_events.db`…) at root — no data-dir segregation |
| `*state*.json` files | 21 | Runtime state files at root (`supervisor_state.json`, `evolutionary_state.json`…) |
| Junk files | 3 | `1.3` (0-byte), `lessons.jsonl` (0-byte), `_risk_state_polluted_backup.json` (suspicious name) |
| Git uncommitted changes | **160 files** | Working tree dirty on a backup branch — snapshot before audit fixes |

**Risk:** `risk_state.json` (live risk limits) sits next to test-pollution files. A stray test writing to the wrong path could corrupt live risk state. → Escalate to Turn 6 (State Persistence).

## 6. Turn 0 Verdict

**✅ THE PROJECT RUNS.** It compiles, 99.7% of its 2,993-test suite passes, both build pipelines are green, and every fail-closed gate is correctly blocking unauthorized live trading. Quality of the baseline is strong.

**Headline risks found:**
1. 🔴 **Position sizing returns 0.0** on 3 test paths (RiskManager + CVaR path) — direct threat to the compounding loop → **Turn 1 resolves**
2. 🟠 Deliberative graph `REJECTED != EXECUTED` on buy signals + HITL interrupt bug → Turn 5
3. 🟠 160 uncommitted files on a backup branch — snapshot needed before any fix
4. 🟡 Root-directory state pollution (25 test JSONs / 12 DBs / 21 state files / junk) → Turn 6
5. 🟡 README test-count claim (730+) vs reality (2,993 tests) → Turn 11

## 7. Actions Before Turn 1
- [x] Fill §2 (suite result), §4 (build results)
- [ ] Commit or stash the 160 dirty files for a clean audit diff baseline (operator decision — done before first fix turn, not needed for read-only Turn 1)

