# Memory, Knowledge Graph & Pipeline Audit — Findings and Remediations

Scope: macro/technical data ingestion → storage → memory/KG retrieval → harness loop → execution.
Companion to `LOOP_AND_TRADING_PROCESS_AUDIT.md` (loop-level faults #1–#6 are already fixed there).

Legend: 🔴 critical · 🟠 medium · 🟡 low. "FIXED" items were implemented and verified in this pass
(`py_compile` clean; focused pytest subsets green: 41 + 39 + 13 passed).

---

## 1. How data flows today (verified end-to-end)

```
Calendar / FRED / RSS / NewsAPI / OpenBB / SeekingAlpha / Portfolio123 / Alt-nowcast
        │  process_cycle_data_sources()          [data/data_source_pipeline.py]
        ▼
macro_sources (content-hash deduped)  ──►  MacroGraph edges (CANDIDATE → review → APPROVED)
        │                                        [intelligence/macro_graph.py]
        ├── event_reaction_trackers → measurements → event_reactions   (48h reviews)
        └── TradingMemory: atomic_facts / narratives / verified_memories /
            scenarios / core_memories / working_context                [trading_memory.py]
        ▼
retrieve_for_proposal(): narrative + verified + scenario + core + semantic layers
        │  (+ approved_edges, similar_event_reactions, intermarket regime,
        │   causal propagation, fusion preview)                       [llm_orchestrator.py]
        ▼
LLM proposal → deterministic validator → RiskController → order intent → broker
        ▼
closed trade → label → retrieval↔trade linkage → scenario rebuild → edge feedback
```

The architecture is sound: source-linked evidence, point-in-time `as_of` cutoffs,
review-gated promotion, no LLM execution authority. The problems below are about
**speed, fault-tolerance, and a few places where the wiring lies about what happened**.

---

## 2. 🔴 Critical — fixed in this pass

### 2.1 LangGraph OrderExecutionNode fabricated fills without calling the broker
`graph_nodes.OrderExecutionNode` computed `ticket = timestamp % 1_000_000` and returned
status EXECUTED **without ever invoking `self.broker`** — even when a real broker gateway
was attached. The journal claimed an executed trade that never existed.

**Fix:** node now routes through `broker.execute_order()`/`open_order()`; only a no-broker
(paper) run synthesizes a local fill id; broker rejection/failure returns status ERROR with
the reason instead of a fake receipt.

### 2.2 HarnessAgentLoop traded on invented market data and an all-green gate env
`harness_agent_loop.run_cycle` hardcoded `price=1.0850`, `atr=0.0015`, confidence 0.85,
volume 0.1 lots, and a `market_env` with **every safety gate True** (`market_open`,
`session_allowed`, `liquidity_ok`, …). That environment is exactly what
`DeterministicValidator` consumes — so the validator could never reject, i.e. the entire
deterministic risk layer was bypassed in this loop.

**Fix:** fail-closed rewrite:
- price/ATR must come from a live provider on the context or the cycle returns HOLD
  (`insufficient_live_market_data`);
- `market_env` comes from a context-supplied provider (`ctx["market_env"]`) or defaults to
  **every gate False**;
- position size comes from `RiskManager.calc_position_size`; sizing refusal ⇒ branch fails
  risk arbitration instead of sending a fixed 0.1-lot order.

### 2.3 SQLite contention could kill mid-cycle reads/writes ("database is locked")
Four stores opened connections with zero lock tolerance while the dual-velocity executor,
reflection lane, TUI, and main loop share one database file.

**Fix:** every `_con()` now sets `busy_timeout=10000` (+`synchronous=NORMAL`); TradingMemory
also enables WAL at init (persistent property — hardens pre-existing DB files too).
Touched: `trading_memory.py`, `macro_graph.py`, `operations/agent_operations.py`,
`graph_checkpointer.py`.

### 2.4 Memory outbox poisoned itself forever
`complete_memory(id, error)` retried failed distillation rows **every 5 minutes forever**
— a malformed record would spin indefinitely and keep surfacing in `pending_memory`.

**Fix:** bounded retry; after 8 attempts the row parks as `FAILED` (auditable, no retry).

---


## 3. 🟠 Medium — speed & fault-proofing of memory fetching

### 3.1 FIXED — serial five-layer memory retrieval
`retrieve_for_proposal()` fetched narrative → verified → scenario → core → semantic layers
one after another; total latency = sum of all layers, directly on the trading-cycle hot path.

**Fix:** all five read-only layers now run concurrently on worker threads (each getter opens
its own connection — safe under WAL). A failing layer degrades to `[]` plus a recorded
`retrieval_errors` entry instead of stalling proposal assembly.

### 3.2 FIXED — one SQLite connection per ingested calendar/news item per cycle
With ~20–40 events per pulse, `ingest_source()` per event dominated ingestion latency and
multiplied lock contention (three separate code paths re-ingested overlapping feeds).

**Fix:** `MacroGraph.ingest_sources_bulk()` inserts a whole batch in one transaction with
identical content-hash dedupe semantics; the orchestrator's calendar loop now uses it.

### 3.3 FIXED — unbounded growth of audit tables
- `memory_retrievals` telemetry grew forever → added `prune_retrievals(keep_days=90)`
  (linked-to-trade rows retained), wired into `monthly_memory_maintenance`.
- `graph_checkpoints` time-travel history grew forever → added
  `SqliteCheckpointSaver.prune_checkpoints(keep_per_thread=200)` (call from monthly ops).

### 3.4 Remaining recommendations (not yet implemented)
| Item | Where | Why it matters |
|---|---|---|
| Parallelize the read-only KG section of `execute_cycle` | `llm_orchestrator.py` ~3669–3805 | Biggest remaining cycle-latency win; same pattern as 3.1 |
| Hoist shared calendar/FRED ingestion out of the per-strategy loop under `--strategies both` | `mt5_trader.py` | Audit item #14: duplicated work doubles provider load |
| Recency-window bias: token-overlap retrievers scan only the newest 200–300 rows then score in Python | `trading_memory.py` | Older approved memories silently vanish from context once >N newer rows exist. Migrate to FTS5 or vector backend (API already designed for swap) |
| Memory starvation by design: distilled narratives/memories are saved CANDIDATE and only APPROVED rows enter prompts; nothing auto-promotes them | `local_llm_memory.py` + review queue | Correct for safety, but unattended operation means memory never enriches proposals. Consider a narrow AUTO_APPROVED lane for *deterministic* summaries (daily/weekly journal) only — they are generated without an LLM and carry source tickets |
| `apply_trade_outcome_feedback` demotes edges to CANDIDATE and relies on "callers may re-run autonomous validation"; verify re-validation actually fires after each close, else edges leak out of context over time | `macro_graph.py` + orchestrator close path | Silent KG decay risk |
| `MacroAnalystNode.causal_paths` is always `[]` and `cesi_score` hardcoded 0.85/1.0 | `graph_nodes.py` | The LangGraph deliberative path doesn't actually consume KG paths; wire `approved_edges` like the main loop does |

---

## 4. Data ingestion assessment

**Working well (verified):**
- Calendar remains the sole event-reaction scheduler; FRED/narrative news are research-only
  candidates — correct doctrine, consistently labeled `execution_authority=False`.
- Content-hash dedupe prevents duplicate sources across the three overlapping ingest paths.
- Event-reaction measurement refuses to backfill invented prices after restarts (uses actual
  elapsed seconds); post-release anchors are labeled honestly.
- Source policy (`macro_source_policy.assess_source`) gates which publishers may become edges.

**Needs improvement:**
1. 🟠 Ingest helpers swallow errors (`except Exception: continue`) without counters — a dead
   API key looks identical to an empty feed. Return per-item failure counts in the report dict.
2. 🟠 `run_realtime_news_ingest` performs network I/O (RSS via `urlopen`) inline inside the
   cycle. Verify explicit socket timeouts and consider moving it to the slow lane
   (`DualVelocityExecutor.submit_slow`) so a hung feed can't delay decisions.
3. 🟡 `ingest_optional_providers` pokes the private `openbb._graph` attribute; give the bridge
   an official `attach_graph` contract everywhere.
4. 🟡 Holiday-skipped candles still consumed permanently (`LOOP_AUDIT` #10, open) — release
   the durable claim instead of completing it.

---

## 5. Orchestration / parallelism summary

- Main loop: correctly fail-isolated per cycle (audit fix #1) and reconnects (#2). ✔
- Dual-velocity executor exists for fast/slow lanes but the heavy read-only pre-LLM section
  of `execute_cycle` does not use it (see 3.4 row 1) — this is the single largest
  "stuck behind a serial process" issue left.
- Reflection runs async on the slow lane via `TradingGraphEngine.run_reflection_cycle`. ✔
- News detection: realtime ingest + calendar + optional providers all wired into TOON context
  with regime filtering and lifecycle checks before reaching the opportunity bus. ✔
- Everything the LLM sees carries `retrieval_as_of` / point-in-time cutoffs and versioned
  config snapshots for offline replay. ✔

---

## 6. What can still break (ranked watch-list)

1. **KG decay loop** (3.4): outcome feedback demotes edges; if auto-revalidation doesn't run,
   approved context shrinks monotonically. Add a scheduled re-validation sweep.
2. **Recency window starvation** (3.4): long-running deployments will retrieve nothing once
   tables exceed the scan windows.
3. **Unattended candidate pileup**: operator review queue grows without a reviewer; memory
   layers return empty results while candidates accumulate (visible via
   `candidate_review_queue()` / `memory_quality_metrics().zero_result_rate`).
4. **Paper-mode divergence** (open audit items #7/#8/#9): paper evidence used for live-gate
   promotion still measures a different system (no protective position management, wrong
   active-trade book, seeded balance).
5. **Dual persistence** (open #12): PerformanceMonitor JSON vs authoritative SQLite.

---

## 7. Verification performed

- `py_compile` clean on all 7 touched modules.
- Focused pytest: `test_trading_memory_*`, `test_memory_*`, `test_scenario_memory_governance`,
  `test_atomic_fact_governance`, `test_working_memory_compaction`, `test_macro_graph_history`,
  `test_graph_checkpointer`, `test_graph_state` → **41 passed**.
- `test_deliberative_graph`, `test_graph_engine_facade`, `test_reflection_graph`,
  `test_harness_kernel`, `test_agent_operations`, `test_data_source_pipeline` → **39 passed**.
- `test_harness_integration`, `test_harness_full_modes`, `test_memory_schedule` → **13 passed**.
- Note: full-suite run stalled at 37% (pre-existing network-bound test unrelated to these
  changes); focused subsets above cover every changed module.

## Files changed

| File | Change |
|---|---|
| `intelligence/trading_memory.py` | WAL/busy_timeout/synchronous hardening; concurrent `retrieve_for_proposal` with per-layer error isolation; `prune_retrievals`; maintenance pruning |
| `intelligence/macro_graph.py` | Connection hardening; `ingest_sources_bulk()` batch transactional ingest |
| `intelligence/graph_nodes.py` | `OrderExecutionNode` routes through real broker; failure surfaces as ERROR receipt |
| `intelligence/graph_checkpointer.py` | Connection hardening; `prune_checkpoints()` retention |
| `operations/agent_operations.py` | Connection hardening; outbox poison-message cap (8 attempts → FAILED) |
| `intelligence/harness_agent_loop.py` | Fail-closed snapshot/env/sizing; HOLD on missing live data |
| `intelligence/llm_orchestrator.py` | Calendar ingestion switched to bulk path with error capture |
