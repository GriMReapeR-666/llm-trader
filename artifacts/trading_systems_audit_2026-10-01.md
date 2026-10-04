# Trading Systems Audit — Types, Health & Strategy Maintenance
*2026-10-01 · branch `feat/ui-pipeline-compact-tabs` @ `50456f4` · read-only inspection + one surgical fix*

## 1. What happened first: the session-6 decisive run completed

While the previous session was interrupted, the re-armed chassis fired on its own:

- **Boot:** 2026-09-30 20:37:16Z (watcher PREFLIGHT PASS: 1.74GB free ≥ 1.5GB bar, fresh M15 candle)
- **Run:** 8/8 cycles, graceful shutdown 23:03Z, checkpoint 13951, scheduler report archived
- **Report:** waiter emitted `artifacts/pu_session6_report.txt` at 23:06Z, exit 0, `PU-0150-OK` marker

### Session-6 results vs. session-5 baseline

| Metric | Session 5 (baseline) | Session 6 | Verdict |
|---|---|---|---|
| Prompt size | avg 129,820 / max 287,319 chars | avg 46,400 / max 48,991 | ✅ **FIXED** (−64% avg, 0 over 96K) |
| Trader requests | n=0 in window | n=8 (5 scalp, 3 swing) | ✅ decision loop engaged |
| Trader error rate | 57.9% (22/38, 7-day) | **100% (8/8)** | ❌ worse — but cause is external |
| Error mode | parse failures | `DETERMINISTIC_FALLBACK` | ✅ *correct* safety behavior |
| Memory requests | 8/8 PARTIAL_FAILURE | 1 FAILURE + 2 SUCCESS w/ PROVIDER_OUTAGE tag | ⚠️ mislabel investigation |
| Outbox PENDING | 7 | 7 (unchanged, ~15h) | ❌ not self-resolving |
| Operational events | 11 distillation_failed, 12 overruns | 9 distillation_failed, 12 overruns | ⚠️ chronic |

**Root cause of the 100% "error" rate: all three LLM providers were down simultaneously.**
Raw trace (22:28:09Z) shows the router's fallback payload listing:
- `nvidia_nim`: HTTP **403 Forbidden — Authorization failed** (intermittent — 200 OK between failures, so key rotation among its 5 keys; ~1/3 of key attempts dead)
- `cerebras`: HTTP **402 Payment Required** — billing/quota exhausted, hard block
- `openrouter`: HTTP 429 → quota-paused until 2026-10-01T00:00:30Z (the `LLM_ROUTER_QUOTA_PAUSE_*` knobs shipped earlier worked exactly as designed)

Every "parse failure" was actually the router's deterministic safety-HOLD payload. The system **refused to trade under total provider loss** — the discipline worked. Zero positions, zero risk taken.

**Observability win:** the provider scorecard wired in commit `7455773` fired in production on first render: `fred.get_series UNSEEN, newsapi.get_hot_topics UNSEEN, newsapi.get_news SKIP` — the boot-time data-provider visibility shipped this session works.

## 2. Trading types: inventory & status

### MT5 CLI modes (mt5_trader.py, ~28 subcommands)
| Mode | Status |
|---|---|
| `account-info`, `positions`, `orders`, `broker-preflight`, `diagnose`, `test-live` | ✅ working (verified live: authenticated, balance 100000, 0 positions) |
| `history` | ✅ **fixed this session** (see §5) |
| `data`, `pending-order`, `modify-order`, `cancel-order`, `benchmark` | ✅ present at HEAD (not live-fired; no positions to manage) |
| `market-order` | ✅ intentionally disabled at HEAD (governance: autonomous path only) |
| `kill-switch` | ✅ `active: false` (verified live) |
| `provider-circuit` | ✅ tier 2, mode AVAILABLE (verified live) |
| `llm-trade` | ⚠️ works mechanically (8/8 cycles), blocked by provider capacity |
| `backtest` / certification pipeline | ❌ **every run FAILS** (see §3) |
| `journal`, `memory-core`, `memory-review`, `demo-run`, `web-research`, `benchmark-llm`, `doctor`, `monitor`, `nexus`, `deck`, `settings`, `accounts` | present; not in scope of this pass |

### Quant workflow engine (7 workflows, all mounted, 2 proposals in 122ms sweep — engine healthy)
1. `statistical_arbitrage` — cointegration gate → Kalman z-score → state-machine signal → deadband → risk arbitration
2. `mean_reversion` — fractional differentiation → BOCPD regime gate → Clayton copula tail-risk gate → extremes-fading
3. `macro_causal_momentum` — causal DAG traversal → macro barometers → lead-lag divergence
4. `microstructure_breakout` — Hurst persistence gate
5. `neural_kronos_foundation` — Kronos foundation model
6. `macro_event_alpha` — event-driven
7. `retail_quant_models` — bag of retail-style quant models

**Verdict: mechanically all trading types are alive and governed.** The blockers are capacity/data, not code.

## 3. Strategy performance evidence — the honest picture

**There is no clean per-strategy performance record to rank.** Evidence assembled from three layers:

1. **Broker truth (MT5 history, last 30 days): 0 orders, 0 deals.** No strategy has produced a broker-reconciled trade. Strategy performance on live/paper is currently **undefined, not bad** — the system has been in safety-HOLD/refusing-to-trade states.
2. **Certification pipeline (`artifacts/backtest_results/pipeline_results.json`): 100/100 runs FAIL** across 13 strategy IDs (kronos_h1, statarb_eur_chf, macro_nfp_pullback, meanrev_bollinger, alpha_055_orderflow, alpha_098_copula, agent_execution_rl, agent_regime_supervisor, model_bocpd_changepoint, model_kalman_filter, gold_momentum_h4, spx_volatility_skew, sprt_hypothesis_gate). Every failure is the same integrity guard: `live OHLC unavailable for EURUSD/H1; refusing synthetic replay bars` (research/continuous_backtest_pipeline.py:193). Downstream DSR, CPCV (needs ≥40 trades; has 0), walk-forward (needs ≥20 broker-reconciled r-multiples; has 0), and live-comparison stages all correctly skip — refusing to certify on no evidence. The guardrails are working; the data plumbing is not.
3. **Learned patterns: 0 rows** in the patterns table — the memory loop has nothing confirmed worth storing (consistent with every session ending in HOLD).
4. **LLM decision loop:** the only strategies generating requests (`scalp`, `swing`) are 0-for-8, all provider-capacity failures, not strategy logic failures.

**Maintenance ranking (highest need first):**
1. **LLM provider capacity** — nvidia_nim 403s (rotate/remove dead keys), cerebras 402 (billing), openrouter quota (429). This is *the* blocker for every LLM-driven trading type.
2. **Backtest OHLC plumbing** — `live OHLC unavailable for EURUSD/H1` fails all 100 certification runs; without it no strategy can ever pass DSR/CPCV/walk-forward certification.
3. **memory outbox PENDING=7 stuck ~15h** — memory-loop batch failures never drain (`memory batch contained failed items; left queued for retry`); also check `SUCCESS`+`PROVIDER_OUTAGE` labeling (2 of 3 memory requests).
4. **Chronic ops noise** — `distillation_failed` 9/window, `schedule_handler_overrun` 12/window persist across sessions.
5. **Zero-trade cold start** — every evidence gate (CPCV ≥40, walk-forward ≥20) needs trades that the current all-HOLD regime can never produce; consider a supervised paper-trading window with relaxed-but-logged gating.

## 4. What works (worth stating)
- Prompt-size fix (COMPACT_CONTEXT) — avg −64%, max −83%, zero >96K. The structural goal of sessions 5–6 **achieved**.
- Deterministic safety-HOLD under total provider failure — 8/8 correct refusals, zero bad trades.
- Watcher/waiter chassis end-to-end — preflight gates, single-shot boot, bounded report, END-MARKER.
- Provider scorecard at boot (this session's `7455773` wiring) — fired in production.
- Quant workflow sweep engine — mounts 7 workflows, 2 proposals in 122ms.
- MT5 read-only surface + kill-switch + provider-circuit CLI — all live-verified.
- Circuit breaker — correctly logged the provider-loss failures, cooldown expired, AVAILABLE.

## 5. Fix applied this session (one, surgical)
`mt5_trader.py history` crashed with `UnboundLocalError: cannot access local variable 'pd'`. Root cause: the foreign workstream's startup rewrite added a function-scope `import pandas as pd` (line ~4225) inside `main()`, which shadows pandas for the entire function — breaking the earlier HEAD-owned `history` handler. **Fix: local `import pandas as pd` inside the history handler** (HEAD-owned region only; no foreign lines touched). `py_compile` green; command verified live: `orders_count 0, deals_count 0`.

## 6. Recommended next actions (in order)
1. **Providers:** remove/rotate the dead nvidia_nim keys (403s), clear the cerebras billing block, let openrouter's pause expire (00:00Z) — then a short rerun will convert the 100% "error" rate into real decisions.
2. **Backtest data:** fix EURUSD/H1 live-OHLC availability (data source/permissions/window) so the certification pipeline can actually run — this unlocks all 7 quant workflows' certification.
3. **Outbox drain:** investigate the 7 stuck PENDING memory items and the `SUCCESS`+`PROVIDER_OUTAGE` mislabel.
4. **Cold-start policy:** decide whether certification thresholds (≥40 CPCV / ≥20 walk-forward samples) need a supervised accumulation path, or trading stays frozen pending provider recovery.
5. **Ops noise:** bound `schedule_handler_overrun` (12/window) and `distillation_failed` (9/window).
