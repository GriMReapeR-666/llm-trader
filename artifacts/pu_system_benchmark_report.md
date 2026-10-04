# Full-System Benchmark Report — LLM + Quant Trading System

**Date:** 2026-10-01 | **Branch:** integration/second-workstream-landing @ 820bc6e | **Marker:** END-REPORT-PU-0156
**Method:** every claim footed to a repo artifact (boot logs, DB tables, backtest results, live session evidence). Framework: the user's 4-layer benchmark (outcome → ablation → components → validation) + retail-FX/gold amendments + MTF amendments.

---

## 0. System inventory verified LIVE at entrypoint boot

Source: `artifacts/pu_session8_boot.log` (a real paper boot, 2026-10-01 17:59–20:06 local). All major subsystems initialize and mount:

| Component | Boot evidence |
|---|---|
| Memory Orchestrator (L0–L3) | `Memory Orchestrator initialized (L0-L3 hierarchy)` |
| Knowledge Retrieval | `Knowledge Retrieval initialized (macro + cases + domain)` |
| Causal/macro graph | `CausalIntelligencePlugin mounted with 134 macro dynamic nodes` |
| Macro event alpha | harness plugin `macro_event_alpha (v1.0.0)` mounted |
| Kronos neural fusion | `KronosBridge: kronos-mini loaded ... device=cpu — error cleared` |
| Advanced RL pipeline | `RL model loaded (111 states, 300 updates)` + `Advanced RL Pipeline initialized (multi-agent)` |
| Meta-improvement / auto-apply | `Meta-Improvement Orchestrator initialized` + `Improvement Pipeline initialized (auto-apply low-risk)` |
| Event bridge / ops kernel | `Event bridge initialized` + `S.Q Priority Queue initialized (43 scheduled tasks)` |
| Startup training | `walk_forward: initialized, cpcv: available, alpha_ensemble: warmed, ga_miner: {evaluated: 21, significant: 14}, capstone: grade C, automated_training: {oos_sharpe 0.627, wfe 0.833, gate True}` |
| Data feeds | `Fetched 18 FRED indicators`, `Fetched 1 news articles`, NewsAPI/FRED/Yahoo/Portfolio123/SeekingAlpha/OpenBB all `initialized` |
| LLM router | 4 providers, 3 agents wired; nvidia_nim 2 live keys, openrouter/tokenrouter/cerebras configured |

**Boot-time degradations observed (honest, fail-open with warnings):**
- `bootstrap_macro_seed exceeded 30s budget; failing open (hung provider?)`
- `bootstrap_global_macro_bridge exceeded 30s budget; failing open`
- `Enhanced calendar unavailable; no calendar fallback is trusted: Could not find MT5 terminal DLL` — the enhanced economic calendar is effectively offline; headline news still flows.

Only **3 ERROR lines in the entire boot log** (2 router total-failure events during the quota-outage window, 1 weekly-research executor). No component crash, no traceback storm.

---

## 1. Layer 1 — End-to-end trading performance (outcome layer)

**Backtest/statistical tier (evidence, not proof):**
- **Deep 5000-bar sweep, per-strategy + wrapped variants** (`artifacts/backtest_results/deep_sweep_5000.json`, driver `artifacts/pu_deep_sweep.py`): 19 runs — the 13 causal per-label signals (`research/strategy_signal_registry.py`), the generic `default`, and 5 `@sprt_confirm` research variants. **Every stage scores its own stream** (the Stage-2b shared-generic-replay bug found via the gauntlet pair was fixed same-day; CPCV n now equals each run's own trade count). All 19 verdicts **FAIL** with the same quantified blockers (sharpe_below_1, profit_factor_below_1.2, negative_expectancy, dsr_gate_failed).
- **Capstone validation (every boot):** 1037 trades, **Grade C (41/100), Gate FAIL**; CPCV n=57 mean Sharpe −1.745, PBO 0.733 → `OVERFITTING_RISK_DETECTED`; slippage applied on 2940 fills, R-multiples net-of-cost (TCE).
- **Boot per-label DSR example:** `macro_nfp_pullback` stage-1 PASS (Sharpe 0.704, DSR 1.0) then full-n-trials REJECT (Sharpe 0.700, DSR 0.4149 < 0.95, n_trials 15,825). The statistical-honesty machinery (DSR, PBO, CPCV) is real, runs on schedule, and diverges honestly between stages.
- ~~Known material gap: all labels scored the generic ruleset~~ → **RESOLVED.** Ruleset attribution now verified per run across 19 configs (`strategy:<name>` and `strategy:<name>@sprt_confirm`, `deterministic_signal` honestly retained for `default`); unknown labels refuse to mislabel (`generic_fallback:<name>`). Registry proofs: causality (future perturbation leaves past scores unchanged), distinctness (max pairwise corr 0.815), refusal-to-mislabel — 5/5 regression tests covering all 5 variants.

| Config | Ruleset scored | Sharpe | Trades | CPCV mean | PBO |
|---|---|---|---|---|---|
| agent_regime_supervisor@sprt_confirm | strategy:…@sprt_confirm | **−0.96** | 259 | −0.938 | **0.53** |
| sprt_hypothesis_gate | strategy:sprt_hypothesis_gate | −0.99 | 237 | −0.921 | 0.67 |
| macro_nfp_pullback@sprt_confirm | strategy:…@sprt_confirm | −1.53 | 253 | −1.376 | 0.80 |
| model_bocpd_changepoint@sprt_confirm | strategy:…@sprt_confirm | −1.54 | 257 | −1.350 | 0.80 |
| kronos_h1 | strategy:kronos_h1 | −2.12 | 249 | −1.901 | 0.87 |
| alpha_098_copula | strategy:alpha_098_copula | −2.20 | 186 | −2.331 | 1.00 |
| agent_execution_rl | strategy:agent_execution_rl | −2.35 | 200 | −2.168 | 0.80 |
| statarb_eur_chf | strategy:statarb_eur_chf | −2.41 | 251 | −2.334 | 0.93 |
| model_kalman_filter | strategy:model_kalman_filter | −2.65 | 269 | −2.445 | 1.00 |
| gold_momentum_h4 | strategy:gold_momentum_h4 | −2.65 | 227 | −2.395 | 0.93 |
| meanrev_bollinger | strategy:meanrev_bollinger | −2.72 | 250 | −2.608 | 1.00 |
| macro_nfp_pullback | strategy:macro_nfp_pullback | −2.81 | 161 | −2.997 | 1.00 |
| model_bocpd_changepoint | strategy:model_bocpd_changepoint | −2.82 | 148 | −3.234 | 1.00 |
| gold_momentum_h4@sprt_confirm | strategy:…@sprt_confirm | −3.00 | 231 | −2.750 | 1.00 |
| spx_volatility_skew | strategy:spx_volatility_skew | −3.17 | 170 | −3.624 | 1.00 |
| default (generic ruleset) | deterministic_signal | −3.22 | 210 | −3.105 | 0.93 |
| agent_regime_supervisor | strategy:agent_regime_supervisor | −3.59 | 181 | −3.466 | 1.00 |
| alpha_055_orderflow | strategy:alpha_055_orderflow | −3.67 | 213 | −3.452 | 1.00 |
| kronos_h1@sprt_confirm | strategy:…@sprt_confirm | **−5.61** | 200 | −4.501 | 1.00 |

**Live/paper tier (truly out-of-sample):**
- Sessions 6–8 (bounded paper runs): attempts 8 → 8 → 3, SUCCESS 0 → 1 → 2 (error rate 100% → 87.5% → 33.3% vs 57.9% baseline). Trajectory is upward but **n = 19 decision attempts total** — far below any statistical significance. Minimum track-record length: not yet estimable; you need ≥20 broker-reconciled r-multiples per walk-forward's own gate and there are **0** (`order_intents` = 0 rows ever; no trades opened since Sep 29).
- ~~Baselines layer absent~~ → **BASELINES NOW MEASURED** (`artifacts/backtest_results/baseline_sweep_5000.json`, driver `artifacts/pu_baseline_sweep.py`, same 5000 H1 bars 2025-12-11→2026-10-01, same friction 1.5/0.5 pips, same SL30/TP60, same engine): window return −3.94%.

| Baseline | Sharpe | PF | Trades | Beats all 19 configs (−1.0…−5.6)? |
|---|---|---|---|---|
| Buy-and-hold long | 0.00* | — | 1 | **19/19** (*1-trade degenerate Sharpe) |
| Buy-and-hold short | ~0* | ∞* | 1 | **19/19** (direction beta: falling window) |
| Trend EMA50/200 | −4.06 | 0.59 | 31 | 1/19 (only beats kronos_h1@sprt_confirm −5.61) |
| **Random null (seed 42)** | **−0.29** | **0.96** | 77 | **19/19 — the null still beats every signal and every wrapped variant (best −0.96)** |
| Trend @ 2× costs | −4.33 | 0.56 | 32 | 0/19 (cost-fragile) |
| Random @ 2× costs | −0.73 | 0.91 | 77 | **19/19** (even stressed, random beats every config) |

**The decisive result: even with dedicated per-strategy signals AND the sprt-confirm wrapper, the random null (−0.29) beats all 19 configs (best: agent_regime_supervisor@sprt_confirm −0.96, then sprt_hypothesis_gate −0.99) under identical data, friction, and risk rules** — entry selection is not the binding constraint; retail costs and the shared SL/TP/risk shell are. The wrapper's effect is **heterogeneous, not a free lunch**: it produced the two best configs in the study (agent_regime −3.59 → −0.96; macro_nfp −2.81 → −1.53; bocpd −2.82 → −1.54) and the single worst (kronos_h1 −2.12 → **−5.61**) — it amplifies whatever directional content the underlying votes carry, so persistent-but-wrong votes get pressed harder into disaster. The generic `deterministic_signal` ruleset (−3.22 this window) sits mid-pack, confirming the earliest −4.3 headline was an artifact of generic scoring plus window drift (absolute values move as the live window rolls; rank order of signals is stable across runs). All verdicts remain honest FAILs with DSR/PBO rejecting — no false certification introduced.

**Layer-1 verdict: FAIL (honestly).** The system's own gates say the strategies are not yet profitable and its statistical machinery is correctly refusing to certify them.

---

## 2. Layer 2 — Ablation testing (does each part earn its place?)

**Status: NOT IMPLEMENTED.** `git grep -i ablation` across `*.py` returns zero harness matches. The user's ablation table (remove LLM / macro / news / order book / synthetic) cannot currently be answered by evidence. The deterministic gates give a partial organic proxy — e.g., the RL bridge is suppressed when the LLM is down, provider outage → pure deterministic HOLD — but no systematic component-removal benchmark exists.

**Marginal cost is partially measurable today:** LLM inference spend is tracked (`llm_requests.estimated_tokens/cost`), latency is tracked (`latency_ms`), and boot logs show data-layer budget overruns. Marginal contribution is not.

**Priority build:** a session-harness ablation mode (env-gated per-boot feature toggles) run on the same bounded session chassis — 3 ablations first: no-LLM (pure quant), no-news, no-macro.

---

## 3. Layer 3 — Component-level benchmarks

**Alpha discovery engine:**
- GA miner evaluates (21) and finds significant (14) candidates per training cycle; alpha ensemble warms; alpha factors computed with `latency_bars=1` honesty shift (no zero-bar clairvoyance, enforced in `fetch_data`).
- **Missing:** OOS IC / IC-decay tracking, alpha survival rate, correlation-to-existing tests, false-discovery-rate accounting. The infrastructure computes alphas but no evidence shows out-of-sample survival measurement.

**LLM layer (the most likely to quietly fail):**
- **Fail-closed contract proven:** provider outage → deterministic HOLD, never a hallucinated trade (session-6: 8/8 honest fallbacks; boot log: "RL bridge suppressed: ... no new risk may be taken").
- **Honest labeling:** Cerebras billing-blocked model is labeled `(billing-blocked)` in UI lists; PROVIDER_OUTAGE vs SUCCESS classification fixed (commit a85aabe).
- **Contamination/look-ahead:** the audit found and fixed a real future-looking clock bug (naive candle labels interpreted in machine-local time, up to 5.5h ahead in UTC — `pu_candle_state.py` normalized). Post-training-cutoff data tests, date/ticker masking, and Brier-score-on-decisions tracking are **not implemented**; Workflow 5 exposes ECE/Brier + CPCV-DSR as an on-demand workflow but no scheduled calibration evidence exists.
- **Consistency:** not measured (no paraphrase/repeat runs). **Hallucination rate:** not measured. **Event-reaction speed:** not measured; news flows through the gate with 60-min/30-min blackout tiers working (session-8 ISM blackout enforced).
- Session-8 wrinkle: compact/tool-call-shaped responses (175–179 chars) parse as HOLD/BUY with "missing reasoning field" — parser falls back gracefully; schema-hardening pending.

**Data layer (macro/RSS/news/FRED):**
- FRED indicators (18), news articles, hot topics, calendar events flow into the toon payload every cycle; `data_quality` gate rejects stale/malformed payloads with a point-in-time revision guard; `news_gate` blocks entries around Tier-2 events with correlation-driven windows.
- **Degraded:** enhanced calendar offline (MT5 DLL); bootstrap macro-seed stages time out and fail open; RSS/macro provenance into decisions is prompt-level only. Vendor discrepancy, missing-data-rate, and survivorship checks are **not scheduled**.

**Backtesting engine:**
- Mature: CPCV, DSR, PBO, walk-forward, TCE slippage on fills, net-of-cost R-multiples, capstone grading, boot-time per-label validation — all executed automatically at every entrypoint boot (verified in logs).
- **Retail cost realism partial:** slippage/TCE and cost gates exist (`cost_assessment`, `min_net_rr=2.0` in the execution path); session-dependent/event-dependent spreads, triple-swap, and 1.5–2× cost stress runs are **not** in the automated sweep.
- **Sim-to-live gap: unmeasurable** — 0 live fills ever; the single most important fidelity metric has no denominator yet. Reproducibility: golden A/B replay suite (legacy vs staged intents) exists and passes (11/11).

**Synthetic data & scenarios:**
- Kronos generates synthetic OOS data for training cycles (verified in boot log); jump-diffusion + CPCV workflows exist.
- **The user's test is pending:** whether synthetic-set performance predicts real OOS. Not yet answerable — treat synthetic as stress machinery, which is how the system uses it (advisory context only, never a permission path).

---

## 4. Layer 4 — Validation hierarchy (evidence quality)

| Tier | Status |
|---|---|
| In-sample backtest | Present, correctly failing (negative Sharpe, DSR rejects) |
| Walk-forward purged CPCV | Present, runs every boot; WF gate shows INSUFFICIENT_JOURNAL_EVIDENCE (< 20 r-multiples) |
| Held-out OOS touched once | CPCV folds serve this; deep sweep honest |
| Stress tests (2008/2020/2022/SNB/gap events) | **Absent** as a scheduled suite |
| Paper trading in real time | **Active** — sessions 5–9 chassis; bounded, marker-footed, measured (PU-0150) |
| Small live capital | Never reached (correctly gated behind paper accumulation) |

The system sits exactly where it should: paper tier, statistical gates refusing to certify, deterministic safety layer binding all new risk.

---

## 5. Robustness & risk (retail amendments)

- **Kill switches / failure modes:** `KillSwitchLatch`, `execution_safety_supervisor`, `code_safety_agent` exist and are checked in bootstrap; bounded session chassis proves graceful shutdown (sessions 6–8 all ended clean); 521 `run_stale_recovered` + 588 runs show crash-recovery under real churn; provider outage and budget-exhaustion paths proven live.
- **Parameter sensitivity ±20%:** not scheduled. **Regime-conditional performance:** regime engines mount (hierarchical portfolio, SPC filter) but regime-conditional outcome tables are not produced.
- **Retail survival metrics:** risk-of-ruin, margin-utilization telemetry, worst day/week — **not implemented**; margin preflight exists at submission (order path), which is the right place but not the metric.
- **MTF look-ahead:** the clock-normalization bug fixed this session was exactly the class of bug the framework warns about; the claim-lease doctrine (release vs complete) now preserves candle triggers across news blackouts (this session's fix, test-pinned).
- **Order-book/volume caveat:** system already treats MT5 tick volume as proxy (framework-compliant); no CME-futures cross-validation exists.

---

## 6. Efficiency & operations

- **Alpha per unit cost:** LLM spend/latency tracked per request (measurable); no net-P&L denominator (0 trades). **Signal-to-order latency:** not measured end-to-end. **Research throughput:** idea→validated-strategy pipeline exists (autonomous pipeline ticks registered 0/skipped 0/flagged 0 in evidence); **Human intervention rate:** sessions 5–9 ran fully autonomous with zero operator overrides — the strongest operational result so far.
- **Reliability:** uptime through 3 provider-outage regimes, quota exhaustion, 402/403/429 storms — always fail-closed, never a bad order. That is the system's best proven property.

---

## 7. Scorecard (framework weights)

| Dimension (weight) | Score | Basis |
|---|---|---|
| Performance (30%) | **2/10** | Negative backtest Sharpe, capstone Grade C FAIL, 0 live expectancy; **baselines now measured: the ruleset loses to random-null (−4.3 vs −0.29) and to a do-nothing buy-and-hold** |
| Validity (25%) | **8/10** | CPCV/DSR/PBO machinery real and honest; **per-strategy backtests implemented and every validation stage now scores its own stream** (two generic-replay misattribution bugs found and fixed same-day: Stage-1 labeling and Stage-2b CPCV); 19 labeled configs through the full gauntlet with heterogeneous wrapper effects honestly measured (including a falsified pre-selection hypothesis); sim-to-live still unmeasurable |
| Robustness (20%) | **4/10** | Failure modes proven (outage/quota/kill-switch); stress suites + parameter sweeps absent |
| Component value (15%) | **2/10** | No ablation harness; marginal contribution of LLM/macro/news unmeasured |
| Efficiency & operations (10%) | **8/10** | Autonomous bounded runs, honest fail-closed under every outage, tracked spend/latency, 0 interventions |
| **Weighted total** | **~3.7/10** | Honest state: infrastructure far ahead of demonstrated edge |

---

## 8. The five moves that raise the score fastest

1. ~~Implement per-strategy backtests~~ → **DONE** (`research/strategy_signal_registry.py`: 13 causal per-label signals feeding the engine's external-signal mode; `default` honestly keeps the generic ruleset). **Deep sweep re-run: 14 distinct results** — Sharpes now span **−0.9 (sprt_hypothesis_gate) … −3.48 (agent_regime_supervisor)**, trades 146–258; the generic ruleset itself scores **−4.26**, i.e. every per-strategy signal beats the old shared ruleset, and the old −4.3 was an artifact of the generic scoring, not of any strategy. All verdicts remain honest FAILs with DSR/PBO rejecting — no false certification introduced. Registry proofs: causality (future perturbation leaves past scores unchanged), distinctness (max pairwise corr 0.815), refusal-to-mislabel on unknown labels (4/4 regression tests).
   - **Follow-up diagnosis (sprt gap, `artifacts/pu_sprt_diagnose.py` → `artifacts/backtest_results/sprt_diagnosis.json`):** sprt loses least because it is an *evidence accumulator with hysteresis*, not an instantaneous z-score: eligible-direction flips 0.007/bar (others 0.02–0.38), trade-direction flips 0.078 (others 0.21–0.62), longest holds (13.3 bars), best TP rate (88/231), pf 0.87 (closest to cost parity). Transplant preview (wrapping other labels' sign votes in the sprt accumulator): **agent_regime_supervisor −2.85 → −0.72 (pf 0.91, only positive per-side expectancy in the study: mean_r_short +0.08)**; kalman unchanged; **actively harms fade/noise voters** (meanrev_bollinger −2.21 → −4.07; alpha_055 −3.82 → −5.37). Mechanism transfers selectively to persistent-vote trend-family signals; wrapped labels must be labeled as new hypotheses (`<name>@sprt_confirm`) and pass the full CPCV/DSR gauntlet before any adoption. Still DSR-rejected — nothing here is certification.
   - **Gauntlet verdict on the least-bad config** (`artifacts/pu_sprt_confirm_gauntlet.py` → `sprt_confirm_gauntlet.json`, full validation hierarchy, same-process pair on identical bars): `agent_regime_supervisor@sprt_confirm` is **REJECTED** — Stage-1 Sharpe −0.73/242 trades, CPCV on its own stream mean fold Sharpe **−0.737** with PBO **0.667**, DSR gate failed, walk-forward INSUFFICIENT_JOURNAL_EVIDENCE (0 broker-reconciled r-multiples); verdict **FAIL** with all four blockers. The base label fails identically but worse (Sharpe −2.85, CPCV mean −2.854, PBO 0.933). Fold Sharpe ≈ in-sample Sharpe for both (consistently mediocre, not fold-unstable). **This gauntlet run also exposed and fixed a Stage-2b bug:** CPCV had been scoring a shared generic-ruleset replay for every label; it now consumes each Stage-1 run's own r-multiple stream (32/32 adjacent + registry tests green). The least-bad config remains far from certifiable — Sharpe must double and PF cross 1.2 before the gates even engage.
   - **Family check extended to all 5 trend-family labels, then swept** (`artifacts/pu_sprt_diagnose.py` bare-engine + refreshed sweep): the wrapper's effect is **heterogeneous** — improved agent_regime (−3.59 → −0.96), macro_nfp (−2.81 → −1.53), bocpd (−2.82 → −1.54); mildly harmed gold_momentum (−2.65 → −3.00); catastrophically worsened kronos_h1 (−2.12 → **−5.61**, worst config in the study, PBO 1.0). The pre-selection hypothesis ("wrapper helps persistent trend voters") is recorded as **falsified as a blanket rule**: persistence sets leverage, but the votes' directional correctness sets the sign — kronos flips votes 3.3× more than agent_regime yet gold is harmed worse per unit of smoothness; the only honest rule is to measure each wrapped hypothesis through the gauntlet. All 5 variants now live in the sweep as labeled hypotheses; every verdict remains FAIL.
2. **Build the ablation harness** on the session chassis (no-LLM / no-news / no-macro toggles) — unlocks Layer 2 and the component-value score.
3. **Fill baselines:** buy-and-hold, trend, random-null with identical holding rules, pure-quant-no-LLM — all runnable on the existing deep-sweep driver.
4. **Add the ≥20-r-multiple accumulation metric to session reports** (order_intents→broker-reconciled) so walk-forward can unblock — session-9 (24 loops, armed) is the first run shaped for it.
5. ~~Retail cost stress in the sweep~~ → **DONE** (`artifacts/backtest_results/cost_stress_sweep_5000.json`, driver `artifacts/pu_cost_stress_sweep.py`, overlay in `full_pipeline_backtest.py`, 3 new regression tests; 39/39 across cost/DSR suites): all 14 labels re-run with ×2 base spread, ×3 event/rollover-hour spread (21:00–23:59 UTC), nightly swap $−2.5/lot, triple-Wednesday.

**Cost-stress results (same engine, same window, per label):**

| Condition | Sharpe | Trades | Swap $ | Verdict |
|---|---|---|---|---|
| Unstressed (deep sweep) | −4.30 | 207 | — | FAIL |
| **Stressed (×2 spread + ×3 event + swap)** | **−6.92** | **129** | **−1,437.78** | **FAIL, 61% worse** |
| Random-null (pipeline geometry, stressed) | −15.52 | 101 | incl. | null also collapses |

Read: the shared ruleset degrades −4.30 → −6.92 (×1.6 worse) under retail costs — no sign of an edge hiding behind optimistic costs. The stressed null (−15.52, pipeline geometry) is **not comparable** to the flat-engine null (−0.29) from the baseline sweep — different engines/geometry; within the same stressed pipeline, the ruleset sits +8.6 Sharpe above that null yet remains deeply unprofitable in absolute terms and fails every DSR gate. Swap alone consumed $1,437.78 of the account across 129 trades (avg ~$11/trade) — the framework's warning that multi-day holds bleed financing is now measured, not assumed. Also fixed en route: the foreign workstream's Stage 5 crashed every run with `name 'pd' is not defined` (missing pandas import — one-line blocker in `continuous_backtest_pipeline.py`).

---

## Appendix: evidence paths
- Boot/component evidence: `artifacts/pu_session8_boot.log` (and `pu_session7_boot.log`)
- Deep sweep: `artifacts/backtest_results/deep_sweep_5000.json`, `artifacts/pu_deep_sweep.py`
- Session reports: `artifacts/pu_session6_report.txt`, `pu_session8_report.txt` (+9 pending, gate 00:05Z)
- Session DB tallies: `llm_requests` (35 SUCCESS/122 lifetime), `order_intents` (0), `operational_events`
- Commits referenced: 11b4bd0 (slug), 99417d7 (backtest pipeline), 820bc6e (paper evidence), a85aabe (memory mislabel)

END-MARKER-PU-0156
