# OPERATOR RUNBOOK — boot, env flags, journal events

Companion to `SESSION_NOTES.md` (items 11–18). Written 2026-09-28 for the
`feat/ui-pipeline-compact-tabs` state: fast-lane dispatch, sub-cadenced
event_monitor (+ optional alternating windows), Laya CPU triage guards,
Laya decision-bundle advisory, env-level live lockout, heartbeat-aware
session executor, unified dashboard percentiles.

Journal of record: `trading_state.db` → `operational_events`
(read-only probe pattern: `sqlite3 "file:trading_state.db?mode=ro"`).
KV state: `state` table (e.g. key `event_monitor:EURUSD`).
Strategy-health quick reference: §6.

---

## 1. Boot

### Boot bar (non-negotiable)
Free RAM **≥ 1.5GB** before any boot. Four torch `0xc0000005` crashes
(`c10.dll` / `torch_cpu.dll`) on 2026-09-28 01:16–02:35Z, each dying faster
as free RAM fell (1.19 → 1.04GB). `KRONOS_FORCE_HEURISTIC=1` does NOT help —
the startup training pipeline imports torch regardless.

```bat
python -X utf8 -c "import psutil; print('%.2fGB' % (psutil.virtual_memory().available/2**30))"
```

### Manual boot (preferred when RAM is verified)
```bat
START_TRADING.bat
```
Default branch = paper AUTO continuous:
`python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO
--model mistralai/mistral-7b-instruct-v0.2 --risk-per-trade 1.0 --continuous`
Set `HEADLESS=1` and `CI=1` to skip the end-of-run pause. Any `--`-prefixed
args are appended to that command line; anything else runs as a custom
command. Paper mode is UNAFFECTED by the live lockout (section 2.1).

### Guarded auto-boot (`artifacts/watch_guarded_boot.py`)
Single-shot watcher for "boot when the box can hold it":

```bat
python -X utf8 artifacts\watch_guarded_boot.py            :: run in a window
python -X utf8 artifacts\watch_guarded_boot.py --selftest :: print config + free RAM
```

| Env | Default | Meaning |
|---|---|---|
| `WATCH_GUARDED_BOOT_BAR_GB` | `1.5` | free-RAM bar to spawn the boot |
| `WATCH_GUARDED_BOOT_WINDOW_H` | `4` | give up (clean no-boot exit) after this |
| `WATCH_GUARDED_BOOT_POLL_S` | `20` | poll interval |

Behavior: name-aware trader check (`python.exe` running `mt5_trader.py llm-trade`;
**two consecutive agreeing polls** — ephemeral one-poll cmdline mentions from
other agents' tooling have produced false positives) refuses a double-boot;
logs every decision to `artifacts/watch_guarded_boot.out` **before** acting;
session stdout/stderr → `artifacts/watch_guarded_boot_session.log`.
Single-shot: one spawn, then exits — a crashed boot is NOT re-spawned (no
boot loops). History: the 10:04:10Z fire failed (`'START_TRADING.bat' is not
recognized` — bare filename to `cmd /c`); fixed 10:27Z to spawn the absolute
path. Verified live: 10:28:30Z spawn (2.78GB free) → `run_started` 10:29:17Z.

### Watchers to have armed during/after a session
| Watcher | Fires | Outputs |
|---|---|---|
| `artifacts/watch_2h_duty.py` | live session crosses the 2.0h span floor | `artifacts/watch_2h_duty.out`, `artifacts/watch_2h_followup.out` (per-gen logs `watch2h_<ts>.log/.err`) |
| `scripts/run_session_followups.py` (`--no-wait --skip-tests`) | post-session; heartbeat-staleness detects crash corpses (`HEARTBEAT_STALE_S`, default 900s) | `artifacts/session_followup_<UTC>.json` |
| `scripts/session_report_hook.py` | graceful shutdown (wired in `core/crash_recovery.py`) | archives `artifacts/scheduler_tuning_report_<UTC>.json` (prune 60); includes per-stage boot-fetch watchdog trips; disable with `LLM_TRADER_POST_SESSION_REPORT=0` |

Re-arm the 2h duty watcher if it dies — it has died once (original PID 28544
was gone by 10:29Z while its session was live).

---

## 2. Env flags

### 2.1 Live lockout (`intelligence/llm_orchestrator.py`)
| Flag | Values | Meaning |
|---|---|---|
| `REQUIRE_LIVE_CONFIRMATION` | `1`/`true`/`yes`/`on` | master switch for live order placement |
| `LIVE_CONFIRMATION_TOKEN` | exact sentence below | operator acknowledgment |

`LIVE_CONFIRMATION_TOKEN_VALUE = "I_UNDERSTAND_LIVE_ORDERS_WILL_BE_PLACED"`
(documented constant on the class). Semantics: **fail-closed** (unset,
partial, or malformed config = locked; `0` stays locked); read at call time
(flip without restart); enforced at the START of `_build_execution_plan`'s
live branch — before any live-broker call. Gates NEW risk only: the paper
branch and emergency/risk-reducing exits are not gated. A rejection is the
pre-submit `ValueError` → auditable HOLD + `REJECTED / live_order_lockout`
lifecycle row + WARNING event. SECURITY: the failure notice points at the
constant and never contains the literal token (reasons persist into
journal/lifecycle rows).

### 2.2 Laya news-triage guards (`data/news_triage.py`)
| Flag | Default | Meaning |
|---|---|---|
| `NEWS_TRIAGE_MIN_FREE_RAM_GB` | `1.8` | refuse the model load below this (fail-closed) |
| `NEWS_TRIAGE_LAYA_MAX_ARTICLES` | `12` | hard per-batch article cap |
| `NEWS_TRIAGE_LAYA_TIME_BUDGET_S` | `60` | wall-clock budget per batch |
| `NEWS_TRIAGE_MODE` | `off` | `laya` = unconditional opt-in (bypasses gates); `off`/unset = deterministic-only unless auto-mode gates pass. Paper launches (since 2026-09-30) default unset → `off` via `apply_paper_triage_default()` (mt5_trader.py) so boots never silently spend ~25 min in CPU Laya triage; an explicit value (shell/`.env`/launcher passthrough like `TRIAGE_MODE=full`) always wins |

Runtime circuit (code constant, not env): 2 consecutive failure batches ⇒
900s cooldown, checked BEFORE model resolution. Force-off per call:
`laya=False` sentinel (None still resolves the singleton). Deferred articles
carry reasons `laya_batch_cap` / `laya_time_budget` / `laya_circuit_open`;
summary gains `laya_used` / `laya_errors` / `laya_batch_cap` /
`laya_circuit_open`. Benchmarks: deterministic 0.184ms/article; real
checkpoint step B needs ≥1.8GB free (re-run `artifacts/bench_laya_real.py`).

### 2.2b compact-context preflight (payload audit R1)
| Flag | Default | Meaning |
|---|---|---|
| `LLM_TRADER_COMPACT_CONTEXT` | unset | `1` = rolling-window LLM history + summaries (brain/memory.py) AND the launch preflight runs `artifacts/ctx_probe.py` (abort on RED); unset/`0` = legacy full-replay behavior, no probe |
| `LLM_TRADER_TAIL_COLLAPSE_CHARS` | `600` | sections ≤ this size fold into `minor_context_summary`; `0` disables (R5) |

Probe report: `artifacts/ctx_probe.txt` (END-MARKER-footed). Harness evidence
PU-0129…0137: RED aborts before trader launch in both bats; explicit args
bypass the auto-launch branch entirely (same semantics as SMOKE_PREFLIGHT).

### 2.2c LLM router quota pause (`data/llm_router.py`)
On a 429 carrying a reset timestamp (OpenRouter `X-RateLimit-Reset`, `Retry-After`,
or an ISO stamp in the body), the provider is parked until reset + margin instead
of burning sleep-retries on every decision; the router then skips paused providers
instantly (a paused primary with failover off goes straight to deterministic HOLD).
429s *without* a parseable reset keep the classic backoff path.

| Env | Default | Meaning |
|---|---|---|
| `LLM_ROUTER_QUOTA_PAUSE_MARGIN` | `30` | seconds added on top of the parsed reset |
| `LLM_ROUTER_QUOTA_PAUSE_MAX` | `86400` | hard clamp on any single pause |
| `LLM_ROUTER_QUOTA_PAUSE_MIN` | `60` | resets closer than this use backoff retries instead |

Signals: `quota_paused_until`/`PAUSED_QUOTA` in `router.get_status()`; error strings
`"paused until quota reset"`; a successful call clears the pause immediately (an
early recovery is honored). Tests: `tests/test_llm_router_quota_pause.py`.

### 2.2d boot fetch watchdog (`intelligence/boot_fetch_watchdog.py`)
Every stage of `pre_pulse_macro_ingest` (calendar_collect → bulk_calendar_ingest →
data_source_pipeline → realtime_news_ingest) runs under a per-source deadline; a
hung provider is abandoned at its budget and the pulse proceeds on degraded data
(fail-open) with a `boot_fetch_stage_timeout` WARNING event. Threads are not
killed (impossible in Python) — the budget bounds *waiting*, so one wedged
provider can no longer stall the first cycle.

| Env | Default | Meaning |
|---|---|---|
| `LLM_TRADER_BOOT_STAGE_BUDGETS` | `calendar_collect=30, bulk_calendar_ingest=30, data_source_pipeline=45, realtime_news_ingest=45` | per-stage seconds, `name=seconds` comma-separated; invalid entries are logged and dropped |

Stage outcomes (`ok`/`timed_out`/`elapsed_s`/`budget_s` per stage) are recorded in
`_pulse_macro_cache[symbol]["boot_stage_outcomes"]`. Tests:
`tests/test_boot_fetch_watchdog.py`.

### 2.2e Fetcher degradation (FRED/NewsAPI + bootstrap PREPARE stages)
Night of 2026-09-30 the FRED/NewsAPI calls failed across the board and each
failure burned 38–94s of silent boot time (no effective timeout inside the
third-party clients; exception-only guards). Two layers now bound this:

1. **`data/api_manager.py`** — every `newsapi`/`fred` network call runs under
   `_run_bounded` (per-call budget: `NEWSAPI_BUDGET_S` 15s, `FRED_BUDGET_S` 20s).
   On timeout/failure `get_news`/`get_hot_topics` serve the **last good cached**
   payload (a stale headline beats a hang, and the degrade is logged as
   WARNING); `get_fred_data` skips the hung series and keeps walking the batch.
2. **`core/autonomous_bootstrap.py` PREPARE** — the network-heavy steps
   (2b macro seed, 2c2 macro bridge, 2c3 alt data, 2c4 Yahoo, 2c5 COT,
   2c7 ForexFactory, 2c8 Exness) run under `_boot_stage` deadlines
   (`BOOT_STAGE_BUDGET_S`, default 30s each); a timeout becomes the step's
   normal SKIPPED degradation. The `PREPARE Complete` log line now discloses
   per-step elapsed seconds so silent slow fetches are visible at a glance.

Tests: `tests/test_fetch_degradation.py`.

### 2.2f Boot datasheet — one table for at-a-glance boot health
After each boot, the **first** pre-pulse ingest renders a single fixed-width
table (≤80 chars/line) merging three layers that previously logged separately:

* **Bootstrap PREPARE steps** — status + elapsed seconds per step (2a–2c8),
  with payload counts in the note column (entities/articles/events/...). A
  bounded-stage timeout shows TIMEOUT + the budget reason, not a bare SKIPPED.
* **Boot-fetch watchdog pulse stages** — OK/TIMEOUT/FAIL per pre-pulse stage
  (calendar_collect, bulk_calendar_ingest, data_source_pipeline,
  realtime_news_ingest) with the same budgets as §2.2c.
* **APIManager degradation markers** — every fail-open point records a row:
  STALE (cached payload served), EMPTY (no cache), SKIP (backed-off or hung
  series skipped, service not configured), ABORT (FRED batch network abort).

The table prints to the boot log (`BootDatasheet`) **once per boot** and is
journaled as a `boot_datasheet` event (INFO severity, payload carries the
full table + structured summary: step/pulse/marker counts, slowest step,
degraded sources). Later pulses never re-print it. Set
`LLM_TRADER_BOOT_DATASHEET=0` to disable recording entirely. Two previously-
unbounded bootstrap fetches were additionally bounded while wiring this:
2b macro seed (the 94.5s session-5 offender) and 2c6 EIA — both now honor
`BOOT_STAGE_BUDGET_S` (default 30s).

**Provider scorecard.** The same first-render moment journals a
`provider_scorecard` event (one per boot): per-source verdicts —
`FRESH` (served fresh data), `STALE` (last-good cache served), `SKIP`
(backed-off / not configured / hung series skipped), `NONE` (produced
zero rows), `ABORT` (batch network abort), `UNSEEN` (known source never
called) — plus a **trend vs the previous boot**: per-source
WORSE/BETTER/SAME/NEW/GONE and an overall `regressed` flag. Severity is
WARNING when this boot regressed vs the previous one, else INFO, so a
degradation spiral is visible from the journal alone (query
`event_type='provider_scorecard'`). Sources counted:
`fred.get_series`, `newsapi.get_news`, `newsapi.get_hot_topics`.

Tests: `tests/test_boot_datasheet.py`, `tests/test_provider_scorecard.py`.

### 2.2g Scheduled-agent external calls (deadline pattern outside boot)
The boot-path bounds (§2.2c–2.2e) did not cover scheduled-agent/cycle call
sites that reach providers directly. Those now use the same wait-bound:

* **`run_bounded.py`** (repo root, alongside `rate_limiter.py`) is the shared
  helper — daemon-thread wait-bound, `(ok, result, error)` contract. New call
  sites should use it; APIManager/watchdog keep their pre-existing locals.
* **`query_seeking_alpha_news`** (research tool): both live calls bounded
  (`SEEKING_ALPHA_BUDGET_S`, default 15s) — a hang returns honest
  `OFFLINE_PROVIDER_UNAVAILABLE` instead of stalling the agent; timeouts land
  a datasheet degradation marker (visible in the provider-scorecard trend).
* **`query_fred_macro_series`**: the managed-APIManager fetch is bounded
  (`FRED_BUDGET_S`, default 20s) with the same marker on timeout.
* **`LiveIndicatorFetcher._fetch_fred_series`** (news-trading NFP/CPI/ESI
  snapshots): previously called the raw `fredapi` client, bypassing every
  bound — now bounded (`FRED_BUDGET_S`); a hung series becomes an honest
  `MISSING` snapshot instead of a stalled lane.

Tests: `tests/test_fetch_degradation.py` (scheduled-agent section).

### 2.2h Boot-watchdog trips in the tuning report
The post-session scheduler tuning report
(`scripts/scheduler_tuning_report.py`, archived by the shutdown hook) now
aggregates `boot_fetch_stage_timeout` events **per stage**: trips, configured
budget, p95 observed elapsed, first/last trip stamps, and a suggested budget
(p95×1.5 — the same raise heuristic as handler overruns, without the
interval/2 cap since boot stages have no schedule interval). This puts
hung-provider evidence in the same duty comparison as handler overruns and
schedule skips — no separate journal query needed. Apply suggested raises to
`LLM_TRADER_BOOT_STAGE_BUDGETS` or `_DEFAULT_STAGE_BUDGETS` in
`intelligence/boot_fetch_watchdog.py`. First live read (2026-09-30 journal):
`data_source_pipeline` tripped its 45s budget on 4 session-5 pulses (FRED/
NewsAPI hang that night) — suggested 67.9s.

Tests: `tests/test_scheduler_tuning_report.py` (`TestBootStageTimeouts`).

### 2.3 event_monitor cadence (`intelligence/llm_orchestrator.py`)
| Flag | Default | Meaning |
|---|---|---|
| `EVENT_MONITOR_FULL_PIPELINE_MIN_S` | `300` | heavy block (FRED/news/hot-topics/pipeline/realtime) runs at most this often; calendar stays 60s |
| `EVENT_MONITOR_SPLIT_WINDOWS` | off | `1`/`true`/`yes`/`on` = alternate halves: even window = graph pipeline, odd = realtime-news; the skipped half is tagged `realtime_deferred` |
| `TRADING_REALTIME_NEWS_EXTRACT` | `false` | LLM claim extraction during realtime ingest |

Window/counter contract: `_event_monitor_last_full_ts` (stamp) and
`_event_monitor_window_no` advance ONLY after a successful window — a failed
window retries the same parity next dispatch; restart resets to the pipeline
half first.

### 2.4 Executor / watchers
| Flag | Default | Meaning |
|---|---|---|
| `HEARTBEAT_STALE_S` | `900` | open run + heartbeat staler than this ⇒ session treated as ended (fail-closed on malformed beats) |
| `LLM_TRADER_POST_SESSION_REPORT` | on | `0` disables the shutdown report hook |
| `WATCH_GUARDED_BOOT_*` | §1 | auto-boot watcher tunables |

---

## 3. Journal event glossary (`operational_events`)

### Session lifecycle
| Event | Meaning | Operator action |
|---|---|---|
| `run_started` | a trading run began (the boot worked) | start the clock: crash window ≈ first 5 min; 2h floor ≈ +2h |
| `run_finished` | graceful shutdown | post-session drill (§4) |
| `run_stale_recovered` | previous run never wrote `run_finished` (crash corpse) — recovered on next boot | expect a crash post-mortem; check `session_*.err` |

### Scheduler family (the arc under test)
| Event | Meaning | Operator action |
|---|---|---|
| `schedule_due` | a cadence claim point (per-schedule) | duty-cycle denominator |
| `schedule_handler_overrun` | handler exceeded its budget — payload `schedule`, `budget_s`, `elapsed_s`, `over_by_s` | feeds tuning report; collapse target: event_monitor count vs 37 baseline (p95 125.6s) |
| `boot_fetch_stage_timeout` | boot watchdog abandoned a pre-pulse stage at its deadline — payload `stage`, `budget_s`, `elapsed_s` | feeds the tuning report's per-stage "boot-fetch watchdog trips" section (trips, p95 elapsed, suggested budget from p95×1.5) |
| `schedule_skipped` | claimed batch abandoned before dispatch; `reason` field (`wave_deadline`, legacy `unattributed`) | should stay 0; nonzero = capacity pressure |
| `schedule_session_skip` | session-skip watchdog: claimed-but-undispatched visible silence | investigate starvation immediately |
| `schedule_skips_cleared` | watchdog RECOVERED (latest event per schedule wins) | none |

### Handler completions (per-dispatch evidence)
`event_window_checked` (event_monitor; check its store key's
`data_source_pipeline.pipeline_mode` — see §5), `market_health_checked`,
`position_reconciliation_checked`, `causal_edges_validated`,
`catalog_measurement_complete`, `session_memory_reviewed`,
`post_event_review_candidates`, `execution_reviews_escalated`,
`macro_event_evidence_failed` (expected without API keys),
`circuit_health`, `task_claimed`, `working_memory_updated`,
`snapshot_collected`, `journal_checkpoint`, `volatile_tables_pruned`.

### Learning loop
`proposal_received`, `proposal_rejected_overfit_not_validated` /
`proposal_rejected_overfit_reject` (overfit gate held — good),
`request_reserved`, `lifecycle_evaluation_failed`.
Contract-checked agent events: `strategy_lifecycle_evaluated` (hourly QC
schedule succeeded — INFO, or CRITICAL when it applied transitions),
`cycle_survival_audit_attached` (per-cycle quant audit reached the prompt),
`cycle_survival_audit_failed` / `lifecycle_evaluation_failed`.
HISTORY: 2026-09-28 ≤11:27Z every hourly attempt logged
`lifecycle_evaluation_failed` with "QuantitativeCoach is not permitted to
perform evaluate_strategy_lifecycle" — the work ran, but the journaling call
hit the contract gate. Fixed by granting QC the two analytical actions
(operations/agent_contracts.py); takes effect on the next process start.

---

## 4. Post-session verification drill
1. `python -X utf8 scripts/scheduler_tuning_report.py --min-span-hours 2.0`
   → per-schedule FIXED/IMPROVED/UNCHANGED/WORSE vs pre-restore baseline
   (6–36%). Expected: EXIT 0, RAISE none, WATCH rows thin-baseline.
2. `python -X utf8 scripts/apply_schedule_budget_raises.py --verify`
   → provenance `confirmed` on every row (then `--apply` dry-run gate holds).
3. Executor strict `dashboard_check` (in `run_session_followups.py`):
   PASS expected — dashboard n/p50/p95 now match the journal exactly
   (unified `_percentile`; pre-unification FAILs: count 2 vs 37,
   p95 281.6 vs 125.59 — gone as of the 07:33Z read).
4. Collapse check: event_monitor overrun count and the
   `pipeline_mode` distribution from `state.event_monitor:EURUSD`.

## 5. Reading `event_monitor:<SYMBOL>` state
`data_source_pipeline.pipeline_mode`:
- `full` — heavy block ran whole (single-window mode; `full_every_s` set)
- `calendar_only` + `status=DEFERRED` — sub-cadence deferral (healthy:
  4-of-5 dispatches should look like this in a dense session)
- `pipeline_window` / `realtime_window` — split-windows mode halves
- `realtime_deferred` (in `realtime_news_ingest`) — split mode skipped half
`realtime_news_ingest.status`: `PROCESSED` / `DEFERRED` / `SKIPPED` (paper)
/ `ERROR`.

---

## 6. Strategy improvement & swing/scalp engine health

The two candle engines are independent state machines resolved by
`resolve_strategy_cycles` (`both` → swing H1/H4/D1 + scalp M1/M5/M15). Each
completed candle claims an `analyse_completed_candle` task; healthy evidence
is `cycle_brief_recorded` per `payload.strategy` (scalp keys cadence ≈ M15,
swing ≈ H1).

Healthy-pattern checklist (2026-09-28 verified):
- `cycle_brief_recorded` rows for BOTH `scalp` and `swing` in the last few
  hours (same cycle_key repeating 2–3× is normal: brief re-recorded on retry
  within the same candle window).
- Proposal funnel moving: `proposal_received` n>0, overfit rejects present
  (gate holding), occasional `proposal_validated`.
- `strategy_lifecycle` board states make sense: automation may promote only
  to PAPER_PROVEN and may demote DEPLOYED→DECAYING→FROZEN; every
  DEPLOYED/READY_FOR_DEPLOYMENT row needs an operator reason in
  `strategy_lifecycle_history` (append-only).
- `agent_runs`: exactly one trader row `RUNNING` with fresh `heartbeat_at`;
  older rows should be STOPPED/STALE_RECOVERED.
- `queue_health`: `running`/`lease_expired` = 0.

Zombie task cleanup after a crashed/killed process: expired
`analyse_completed_candle` RUNNING rows (e.g. killed mid-cycle, keys from
days ago) never reclaim (their cycle keys don't recur) — sweep them with the
sanctioned recovery, from repo root:

```bat
python -X utf8 -c "import sys; sys.path[:0]=['operations','research']; from agent_operations import AgentOperations; o=AgentOperations('trading_state.db'); print(o.recover_stale_tasks(900), o.queue_health())"
```

### 6.1 External inputs landed Oct 2026 (status annotations)

- **Zero-lag macro provenance** — `data/zero_lag_macro_provider.py` exposes
  `source_quality_annotations()`: every hardcoded physical-proxy ratio is
  labelled `LIVE` or `SYNTHETIC_PLACEHOLDER` based on whether its upstream
  Yahoo quotes are cached and differ from the placeholder anchors. Check the
  annotation before trusting a ratio in LLM context or gate logic.
- **TradingView advisory bridge (ingest-only)** — `data/tradingview_bridge.py`
  receives TV webhook alerts on POST `/alert`, auth-gated via env
  `TV_WEBHOOK_SECRET` (header `X-TV-Token`). Rows land in `order_intents` with
  status `ADVISORY`: they are never executed and are invisible to the pending
  reconciler and the killswitch cancel loop. Starting the server:
  `python -m data.tradingview_bridge` (bind/port default 127.0.0.1:19887).
- **Social evidence tier** — `SOCIAL_EVIDENCE_ONLY` in
  `intelligence/macro_source_policy.py` permits ingest of whitelisted social
  entity posts via `data/social_entity_ingest.py` (Trump/@POTUS, central
  banks, market outlets; override via `data/social_watchlist.json`). Posts are
  journaled for LLM context with `causal_authority=False` — the causal block
  on social domains is unchanged.
- **Boot stale state** — daemon writes a lease-backed `engine.pid`
  (JSON: pid + stamped_at); a live prior lease refuses a second start
  (single-instance discipline). `operations/live_state_probe.py`
  (`probe_live_state` / `sweep_live_state`) reconciles stale schedule claims
  and ghost `RUNNING` agent_runs at boot; every recovery is WARNING-logged.: the
confluence/news/regime gates and the CRITICAL-grade overfit filter are doing
their job (`proposal_validated` payloads show the veto reasons).
