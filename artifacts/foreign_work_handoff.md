# FOREIGN-WORK HANDOFF — uncommitted changes on `feat/ui-pipeline-compact-tabs`

**Written:** 2026-09-30 (UTC) · **At commit:** `7455773` (scorecard wiring) · **By:** Buffy (Codebuff session arc)
**Audience:** the second workstream's author (or any future operator landing this work).

This repo carries **two workstreams**. The Codebuff session arc is fully committed
(14 commits, `85d54e7` → `7455773`: payload R1–R5, router quota-pause, memory
outage fix, paper fast-boot, boot fetch watchdog, fetcher degradation,
quota-aware backoff, `run_bounded.py`, scheduled-agent bounding, boot datasheet
+ provider scorecard + wiring, tuning-report telemetry). Everything **uncommitted**
in the worktree belongs to the *other* author and is inventoried here so it can
land cleanly later.

## 1. Size and shape (measured at `7455773`)

| Bucket | Count | Notes |
|---|---|---|
| Modified tracked files | 188 | +394,285 / −285,882 raw, but ~375K of that is the generated `graphify-out/graph.json` |
| Excluding `graphify-out` | 202 files, +14,968 / −20,691 | the real hand-authored churn is ~15K lines |
| Deleted tracked files | 22 | see §3 — all recoverable from git history |
| Untracked files | 6,116 | see §4 — dominated by generated/run-artifact dirs |
| Stashes | 3 | `cline restore transaction 5c9dd80e` (this branch), `cline restore transaction 24d7e923` + `WIP on backup/plugin-dedup-24` (other branch) — **not inventoried here, do not drop without their owner** |

### Churn by area (tracked modifications, excl. deletions)

| Area | +/− | What it is |
|---|---|---|
| `tests/` | +2,642 / −92 | new + extended suites (alert_outbox, scheduler_pulse, risk_management_hardening…) |
| `intelligence/` | +2,171 / −1,410 | `graph_nodes.py` (+1,287/−1,263), orchestrator (see §5), scheduler cadence |
| `tui_opentui/src/` | +1,722 / −1,403 | `GraphScreen.tsx` (+991/−384), `StrategyObservatoryScreen.tsx` (−heavy) |
| `strategy_registry.json` | +1,321 / −45 | registry data |
| `research/` | +1,151 / −197 | `continuous_backtest_pipeline.py`, `full_pipeline_backtest.py`, `alpha_miner/` |
| `core/` | +982 / −244 | `harness/pipeline_v2.py` (+409/−16), trading_tools (§5) |
| `data/` | +892 / −268 | news_indicator_fetcher (§5), yahoo_finance_provider (+259/−167) |
| `operations/` | +814 / −254 | `alert_outbox.py` (+451/−18) + its new test (+399) |
| `ui/` | +733 / −209 | `gui_api.py` (+623/−171) |
| `mt5_trader.py` | +442 / −90 | launcher rewrite (§5) |
| `OPERATOR_RUNBOOK.md` | +234 | foreign sections only (§5) |

## 2. Foreign feature inventory (from the committed runbook's foreign sections)

The other author's work, as documented in the runbook sections they wrote
(§2.2 Laya guards, §2.3 event_monitor cadence, §2.4 executor/watchers, §3 glossary,
§6 strategy health) and visible in the diff:

1. **Live lockout** — `REQUIRE_LIVE_CONFIRMATION` / `LIVE_CONFIRMATION_TOKEN`,
   fail-closed master switch enforced at the start of `_build_execution_plan`'s
   live branch (orchestrator + mt5_trader paths).
2. **Per-strategy context buffers** — `_cycle_state_lock` (new lock; known
   breakage note in §6 below).
3. **Router hardening** — `_extract_message` and related response-shape defenses.
4. **Laya CPU triage guards** — `data/news_triage.py`:
   `NEWS_TRIAGE_MIN_FREE_RAM_GB` (1.8), `NEWS_TRIAGE_LAYA_MAX_ARTICLES` (12),
   `NEWS_TRIAGE_LAYA_TIME_BUDGET_S` (60), runtime circuit
   (2 fail-batches ⇒ 900s cooldown), `laya_batch_cap` / `laya_time_budget` /
   `laya_circuit_open` deferral reasons. (Note: the *paper default*
   `NEWS_TRIAGE_MODE=off` is Codebuff's committed `34ba7bd`; the two coexist.)
5. **Scheduler cadence rework** — `EVENT_MONITOR_FULL_PIPELINE_MIN_S` (300),
   `EVENT_MONITOR_SPLIT_WINDOWS` alternating halves, window/counter contract
   (`_event_monitor_last_full_ts`, `_event_monitor_window_no`), plus heartbeat
   session executor and `run_session_followups.py` follow-ups.
6. **Launcher rewrite** — `mt5_trader.py` (+442/−90), `START_DECK.bat`,
   `START_TRADING.bat`: `--`-passthrough arg convention, QKF smoke preflight,
   ctx_probe preflight blocks (`call python` errorlevel trap documented),
   `HEADLESS`/`CI` exit-pause skip.
7. **FRED series-map extension** — `core/news_indicator_fetcher.py`:
   ADPFE→PAYEMS, NAPM→PCUOMFGOMFG, RSAFS/UMCSENT/MANEMP added to the series maps.
8. **Evidence-validation gates + Graphify/TUI layer** — `graph_nodes.py`
   rework, `tui_opentui` screens, `ui/gui_api.py`, `graphify-out/*` regeneration,
   `strategy_registry.json` updates, `compliance_dashboard.html` /
   `alpha_dashboard.html`.
9. **Alert outbox** — `operations/alert_outbox.py` (+451) and
   `tests/test_alert_outbox.py` (+399).
10. **Research pipeline extensions** — `continuous_backtest_pipeline.py`,
    `full_pipeline_backtest.py`, `alpha_miner/`, `research/decision_models/` (untracked).

## 3. The 22 deleted tracked files — safe to commit as deletions

All are recoverable from any ancestor of HEAD (e.g. `git show HEAD:<path>`), so
committing the deletions loses nothing. Pattern: root-level scratch test files
and stale artifacts.

- Scratch tests (12): `test_cycle.py`, `test_debug.py`, `test_engine2.py` …
  `test_engine8.py`, `test_fallback.py`, `test_fallback2.py`, `test_minimal.py`
- `core/equity_tracker.py`, `operations/launch_trading.py`,
  `operations/session_manager.py`
- Stale artifacts (7): `artifacts/ppo_smoke.json`,
  `sample_calendar_events.csv`, `test_data_source_live_integration_report.json`,
  `test_rl_integration.json`, `learned_case_studies.json`,
  `high_impact_events.csv`, `artifacts/economic dynamics.txt`

## 4. Untracked files (6,116) — triage guidance

Mostly generated/ephemeral; recommend committing only hand-authored work:

| Path | Files | Suggested disposition |
|---|---|---|
| `artifacts/capstone/` | 3,433 | generated run output — gitignore, don't commit |
| `graphify-out/cache/` | 931 | generated — gitignore |
| `artifacts/training/` | 843 | generated — gitignore |
| `artifacts/tmp_verify/` | 212 | ephemeral — delete/gitignore |
| `.freebuff/sponsored-runtime/` | 144 | tool-managed — gitignore |
| `docs/_prd_build/`, `docs/diagrams/` | 39 | **hand-authored docs — candidate to commit** |
| `research/decision_models/` | 10 | **hand-authored code — candidate to commit** |
| `operations/agents/` | 4 | inspect; small |
| `artifacts/pu_*.py/.ps1/.out/.txt` | ~40 | Codebuff session probes/evidence; commit selectively if wanted as evidence (the session-5/6 chassis scripts are the durable ones) |
| root stragglers | few | inspect individually |

Note: `artifacts/pytest_tmp_bounded/` is unreadable (Windows permission denied)
— present in the worktree, invisible to some git operations; resolve before any
tree-wide cleanup.

## 5. Known-clean boundaries already established (mixed-authorship files)

The Codebuff arc landed exactly its own sections in these files; the remainder
is foreign. If landing foreign work, these are the verified split points
(content anchors, not line numbers — files will shift):

- `OPERATOR_RUNBOOK.md` — **committed (foreign-author-authored)**: header, §1,
  §2 intro, §2.2 Laya, §2.3–§2.4, §3–§6. **Still uncommitted (Codebuff's)**:
  §2.2b–§2.2h compact-context addenda (anchors: `### 2.2b compact-context
  preflight` → `### 2.3 event_monitor cadence`).
- `artifacts/payload_flow_audit.md` — **committed (Codebuff's)**: the 2026-09-30
  verification addenda (anchor: `## Verification addendum (2026-09-30)` → EOF).
  **Uncommitted (foreign)**: the audit body, intro → anchor.
- `intelligence/llm_orchestrator.py` — **committed (Codebuff's)**: `_emit_provider_scorecard`
  method + datasheet fold region + all session-arc hunks. **Uncommitted (foreign)**:
  `_cycle_state_lock`, live-lockout enforcement, `_extract_message` hardening,
  scheduler cadence (+491/−64 vs HEAD as of writing).
- `core/trading_tools.py` — uncommitted remainder = foreign `broker_reconciled`
  hunk (3 `+` lines) only; Codebuff's bounding work is fully committed.
- `core/news_indicator_fetcher.py` — Codebuff's bounded `_fetch_fred_series` is
  committed; the FRED series-map hunks are foreign and uncommitted (+44/−22 total).
- `mt5_trader.py` — Codebuff's `apply_paper_triage_default` is committed
  (`34ba7bd`); the launcher rewrite (+442/−90) is foreign.
- `.env`-adjacent and DB files (`agent_events.db*`, `approvals_outbox.jsonl`,
  etc.) — runtime state, never commit.

## 6. Known issues to resolve before landing foreign work

1. **`_cycle_state_lock` breakage** — `tests/test_pair_selection_context.py`
   fails at HEAD-adjacent states with `AttributeError: _cycle_state_lock` when
   the orchestrator hunks are absent; the attribute exists only in the foreign
   uncommitted code. Landing the orchestrator hunks fixes it; landing *around*
   them will not.
2. **Torch/RAM context** — the box has ~1.5GB free-RAM boot bar; foreign work
   touching startup paths (Laya triage model load, launcher) must respect the
   `NEWS_TRIAGE_MODE` paper default and the memory doctrine.
3. **Serial pytest required** — repo pytest.ini bakes `-n` xdist, which hangs on
   this box; run `python -X utf8 -m pytest … -o addopts=""` serially.
4. **`graphify-out/graph.json`** (+374,707/−262,318) — confirm it's meant to be
   tracked at all before committing; it dwarfs every other change and looks
   machine-regenerated.
5. **Stashes are not yours to drop** — three stashes from cline restore
   transactions and another branch remain; coordinate with their owner.

## 7. Suggested landing order (for the foreign author)

1. Decide `graphify-out` tracking (gitignore vs commit) — it distorts every
   review.
2. Commit the 22 deletions as one housekeeping commit.
3. Commit `operations/alert_outbox.py` + its test (self-contained pair).
4. Commit the TUI/UI layer (`tui_opentui/`, `ui/gui_api.py`, dashboards).
5. Commit research pipelines + `research/decision_models/`.
6. Commit scheduler cadence + executor + launcher rewrite together (they
   interlock via `mt5_trader.py`).
7. Land the orchestrator hunks last (`_cycle_state_lock`, live lockout,
   `_extract_message`) — they carry the §6.1 test dependency — plus the Laya
   guards and FRED maps.
8. Commit the foreign doc sections last, using the §5 anchors to avoid
   clobbering the already-landed Codebuff sections.

*Verification hooks for whoever lands this: `python -X utf8 -m pytest
tests/test_pair_selection_context.py tests/test_alert_outbox.py -o addopts=""`
serially; journal probe pattern `sqlite3 "file:trading_state.db?mode=ro"`;
END-MARKER-PU-#### doctrine for any new probes.*
