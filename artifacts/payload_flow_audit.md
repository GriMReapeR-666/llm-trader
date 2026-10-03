# Payload-Flow Audit: Node Payloads → LLM Context

**Date:** 2026-09-29 · **Method:** static inventory of `intelligence/llm_orchestrator.py` context assembly + read-only probes over the live journal (PU-0093…0098, marker-verified).

## Question

"Are all the payloads generated from all nodes fed to the LLM and processed?"

## Answer

**No — by design, not everything reaches the LLM.** The context builder curates aggressively. But the live data shows the bigger story: the real token cost driver is not per-section inclusion at all — it is **replayed conversation history**, which currently accounts for **~79% of every prompt**.

## 1. Static inventory (context builder, `get_decision` → `toon_lite`)

The builder assembles ~90 keys from the `toon` payload with three mechanisms:

| Mechanism | Examples |
|---|---|
| **Hard slices** | `news_events[:5]`, `api_news[:5]`, `hot_topics[:3]`, `event_playbooks[:5]`, `macro_causal_context[:8]`, `similar_past_regimes[:3]`, `scenario_memory[:3]`, `core_memory[:8]`, `learned_patterns[:5]`, indicator/history arrays truncated to last 5 |
| **Allowlist projection** | `intermarket_regime`, `opportunity_fusion`, `causal_propagation`, `macro_state_vector`, `macro_opportunity_bus` — only named fields pass; `execution_authority` included explicitly |
| **Budget sanitizer** | `_sanitize_toon_lite_budget`: floats rounded to 5dp, news re-sliced, then **progressive pruning of 19 research keys** if serialization exceeds **60,000 chars (~15K tokens)** — documented target for 32k-context models |

Payloads produced by scheduler/module agents (13 module tools, scheduled agents like `market_health`, `event_monitor`, `lifecycle_evaluation`) do **not** flow straight into the prompt. They land in the journal (`operational_events`), the event bus, and working memory, and only reach the LLM through the bounded `working_memory` / memory sections of the context builder.

## 2. Live measurements (journal, read-only)

| Metric | Value | Source |
|---|---|---|
| LLM requests (7d) | 38 (`mistralai/mistral-7b-instruct-v0.2`) | PU-0093 |
| Status split | **22 ERROR / 16 SUCCESS** | PU-0093 |
| `llm_requests.payload` | 84 chars — **budget metadata only**, not context | PU-0094 |
| Real prompt store | `raw_traces.trace_type='llm_prompt'`: **avg 129,819 chars, max 287,319** | PU-0094 |
| Prompt composition | **78.9% replayed history**, 21.1% current payload; avg prompt 143,189 chars (~36K tokens) | PU-0098 |
| History content | Each prior cycle's full ~30K market-data JSON re-sent as user turns; every assistant turn is `deterministic_fallback` HOLD | PU-0097 |
| Current payload size | ~30K chars, 73 top-level sections (within budget) | PU-0098 |

Current-payload section ranking (PU-0098):

```
 22.3%  multi_timeframe            (5 TFs × last-5 OHLCV+indicator bars)
 10.2%  live_advisory_hub
  9.4%  alpha_mining_results
  8.2%  completed_feature_snapshot
  6.2%  news_trading_context
  4.9%  indicators
  4.7%  macro
  ...
  remaining 48 sections combined: ~3.3%
```

`working_memory` is NOT a problem: 4 ACTIVE items, ~11K chars each.

## 3. Findings

1. **The 60K-char sanitizer budget is structurally bypassed.** It sanitizes only the *current* `toon_lite`; conversation history (5–6 prior 30K payloads) is appended afterwards, so real prompts average **143K chars (~2.4× budget)** — consistent with the ERROR-dominant status split (context-length failures).
2. **The error loop compounds cost.** A failed call leaves a `deterministic_fallback` HOLD that is then replayed in subsequent prompts; ~79% of every prompt is dead-weight history containing mostly those fallbacks.
3. **Per-section curation is working as intended.** The current payload (~30K) fits the budget with room to spare; the much-feared "everything is dumped in" is not happening.
4. **Minor risk:** some context keys observed live (`live_advisory_hub`, `hierarchical_advisory`, `meta_label_advisory`, `advisory_regime`, `quant_audit`) are not in the sanitizer's prunable list, so a future bloat in those sections would not be pruned under budget pressure.

## 4. Recommendations (no behavior changed by this audit)

- **R1 (highest impact): stop replaying full historical payloads.** Keep at most a 1–2 turn rolling window, or replace replayed market-data turns with 200–300-char summaries. Expected: ~36K → ~10K tokens per prompt.
- **R2: make the sanitizer budget enforce the *whole request*** (system + history + current), not just `toon_lite`.
- **R3: break the fallback loop** — when a cycle fails, record a short outcome note instead of a full replay pair (also removes mostly-error history from context).
- **R4: add the four unpruned advisory keys to the prunable list.**
- **R5 (optional):** collapse the 48-section ~3% tail into a single compact summary blob.

## Artifacts

`artifacts/pu_payload_audit{,2,3,4,5,6}.py/.txt` (PU-0093…0098) — all read-only, all marker-footed.

## Verification addendum (2026-09-30)

R1–R4 implemented (opt-in R1 via `LLM_TRADER_COMPACT_CONTEXT`, default-on R2/R3/R4). Verification PU-0105…0109, all marker-footed:

- **Live session attempt:** two bounded `llm-trade --paper --loops 5` launches (baseline watermark PU-0105 captured first). Attempt 1 killed by the probe timeout during 9-min boot; attempt 2 hung 22 min in the news/indicators API layer **before any trader LLM call** (consistent with the FRED/provider failures behind the 22-error baseline) and was reaped by the wrapper. Journal delta PU-0107: only one boot-phase `memory`-strategy ERROR; no trader prompts reached the network. No verdict on the network round-trip is possible from tonight's session.
- **Pipeline-level verification (PU-0109, real components):** the actual production request-assembly path — real `SIGNAL_SYSTEM_PROMPT_TEMPLATE`, real `ContextBuffer`, real `_sanitize_toon_lite_budget` + `_enforce_request_char_budget` — over 6 realistic 30K-char cycles:

| Configuration | Request size | vs audit live avg (143,189 chars) |
|---|---|---|
| BEFORE (legacy replay, no R2/R3) | 134,473 chars (~33.6K tok) | −6% (reproduces the audit's live case) |
| AFTER (compact R1 + R2 budget) | **44,392 chars (~11.1K tok)** | **3.0× smaller, ≤24K-tok ceiling enforced** |
| AFTER + failed cycle (R3) | **14,351 chars (~3.6K tok)** | failed pair collapses to a summary |

**Conclusion:** the context-length failure mode (prompts at 2.4× budget against a 32k-context model) is structurally eliminated at the request-assembly level: R1/R3 remove the replayed-history weight and R2 hard-caps the whole request at ~24K tokens regardless of future payload growth. Network-level confirmation (ERROR rate → 0) awaits a session during healthy provider windows:
`LLM_TRADER_COMPACT_CONTEXT=1 python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO --model mistralai/mistral-7b-instruct-v0.2 --risk-per-trade 1.0 --loops 5 --no-dashboard`

Verification artifacts: `artifacts/pu_live_{baseline,delta,e2e,session,session2}.{py,txt}`, `artifacts/pu_kill_session.ps1`.

### Session 3 (2026-09-30 02:30–03:05 local, PU-0124…0126)

With `NEWS_TRIAGE_MODE=off` (fast boot; the "hang" in session 2 was the ~25-min CPU Laya triage phase, not a deadlock), the bounded `--loops 5` session completed end-to-end: boot → scheduled agents → 26 cycle ticks → clean exit. Journal rows in the window (PU-0126): **4 ERRORs, all `strategy='memory'`, all `error='memory batch contained failed items'`** (tiny-payload batch parse failures, unrelated to context length); **zero `trader` rows; zero `llm_prompt` traces** — every decision cycle hit the duplicate-candle gate (candle 2026-09-29 21:00 already analyzed), so `get_decision` was never entered. Additionally, **OpenRouter free-tier daily quota was exhausted** (`429 openrouter_free_tier_daily`, remaining=0, reset 2026-09-30 00:00Z) — a strong candidate for much of the historical 22-ERROR baseline alongside context length.

**Status of the network-level confirmation: inconclusive by environment** — the trader never assembled a prompt tonight, so there was nothing to measure. What stands: (a) pipeline-level proof PU-0109 — requests are structurally capped ≤96K chars (~24K tok) and 3× smaller with compact mode; (b) no context-length error class appeared anywhere in the window; (c) the boot stall is diagnosed and avoidable. A decisive run needs a fresh-candle boundary + available quota, e.g. after the 00:00Z reset:
`LLM_TRADER_COMPACT_CONTEXT=1 NEWS_TRIAGE_MODE=off python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO --model mistralai/mistral-7b-instruct-v0.2 --risk-per-trade 1.0 --loops 8 --no-dashboard`

### Session 4 + error-class correlation (2026-09-30, PU-0139…0143)

Session 4 (fresh un-analyzed M15 candle verified pre-launch, PU-0139) was killed externally mid-cycle-1 during the total provider outage (OpenRouter 429 daily cap, Cerebras 402 payment, NVIDIA 403 auth — all three down simultaneously; quota resets 00:00Z). Zero trader prompts again, zero context-length errors.

**Historical error-class correlation (PU-0143, decisive):** joining all 38 trader `llm_requests` rows to their `raw_traces llm_prompt` sizes:

- **13/27 ERRORs rode on >96K prompts** — including the Sept-28 escalating spiral 112,801 → 146,301 → 179,108 chars across consecutive failures. That spiral is the audit's failure loop made visible: failed cycle → full replay pair added to history → bigger prompt → more failures. R2 caps the request at ~96K and R3 replaces failed pairs with summaries, so this class is structurally eliminated.
- **9/27 ERRORs occurred at ≤96K prompts** (77K, 89K failed too) — provider-availability failures (quota/auth), matching the live 429/402/403 observed tonight. Not a context-length problem; unaffected by R2/R3.
- **6/16 SUCCESSes were also >96K** — providers sometimes tolerated oversized prompts, explaining why the failure pattern looked intermittent.
- All errors surfaced as generic `DETERMINISTIC_FALLBACK`/`PARSE_ERROR` with no explicit reason string — the router does not classify provider errors into the journal.

**Final status of "confirm context-length failures are gone":** the context-length error class (13/27 of the baseline) is structurally eliminated — PU-0109 proves the assembled request can no longer exceed ~96K chars, and PU-0143 shows exactly which historical errors that removes. The remaining error class is provider quota/auth availability, which needs router-level handling (pause provider until reset, classify errors in the journal). A post-reset live re-measure remains the last open step and requires only a healthy provider window.

### R1 wired into launchers (2026-09-30, PU-0127…0137)

`LLM_TRADER_COMPACT_CONTEXT=1` now also triggers a preflight probe in both launchers (same opt-in shape as `SMOKE_PREFLIGHT`): `artifacts/ctx_probe.py` verifies the buffer's compact behavior, legacy inactivity, and the R2 request budget (sandbox-only; report `artifacts/ctx_probe.txt`, END-MARKER-footed) and RED aborts the launch. START_DECK.bat: block after the project check; START_TRADING.bat: inside the zero-arg auto-launch branch after the QKF preflight. Explicit-args invocations bypass the branch (unchanged semantics). Harness-verified 6/6 scenarios through `cmd` with a stubbed python (RED aborts before trader launch; GREEN proceeds; unset skips; explicit command bypasses). Known bat-runtime trap fixed en route: both blocks use `call python` because a PATH-resolved `python.bat` otherwise discards the child's errorlevel. Env var documented in `OPERATOR_RUNBOOK.md` §2.2b. Both bat edits remain uncommitted (user's launch rewrites ride in the same files).

### R5 implemented (2026-09-30)

`_collapse_tail_sections` folds minor sections (serialized ≤ `LLM_TRADER_TAIL_COLLAPSE_CHARS`, default 600) into one `minor_context_summary` blob — one `key: ≤80-char head` line per section. Never collapsed: sections larger than the limit, dicts carrying `execution_authority`/`status` (gated projections, advisory state), single-entry payloads, and protected structural keys (account, positions, working memory, session, knowledge context, news). The blob is dropped first under extreme prune pressure (sits behind every named research key in the pruning loop). `LLM_TRADER_TAIL_COLLAPSE_CHARS=0` disables. Measured on an audit-shaped 60-section payload (PU-0121): **60 → 13 sections, 48 collapsed, serialization 25,334 → 12,292 chars (−51.5%)**; tests PU-0120: 56/56 green incl. 5 R5 cases (collapse, protected keys, opt-out, prune order, single-key guard).

### Provider outage investigation: NVIDIA 403 + Cerebras 402 (2026-09-30 08:15–08:55 IST, PU-0144…0148)

Question: are the NVIDIA keys invalid, the Cerebras account unbilled, or something else? All probes key-safe (masked fingerprints only); artifacts `artifacts/pu_provider_probe{,2,3}.txt`, `artifacts/pu_provider_timeline.txt`, `artifacts/pu_cerebras_history.txt`.

**Findings (probed 02:45–02:54Z, post-quota-reset):**

- **NVIDIA NIM — keys VALID, error was transient.** All 5 `NVIDIA_KEY_*` (`nvapi-` format, 70 chars) returned 200 on `GET /v1/models` AND a real 1-token chat completion on `meta/llama-3.2-11b-vision-instruct`. A payload ladder at 1K/10K/50K/100K/150K/200K chars was 200 at every rung (max 18.4s) — 403 is NOT body-size-triggered, so R2's 96K cap does not interact. Session-4's 403 `Authorization failed` matches a known NVIDIA-side flakiness pattern: build.nvidia.com's account-verification / "Public API Endpoints" permission layer intermittently revokes access while keys stay valid (developer-forum threads, incl. a pinned Feb-2026 NVIDIA staff "Account Access & Verification Update" acknowledging the broken verification pipeline; remediation channel help@build.nvidia.com). Current keys need nothing; if 403 recurs, check the build.nvidia.com account state first, not the keys.
- **Cerebras — key VALID, model VALID, account needs billing action.** `GET /models` → 200 (2 models: `gpt-oss-120b`, `qwen-3.8-27b` — the configured `LLM_ROUTER_MODEL_CEREBRAS` exists; the older `gemma-4-31b` archived-model error is gone). Chat → **402 `payment_required_error` (code 'quota')**: the account is on free-tier terms that now require payment/credits — this is an account-billing block, not fixable in `.env`. Remediation is console-side: add credits/billing (or qualify for whatever free allowance Cerebras currently grants). One earlier probe returned a Cloudflare 1010 UA block on raw urllib but 200 via the OpenAI SDK — probes must use the SDK (as the router does).
- **OpenRouter — healthy again post-reset.** Chat 200 on `nvidia/nemotron-3-super-120b-a12b:free`; `/api/v1/key`: `is_free_tier: true`, usage $0.104, no hard limit. (Quota resets 00:00Z as established.)
- **TokenRouter fallback slot is dead weight.** `TOKENROUTER_KEY` (`sk-kdo…`) gets 401 `Missing Authentication header` from openrouter.ai even on a raw urllib chat with an explicit Bearer header and 401 on `/api/v1/key` — the key is not an OpenRouter-recognized credential at all. Either point `TOKENROUTER_BASE_URL` at the real gateway this key belongs to, or blank the var so the router stops initializing the slot.
- **Router config:** chain for all three agents is `nvidia_nim → cerebras → openrouter` (`LLM_TRADER_ALLOW_PROVIDER_FAILOVER=true`). The journal logs only the primary provider, so failover hops are invisible in `llm_requests` (zero cerebras rows ever; session-4 log line "cerebras (1 keys)" came from provider *initialization*, not a call). This is why the 402 surfaced in console only.

**Net provider-pool state:** nvidia_nim healthy (5 keys), openrouter healthy (free tier, resets 00:00Z), cerebras blocked on billing (console action required), tokenrouter misconfigured (dead key). Effective pool = 2 of 4. The session-4 "all three down" event required an unlucky triple: NVIDIA transient 403 + Cerebras standing 402 + OpenRouter daily quota at its 00:00Z floor.

### Quota pause implemented (2026-09-30, post-investigation)

`data/llm_router.py` now parks a provider until its 429 quota reset instead of burning sleep-retries every decision: `parse_rate_limit_reset()` reads `Retry-After`, `X-RateLimit-Reset` (epoch-ms/s/relative) or an ISO stamp from the SDK-attached response headers (error-text fallback); `_pause_until_reset()` clamps the pause (margin 30s, max 24h, resets <60s use the old backoff path); `_retry_with_backoff` returns immediately while paused; `LLMRouter.generate` skips paused providers before any network attempt (a paused primary with failover off goes straight to deterministic HOLD); `get_status()` exposes `PAUSED_QUOTA` + `quota_paused_until`; a success clears the pause early. Env: `LLM_ROUTER_QUOTA_PAUSE_{MARGIN,MAX,MIN}`. Replay of session 4: OpenRouter would have been paused on first 429 until 00:00Z — zero wasted cycles, HOLD reason self-explanatory. Tests: `tests/test_llm_router_quota_pause.py` (15); runbook §2.2c.

### Memory-batch ERROR root cause (2026-09-30, PU-0149)

**The recurring `memory batch contained failed items` journal ERROR was never a model-format problem.** Row anatomy (5 rows, all `strategy=memory`, `parse_status=FAILURE`, response 1.8–2.9K chars): `process_outbox` (local_llm_memory.py) treats the router reply as a model response; during the outage window the router's fail-closed contract returns the **deterministic HOLD JSON string**, which `json.loads` happily parses and validation then rejects (`"memory response missing required fields"`). PU-0149 replayed the exact production prompt through the production router and captured the HOLD reply verbatim — shape mismatch confirmed. The three outbox items (source_ids 37–39, macro `event_reaction` EURUSD records) re-failed on **five consecutive boots** (15 identical `distillation_failed` events; `distilled_locally` has never fired) because failed items stay queued: each boot burned 3 real provider calls re-failing a poisoned queue. The router-string contract is intentional (decision path depends on it); the memory consumer was the missing classification layer.

**Fix (3 files):** (1) `local_llm_memory.py` — a reply starting with `Error:` or carrying `"deterministic_fallback": true` is classified as a provider **outage**, not a parse failure: journal error becomes `"provider outage: deterministic HOLD received instead of a distillation reply"`, the string is NOT hashed/counted as model output (`response_chars` stays honest), items stay queued, and parse_status can now be `PROVIDER_OUTAGE` (all-outage) vs `FAILURE`/`PARTIAL_FAILURE` (genuine parse problems). At retry exhaustion (row parked FAILED by `complete_memory`) the module's documented-but-never-wired `_fallback` deterministic narrative is finally saved (`distillation_fallback_used` event, CANDIDATE memory row) instead of parking a dead row forever. (2) `agent_operations.complete_memory` returns the row's new status (SENT/PENDING/FAILED) so the caller can distinguish retry from parking without re-querying. (3) `llm_orchestrator.process_pending_memory` maps outcomes truthfully: outage-only batches log `"memory batch blocked by provider outage; items left queued for retry"`, exhaustion logs the fallback-save, and `success` now means no failures of any class.

**Disposition of the 3 stuck rows:** left untouched (live DB stays read-only without explicit instruction). With the fix they self-resolve: next healthy-provider boot distills them for real; continued outages count toward the attempts=8 parking bound, at which point fallback narratives preserve auditable summaries. Tests: `tests/test_memory_outage_handling.py` (6) + governance regressions; blast-radius battery 109/109 green.

### Session 5 analysis (PU-0150): verdict + the stale-circuit discovery (2026-09-30)

The waiter fired (run completed 07:32Z, all 8 cycles, graceful shutdown). **Memory outcomes: the poison-queue fix works** — `distilled_locally` fired for the first time ever (13 events), outbox ended 7 PENDING/13 SENT (items 37–39 gone: self-resolved), the 07:11Z deterministic-HOLD was truthfully classified `"provider outage: deterministic HOLD received instead of a distillation reply"` (the new outage classifier), and per-item failures now carry real errors instead of a blanket batch error. **Trader ERROR rate: 0/0 — but meaningless as a measurement**: zero trader LLM rows because the trader never reached the network. Two causes, in priority order: (1) the **Tier-1 news gate** legitimately blocked M15 cycles 1–5 (German Prelim CPI: 120-min warning then 60-min blackout — the gate working as designed); (2) **the persistent provider circuit breaker (`data/provider_circuit.py`) has been at tier 4 / MANUAL_RESET_REQUIRED since 2026-09-28 11:18Z**, `last_error: "name 'logger' is not defined"` — the logger NameError bug that the current uncommitted orchestrator code already fixes (module head comment). The circuit's design is manual-reset-only from tier 4, so **every trader decision since Sept 28 has been deterministic-only by policy**; cycles 6–8 quoted it verbatim once the news gate stopped shadowing it. Providers were NOT the blocker for the trader this session: NVIDIA 403 flapped all session (transient, per the provider investigation) and hit only the memory batches, which failed over to OpenRouter 200s and Cerebras-402 as before. Side findings: R2's ≤96K cap untestable this session (no trader prompts; PU-0109 e2e still stands); 12 `schedule_handler_overrun` events from ~15-min cycle work spilling; `Fetched 0 news articles` + `Fetched 18 FRED series` show the new degradation paths in live operation. **Action taken: circuit reset** (2026-09-30T08:21:18Z, by buffy-codebuff, reason journaled) — breaker now tier 1 / AVAILABLE; the next bounded session can finally produce a real trader measurement.

### Boot-watchdog trips in the tuning report (2026-09-30)

Per-stage `boot_fetch_stage_timeout` telemetry now lands in the post-session scheduler tuning report (`scripts/scheduler_tuning_report.py`): `_aggregate_boot_stage_timeouts` folds the watchdog events into per-stage trips/budget/p95-elapsed/first-last stamps plus a suggested budget (p95×1.5, same raise heuristic as handler overruns, no interval/2 cap — boot stages have no interval), printed as its own duty-report section and written to the artifact under `boot_stage_timeouts`; `has_signal` includes it so the report no longer claims "no new-telemetry events" when only watchdog trips are present. Validated live against the real journal: **session 5 produced 4 `data_source_pipeline` trips** (45s budget, elapsed p95 45.26s, suggested 67.9s) at exactly the memory-batch ERROR timestamps (05:26/05:40/05:58/07:10Z) — the hung FRED/NewsAPI pipeline was abandoned by the watchdog on half of session 5's pulses, and that evidence is now in the duty comparison instead of buried in the journal. Tests: `TestBootStageTimeouts` in `tests/test_scheduler_tuning_report.py` (aggregator fold + degenerate payloads, main() section + artifact key, placeholder when empty); suite 17/17.

### Scheduled-agent fetch bounding: deadline pattern outside the boot path (2026-09-30)

Follow-up: the boot-path bounds (§2.2c–e) left scheduled-agent/cycle call sites that reach providers directly. Audit of every `APIManager`/provider consumer outside boot found most inherit the bounds via APIManager methods (`market_data_provider`, `realtime_news_ingest`, `ui/scanner`), `get_calendar` is a dead AttributeError fallback (no network), and `is_holiday` is pure date math — three real gaps: (1) **`query_seeking_alpha_news`** (`core/trading_tools.py`): two unbounded live calls (`get_news_articles`, `get_quant_ratings`) — now bounded via new shared helper `run_bounded.py` (`SEEKING_ALPHA_BUDGET_S`, 15s; `FRED_BUDGET_S` 20s for the FRED tool), timeout → honest `OFFLINE_PROVIDER_UNAVAILABLE` + datasheet `SKIP` marker so the scorecard trend sees scheduled-path degradations (marker helper `_bounded_tool_call` records and re-raises the contained error into the tool's existing except-path); (2) **`LiveIndicatorFetcher._fetch_fred_series`** (`core/news_indicator_fetcher.py`): called the raw `fredapi` client, bypassing every bound — now bounded, hung series → honest MISSING snapshot. `run_bounded.py` sits at repo root per the `rate_limiter.py`/`http_retry.py` convention; APIManager/watchdog keep their earlier local implementations (documented). Test-suite lesson: the first run had two spuriously-green hang tests via the ImportError path before `run_bounded.py` existed — fixed by actually creating the module, then 17/17 + 91/91 regression green (trading tools, news-indicator wiring, degradation, datasheet, scorecard, watchdog, ingestion).

### Provider scorecard: per-boot source verdicts + degradation trend (2026-09-30)

Companion to the boot datasheet: `intelligence/provider_scorecard.py` turns the datasheet's fresh/degraded markers into one `provider_scorecard` journal event per boot, answering "which sources served fresh, stale, or no data — and is it getting worse?". Verdict derivation: degraded-wins-over-fresh, worst-outcome-wins among degradations (ABORT not masked by SKIP), a zero-count success note (`0 articles`) is NONE not FRESH, and `KNOWN_API_SOURCES` never called this boot surface as UNSEEN so a silently-dead source stays visible. Trend: `load_recent_summaries` reads the previous boot's scorecard summary from `operational_events` (indexed `(event_type, created_at)`, best-effort — a closed/locked journal degrades to trend-unavailable), `trend_vs_previous` reports per-source WORSE/BETTER/SAME/NEW/GONE plus an overall `regressed` flag; severity WARNING iff regressed. Emitted by the orchestrator exactly once per boot, at the datasheet's single render moment (`_emit_provider_scorecard`, fully best-effort), with a compact fixed-width log block after the datasheet table. Tests: `tests/test_provider_scorecard.py` (15: verdict rules, trend movement/regression, journal round-trip through a real AgentOperations DB, severity rules, render, orchestrator one-shot + failure containment); batteries 54+29 green.

### Boot datasheet: one table merging boot health (2026-09-30)

Follow-up to the fetcher-degradation fix: bootstrap steps, watchdog outcomes, and provider degradations each lived in different logs (APIManager fail-opens were WARNING-only, invisible in any summary). New `intelligence/boot_datasheet.py` is a process-lifetime collector all three layers record into: bootstrap records per-step rows at PREPARE completion (`record_prepare_rows` — status from `details`, elapsed from `step_elapsed`, bounded-timeout steps get honest TIMEOUT status + reason matched from `warnings`); the orchestrator folds `boot_stage_outcomes` per pre-pulse stage and renders **exactly once** at the first pulse (`maybe_render_once`), journaling `boot_datasheet` (INFO, table + summary: counts, slowest step, degraded sources); APIManager records degradation markers at every fail-open point (STALE/EMPTY/SKIP/ABORT, capped at 24 rows so churning sessions can't grow it unboundedly). Bootstrap `run()` resets the collector so the sheet is per-boot. While wiring, the last two unbounded bootstrap fetches were bounded: **2b macro seed** (the 94.5s session-5 offender — now under `_boot_stage` like its siblings) and **2c6 EIA**. Render is fixed-width ≤80 chars and idempotent; `LLM_TRADER_BOOT_DATASHEET=0` opts out. Tests: `tests/test_boot_datasheet.py` (11: collector render/summary/cap/one-shot/opt-out, bootstrap warning-matching + TIMEOUT status, orchestrator single-render fold, e2e hung-FRED marker landing in the table); regression batteries 45+45 green (incl. one stale-assertion fix in the watchdog suite: the timeout-event assertion now scans all journaled events since the datasheet event fires after it).

### Fetcher degradation: tonight's FRED/news failures made graceful (2026-09-30, session-5 boot)

Session 5 (booted 05:07:14Z at 1.57GB, PID 28896, waiter armed) exposed the second unbounded path: the bootstrap **PREPARE** phase. Timeline: 2b macro-graph seed (which runs `process_cycle_data_sources` → FRED/NewsAPI) took **94.5s silently**, the Global Macro Bridge 41.5s, alternative-data provider 38.2s — yet `PREPARE Complete: duration=193.3s warnings=0`. Mechanism: the `newsapi`/`fredapi` clients issue requests with **no effective timeout**, `APIManager` guarded exceptions only (so a hang slipped every guard), and bootstrap steps wrapped everything in try/except that converts slow failure into invisible success. `APIManager.get_news` also discarded cached headlines on failure — maximizing information loss.

**Fix, two layers.** (1) `data/api_manager.py`: every `newsapi`/`fred` network call now runs under `_run_bounded` (daemon-thread wait-bound; `NEWSAPI_BUDGET_S`=15s, `FRED_BUDGET_S`=20s). On timeout/failure, `get_news`/`get_hot_topics` serve the **last good cached** payload and log a WARNING (a stale headline beats a hang and beats a silent empty list); `get_fred_data` abandons the hung series and continues the batch, preserving the existing network-abort cache merge. (2) `core/autonomous_bootstrap.py`: the seven network-heavy PREPARE steps (2b, 2c2, 2c3, 2c4, 2c5, 2c7, 2c8) run under `_boot_stage` deadlines (`BOOT_STAGE_BUDGET_S`, default 30s) via the existing watchdog — a timeout becomes the step's ordinary SKIPPED degradation with a named WARNING — and the `PREPARE Complete` log line now discloses **per-step elapsed seconds**, so a 94-second silent step can never hide again. `MacroGraph` uses per-call connections, so bounded workers are cross-thread safe. Tests: `tests/test_fetch_degradation.py` (11: bounded helper, hung-call abandonment <3s, stale-cache fail-open, FRED batch continuation, bootstrap timeout/error/success markers); regression battery 61/61 green.

### Boot fetch watchdog (2026-09-30)

`pre_pulse_macro_ingest` hoists calendar/FRED/NewsAPI/realtime-news ingestion once per pulse before the first cycle; every stage was exception-guarded but **hang-unbounded** — the same silent 20+ minute pre-first-cycle stall class as session 2's CPU Laya triage, from a wedged provider call with no effective timeout. New `intelligence/boot_fetch_watchdog.py` runs each stage under a per-source deadline on a daemon thread and abandons it at the budget (fail-open): the pulse proceeds on degraded data and emits a `boot_fetch_stage_timeout` WARNING event; abandoned threads are never killed (impossible in Python) — the budget bounds waiting, not cancellation. Budgets default 30s/30s/45s/45s per stage, overridable via `LLM_TRADER_BOOT_STAGE_BUDGETS="name=seconds,..."` (invalid entries logged and dropped). Stage outcomes (`ok`/`timed_out`/`elapsed_s`/`budget_s`) are folded into `_pulse_macro_cache[symbol]["boot_stage_outcomes"]` so boots are observable. Wiring preserves every old cache field and debug-log path; healthy boots are byte-identical apart from the new outcomes block. Tests: `tests/test_boot_fetch_watchdog.py` (9: budget resolution incl. garbage-tolerance, success/exception/timeout outcomes, emitter-failure swallow, orchestrator fail-open with a hung calendar bridge (<3s with 0.5s budget, degraded cache entry, journal WARNING), healthy path populating all four stages, no-data-provider path). Blast-radius battery 54/54 green; note: `tests/test_pair_selection_context.py` failures (`AttributeError: _cycle_state_lock`) are pre-existing from another agent's uncommitted orchestrator work — `_cycle_state_lock` exists in no commit and is outside this change's hunks.

### Session 5 armed: decisive post-fix measurement (2026-09-30 04:35Z)

Quota reset confirmed (02:45Z probe: OpenRouter 200). Direct boot refused by doctrine: free RAM oscillating 0.30–1.34GB (other agents' pytest/stdin processes pinning the box; crash precedent at 1.04GB). Session-proven guarded-boot chassis re-armed as `artifacts/pu_session5_boot.py` (PID 27576): single-shot spawn when free RAM ≥1.5GB **and** PU-0139 probe reports a fresh M15 candle (one exists: completed 04:00 vs newest analyzed 22:30). Spawns the bounded command directly — `llm-trade --paper EURUSD --model mistralai/mistral-7b-instruct-v0.2 --risk-per-trade 1.0 --loops 8 --no-dashboard` with `LLM_TRADER_COMPACT_CONTEXT=1`, `NEWS_TRIAGE_MODE=off` — detached, logging to `artifacts/pu_session5_boot.log`, boot stamp to `artifacts/pu_session5_meta.json`. Paired waiter (PID 11880) detects boot, waits out a 5-min grace, fires on process exit (2-strike), then auto-runs the PU-0150 report → `artifacts/pu_session5_report.txt`: trader ERROR rate by strategy vs the 57.9% baseline, prompt-size join vs 129,820/287,319 avg/max, ≤96K enforcement check, memory-strategy outcome (expect `PROVIDER_OUTAGE`-classified rows, not `FAILURE`+phantom errors), outbox self-resolution, `distillation_fallback_used`/`distilled_locally` events. Windows: 10h boot, 3h run cap; report always runs with whatever window exists.

### Paper fast-boot default for NEWS_TRIAGE_MODE (2026-09-30)

Session 2's 22-minute stall was auto-mode CPU Laya triage masquerading as a hang; `triage_mode()` defaults unset → `auto`, and `_get_laya()` in auto mode attempts the model load whenever free RAM ≥ 1.8GB — so a paper boot could still silently spend ~25 minutes in CPU triage. `mt5_trader.py` now applies a paper-only env default via `apply_paper_triage_default()`: right after arg parsing, `--paper` runs with `NEWS_TRIAGE_MODE=off` (deterministic rule triage, ~100s boot) unless the variable is explicitly set — shell, `.env` (loaded at import), or a launcher passthrough (`START_DECK.bat TRIAGE_MODE=full` → sets `auto` in the child env, which wins; the neither-branch previously cleared the var and silently reverted to auto — now it lands on `off`). Demo/live paths keep the old auto behavior; the engine's `ENGINE_CONFIG` boot event self-reports the effective `triage_mode()`, so the deck shows the real mode. Tests: `tests/test_paper_triage_default.py` (5: default applies + visible to `triage_mode()`, explicit laya/auto/off respected, whitespace treated as unset); triage/mt5_trader-import regression battery 47/47 green.
