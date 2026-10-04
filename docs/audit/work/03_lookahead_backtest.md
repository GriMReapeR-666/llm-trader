# 03: Look-ahead bias and backtest realism audit

**Date:** 2026-10-04 · **Commit:** `3e7baf1` + uncommitted tree · **Scope:** live feature point-in-time correctness (Task A), backtests/labels (Task B), empirical truncation probes (Task C).
**Excluded (already in AUDIT §3.5, not re-reported):** DSR `trials=1`, √252 annualization on per-trade/per-pulse series, GA alpha-miner in-sample selection. Status confirmed where touched (see §5).

Severity: 🔴 reported performance is materially fabricated/leaked · 🟠 material bias in evidence that feeds gates/promotion · 🟡 bias or inconsistency with bounded impact · ⚪ hygiene.
"VERIFIED" = reproduced by a probe in `/private/tmp/la_probe/` or by unambiguous code reading (stated which). "UNVERIFIED" = needs broker/real data.


---

## 1. Findings (static reading, verified by code)

### B1 🟠 `full_pipeline_backtest`: entry fills at the NEXT bar's CLOSE, then SL/TP are tested against that same bar's high/low (path before the fill)
- `research/full_pipeline_backtest.py:1010-1014`: comment `# --- Open position at NEXT bar's open (no lookahead) ---`, code `_entry_ref = closes[i + 1]`; `entry_bar: i + 1`.
- Next loop iteration (`:679-745`) tests `low <= open_pos["sl"]` / `high >= open_pos["tp"]` on bar `i+1` — the bar whose close *is* the fill. SL/TP levels are anchored to `closes[i]` (signal bar), not to the fill.
- Scenario: BUY signal at close[i]; bar i+1 drops below SL then recovers. Fill booked at close[i+1] (already below the SL), exit booked at the SL price → a "stop loss" that books a **profit**. Conversely a bar that spiked through TP before the close books a TP win from a fill that happened after the spike.
- Impact (probe P2, VERIFIED): 20 random-walk runs, random signals, zero costs: 229/7,912 trades (2.9 %) exit on the fill bar (−0.088 R avg vs −0.012 R otherwise); **75 trades book an `sl` exit at a profit**. With random signals the net bias is ≈0 (−0.014 R ± 0.015), so this corrupts R:R geometry and per-trade attribution more than headline P&L; for signals correlated with bar i+1 the fill price absorbs that bar's move (direction strategy-dependent). Fix: fill at `opens[i+1]` and start SL/TP evaluation on bar `i+1` only for the part after the open (or conservatively from bar `i+2`), recompute SL/TP relative to the actual fill.

### B2 🟠 `broker_replay.replay`: same-bar fill+exit and half-spread costing; callers default `latency_bars=0` with entry = signal-bar close
- `research/broker_replay.py:141-155` fill when `low <= entry <= high` on `submit_index + latency_bars`; `ReplaySpec.latency_bars = 0` default (`:23`).
- Callers `intelligence/autonomous_causal_engine.py:652-667` and `research/historical_regime_replay.py:178-191` set `entry = bars[idx]["close"]`, `submit_index = idx`, no latency → the limit is *guaranteed* to fill on bar idx (close ∈ [low, high]), and the exit loop (`:169`) starts at `entry_index`, testing SL/TP against the full range of the bar **whose close is the fill** (path that happened before the fill). Stop-first tie-break makes SL+TP-same-bar conservative, but TP-only bars produce phantom wins and SL-only bars phantom losses.
- Spread: fill = `entry + direction*(spread/2 + slippage)` (`:153`), exits get only slippage (`:182,185`), END_OF_DATA exit gets nothing (`:190`). Bars are MT5 bid bars; a round trip costs the full spread. Under-charged by `spread/2` per trade (0.5 pip with the default 1.0-pip spec). Short-side SL/TP triggers use bid highs/lows without adding spread → short stops fire late, short TPs fire early.
- `swap_per_volume_day >= 0` enforced (`:59`): positive carry and triple-Wednesday cannot be modelled; swap prorated on calendar time.
- Impact (VERIFIED, probe P5a/P5b/P5c): a TP printed *before* the close fill is booked as a same-bar TARGET (+1 R); a 1.0-pip spread costs 0.5 pip (USD 5 instead of 10 per lot); random-walk, random-side, zero-cost intents with entry = signal close: latency 0 → **−0.158 R** (se 0.021) vs latency 1 → +0.022 R (se 0.022). The latency-0 artefact is the configuration used by `autonomous_causal_engine.backtest_causal_edge_strategy`, whose `expectancy_r` decides `REPLAY_PASS/REPLAY_FAIL` lifecycle (`intelligence/autonomous_causal_engine.py:797-806`): edge evidence is dominated by entry-bar noise (sign depends on stop distance vs bar range).
- Fix: default `latency_bars=1` (reject 0 unless explicitly declared), start exit scan at `entry_index+1` or model intrabar order with open-first, charge full spread (ask/bid conversion), add spread to short-side triggers.

### B3 🔴 `alpha_backtest_harness` / `alpha_ensemble.combine_alphas`: ensemble weights are full-sample IC statistics applied to every bar; signals are filled at the same close they are computed from
- `core/alpha_ensemble.py:433-438`: `ic_mean = float(ic_valid.mean())` over the **entire** rolling-IC series; `:768-770` weights from those; `:796-801` constant weights applied to all bars. The per-anchor IC is causal, but its mean is not: weights at bar t use IC computed from returns after t.
- `core/alpha_ensemble.py:356-358`: rolling IC at bar i includes `forward_returns[i]` = return from i to i+1 (`:705-707` `shift(-forward_return_periods)`), i.e. even each anchor uses one bar of future.
- `research/alpha_backtest_harness.py:963`: alphas computed on `close[t]`, no `shift(1)` (live path does shift: `data/market_data_provider.py:1446-1451`); `run_backtest` fills at `close[i]` (`:544,567`) — zero-latency fills on the bar that generated the signal.
- `core/alpha_factors.py:69-75` `rank()` = `df.rank(pct=True)` over the **full series** (docstring admits "not rolling"); `:78-83` `scale()` divides by the full-sample sum. Used inside 20+ alphas (001, 005, 014, 017, 019, 022, 025, 030, 034, 036, 037, 038, 055, 060, ...). In any historical evaluation (harness, `ensemble_backtest.py:107`, `capstone_validator.py:183,412`, `auto_quant_tools.py:2484`) these values are normalised with future data.
- Impact (VERIFIED, probe P3): with 12 **pure-noise** alphas on a driftless random walk (24 seeds), the harness's full-sample IC weights produce a composite with **out-of-half IC +0.048 (t = +8.1)** vs next-bar return; the same procedure with weights fitted on the first half only gives +0.0004 (t = +0.08), equal weights −0.002. An IC of 0.05 at M15/H1 is a large, entirely fabricated edge. Probe P1: 17 of 23 Alpha101 factors fail the truncation test (full-series `rank()`/`scale()`), as do `rolling_ic` (one-bar label leak) and `combine_alphas` composite (with and without orthogonalization).
- Live: `fetch_data` computes alphas on a 250-bar window ending at the forming bar and shifts them one bar (`market_data_provider.py:1446-1451`), so the live endpoint value uses no future data — but its scale depends on the window, so a research IC measured with full-history ranks is not the live signal (train/serve skew).
- Fix: rolling/expanding rank (`ts_rank`), compute weights from IC strictly before t (expanding mean of IC lagged by the label horizon), shift alphas one bar in the harness like live, fill at next open.

### B4 🟠 `mt5_backtest_engine`: silent synthetic fallback with positive drift + auto-promotion to PAPER_PROVEN + writes into the live `trades` table
- `research/mt5_backtest_engine.py:248-268`: when MT5 and `data_fetcher` are unavailable it silently generates `np.random.normal(0.0001, 0.0015)` returns with seed 42 (a +1 bp/bar drift = +5 % over the default 500 bars) and continues as if real.
- `:675-693`: `CANDIDATE -> VALIDATING -> PAPER_PROVEN` transition (`allow_auto=True`) on Sharpe ≥ 0.8 (per-trade × √252), win-rate ≥ 45 %, PF ≥ 1.2 from this one in-sample run — no paper trading, no OOS.
- `:787-816` `INSERT OR REPLACE INTO trades` with `pnl = pnl_pips`, `risk_amount = 100.0`, tickets `999000000+…` into `trading_state.db` (the production journal). The research labelled dataset filters on `trade_labels` so it is probably excluded there, but any other `trades` reader (P&L, dashboards) sees pips as currency (UNVERIFIED which readers).
- Fill/cost model (`:330-360`): entry at signal-bar close; SL/TP evaluated on **closes only** and exited at exactly `entry ± sl/tp` even when the close gapped beyond (losses truncated); `exit_price = entry ± dist - slip_cost` — slippage is **not multiplied by side**, so for shorts slippage *improves* both SL and TP exits; exits pay no spread (half-spread total); commission computed for the report (`:457`) but never deducted from `pnl_pips`. Also reached from `mt5_trader.py:3497` (`backtest --engine mt5`), which itself falls back to the same synthetic generator (`mt5_trader.py:3460-3476`).
- Probe P5d (VERIFIED): on the exact fallback generator, the EMA 12/26 crossover gets expR **+0.074, Sharpe 0.86** with the built-in drift vs expR −0.287, Sharpe −3.59 with drift 0 — the synthetic drift alone flips the sign. (This seed does not clear the promotion bar; other seeds/strategies can.) A SHORT that hits a 30-pip TP with 5-pip slippage books **+35 pips** (should be ≤ 25). `max_drawdown_pct` is measured in pips but compared to a 25 % threshold.
- Fix: fail closed without real data, never auto-promote from a single in-sample backtest, write research trades to a separate table, sign slippage by side, deduct commission, full spread.

### B5 🔴 `hybrid_backtest_pipeline`: tier-1 caps losers and keeps winners' overshoot; tier-2 ignores the strategy under test
- `research/hybrid_backtest_pipeline.py:52-97` (`_run_vectorized`): close-only checks; `trades_pnl.append(pnl if pnl >= tp_pips else -sl_pips)` — winners keep the full close-to-close overshoot beyond TP, losers are capped at exactly −SL. No costs. `exits` is treated as a *short entry* signal (`:74-77`).
- `:100-130` (`_run_mt5_replay`): receives `entries`/`exits` but runs `engine.run_crossover_strategy(bars_df, 12, 26)` — "tier-2 realistic replay" validates a fixed EMA crossover, not the candidate.
- Impact (VERIFIED, probe P6): random entries on a driftless walk with zero costs → **+0.36 R per trade** (2,572 trades, 10 seeds; reported total_return ≈ +9,100 %/run). Reachable via `mt5_trader.py:3529` (`backtest --engine hybrid`); `passes_promotion` is reported, not consumed by a gate (verified by grep).
- Fix: symmetric barrier accounting (exit at barrier ± modeled gap for both), pass the candidate's signals to tier 2.

### B6 🟠 Macro/event features in research use observation dates as release dates and latest-vintage data
- `research/fred_backtest_data.py:170-187`: FRED `get_series()` (latest vintage, revised) with `release_date = observation date` (e.g. PAYEMS 2026-08-01 is released ~2026-09-05). Values become "known" ~5 weeks early and revised values replace first prints. `consensus = previous (revised) value` (`:178`).
- `:477-494` `_match_event_to_releases` picks the **closest** release by `abs(delta)` within 45 days — can attach a release from *after* the event.
- `research/news_gate_backtest.py:844-876` (legacy path): attaches the *current* FRED indicator values (fetched now) to every historical event.
- `research/news_gate_backtest.py:902-946`: `base = datetime(2025,1,1)` (a Wednesday) + whole weeks → every generated event date is a Wednesday, so the `weekday()==4` NFP and `==3` claims branches never fire. Probe P7 (VERIFIED): 730 days → **19 events, all `ISM MANUFACTURING PMI`, all Wednesdays**, 2025-01-01 → 2026-12-02 (incl. future dates); zero NFP/claims events in the "real MT5 history" news-gate backtest.
- `:519-543` `_bars_to_trades`: `direction = "BUY" if next_bar["close"] > bar["close"]` chooses direction from the outcome (pnl itself is unsigned long P&L, so this field is a latent leak, not used in stats).
- `:597` reaction `price_before = bars[best_idx]["close"]` where `best_idx` is the bar whose **open** time is nearest the release → for M15 that close is 15 min *after* the print; the measured "spike" omits the first 15 minutes.
- `:261-309` synthetic trades hard-code the hypothesis (event-window trades get 3× slippage and a 60/40 whipsaw flip) — the gate's "evidence" on the synthetic path is circular.
- Fix: use ALFRED `realtime_start` vintages and the official release calendar time; match only releases with `release_time <= event_time`.

### A1 🟡 Live SMC block runs on the forming bar
- `intelligence/llm_orchestrator.py:7713-7738` passes all of `toon["ohlc"]` (last row = forming bar, see `feature_builder.py:45`) to `detect_structure/find_order_blocks/liquidity_pools`; `_curr_price = _c[-1]` (forming close) for premium/range position.
- `core/smc_structure.py:55-59` centered pivots (`window` bars each side); `:84` uses swings `s < i-1`, i.e. a swing at `i-2` that needs bars up to `i+1` to exist — historical BOS/CHoCH events are stamped at bars where they could not yet be known (probe P1). `:128-129` OB `mitigated` uses all later bars incl. the forming one.
- Correction after probe P1: `detect_structure` is **causal** (a swing at `s ≥ i-3` always has bars `i-1, i` inside its pivot window, so two closes beyond it are impossible) — truncation test PASS. `swing_points` itself is centered (pivots ≤ t−3 only; by design) and OB `mitigated` uses later bars (endpoint-only use is fine).
- Remaining live issue: the forming bar is the `i` of the two-close BOS/CHoCH confirmation and of `_curr_price`, so `last_event`/`has_choch`/`in_premium` can flip intrabar. Fix: pass `ohlc[:-1]`.

### A2 🟡 Several live context fields read `iloc[-1]` (forming bar) despite the completed-bar contract
- `data/market_data_provider.py:1157-1167` (multi-TF dict: price, RSI, MACD, EMA, ATR, trend for D1…M5), `:1332-1336` (HTF trend/RSI), `:1391-1394` (HMA trend), `:1509-1511` (regime engine ADX/ATR), `:1189-1213` (confluence). `_identify_support_resistance` (`:1117-1125`) confirms pivots with `i+1..i+window`, including the forming bar.
- These feed `mtf_alignment`, `regime`, `near_key_level` in `_validate_proposal` (`intelligence/llm_orchestrator.py:2376-2385`) while the snapshot features use `[-2]`. In live this is current (not future) information, so no P&L leak; but (a) setup classification depends on an incomplete H4/D1 bar, and (b) any replay that feeds completed bars into this path leaks a full bar. The opportunity-bus path hard-codes `mtf_alignment: 0` (`:7746`) while the validator computes it → train/serve skew.
- Fix: drop the last row for all HTF fetches (`df.iloc[:-1]`) or record HTF bar close time and require `htf_close_time <= decision_time`.

### A3 🟡 Server time vs UTC: two opposite assumptions about MT5 `tick.time_msc`
- `data/market_data_provider.py:131-182` derives the server offset by assuming `tick.time_msc` is **server-local** epoch (and corrects bar times).
- `core/execution_planning.py:146-158` (`validate_quote`), `intelligence/llm_orchestrator.py:2842-2851` (clock health, `provider_time`), `:3686-3694` (tick age) treat the same field as **UTC**. On a UTC+3 server: `validate_quote` age ≈ −10.8 M ms → every order rejected as "clock-drifted"; tick-age in the lag event is `max(0, negative)` = 0 → staleness invisible for up to 3 h. One of the two assumptions is wrong. UNVERIFIED (no broker).
- MT5 calendar times (`data/market_data_provider.py:356`, `data/mt5_calendar_wrapper.py:559,641,692`) decoded as UTC with no offset → event proximity off by the server offset (UNVERIFIED).
- A single offset is applied to all historical bars (`:283-285`): for multi-month research pulls across a DST change, session-hour features are off by 1 h for half of the sample. `core/capstone_validator.py:174` decodes bars with no offset at all.

### B7 🟡 `vbt_engine` annualization string-match bug, no slippage/spread, shorts ignored
- `research/vbt_engine.py:183`: `if "M1" in tf_clean` is tested first, so **"M15" (and "M10"/"M12") match M1** → `periods_per_year = 252*24*60`; Sharpe for M15 overstated by √15 ≈ 3.87×; "M30" falls through to the M15 constant (√2 overstated). Probe P4 (VERIFIED): identical trades give Sharpe −8.23 for both "M1" and "M15" (ratio 1.0, correct 0.258); `slippage_pips=50` gives a result identical to 0.
- `:150-168` fill at signal-bar close; `slippage_pips` stored (`:100`) but never used; no spread; `short_entries/short_exits` accepted and ignored. Used by `mt5_trader.py:3484` (`backtest --engine vectorbt`), `core/skill_registry.py:106`, LLM code interpreter.

### B8 🟡 CPCV / walk-forward are not what they say
- `core/cpcv_backtester.py:44` `embargo_bars` computed, never used; no model is fit, so "purging" is moot; `:92` "PBO" = share of negative path Sharpes, not Bailey PBO; `:57` std floored at 0.002. This is a resampling of one fixed return series.
- `core/walk_forward_validator.py:517-581` splits a fixed return series; nothing is re-optimised per fold, so WFE of a strategy tuned on the whole sample is ≈1 by construction and cannot detect curve-fitting.
- Fix: run CPCV/WF over the *fitting procedure* (params chosen on train folds only).

### B9 🟡 Triple-barrier labels: close-only, fill at signal close, overlapping labels without uniqueness weights
- `core/meta_labeling.py:82,97-110`: entry = `p[idx]` (signal-bar close), touches tested on closes only (intrabar touches missed; exit at the overshooting close), concurrent events not weighted. Labels are causal (no leak into features), but meta-model labels ≠ executable outcomes.

### B10 ⚪ Research label selection excludes non-decisive exits
- `research/baseline_research.py:122` and `research/supervised_training.py:68-74` drop BREAKEVEN/TIME/EVENT/MANUAL exits from training *and* evaluation; reported `expectancy_r` is conditioned on a decisive outcome. Impact direction depends on strategy (UNVERIFIED magnitude).

### A4 ⚪ `zero_lag_macro_provider` hard-codes "nowcasts"
- `data/zero_lag_macro_provider.py:381-394` constants (CPI 0.22 % MoM, grid load 101.4 …) labelled `ZERO_LAG`. The toon injection (`data/market_data_provider.py:1906-1913`) reads non-existent attributes (`regime`, `nowcast_gdp`) and always falls back to `UNKNOWN`, so the constants don't reach the LLM via that path. Delete or mark synthetic.

---

## 2. Task C: truncation (future-leak) test, tabulated

**Method** (`/private/tmp/la_probe/p1_truncation.py`, run with `.venv/bin/python`, `FAST_TEST=1 LLM_TRADER_OPENBB_OFFLINE=1`, no network/broker): synthetic GBM OHLCV (700 M15 bars; 4,400 bars for the multi-day persistence alphas). For each function f and test points t ∈ {350, 450, 550, 640} (persistence: {3950, 4200}) compare `f(data[:t+1])[t]` with `f(data)[t]` (tol 1e-9). Any difference means the value at t depends on bars after t. For endpoint-only live functions (`build_snapshot`, S/R levels) the test is "perturb the forming bar −1, values must not change". SMC lists compare events/pivots with index ≤ t.

**Tally: 62 functions tested → 40 PASS, 22 FAIL.** All 22 failures are full-sample normalisation (`rank()`/`scale()` inside Alpha101 factors), the forward-return label inside rolling IC, the ensemble's full-sample IC weights, or centered/endpoint SMC helpers (expected by design, flagged ⚪).

| # | Function | Location | Result | Example (t, truncated, full) |
|---|---|---|---|---|
| 1 | `ema(50)` | `core/technical_indicators.py:17` | ✅ PASS |  |
| 2 | `rsi(14)` | `core/technical_indicators.py:21` | ✅ PASS |  |
| 3 | `macd line` | `core/technical_indicators.py:32` | ✅ PASS |  |
| 4 | `macd hist` | `core/technical_indicators.py:32` | ✅ PASS |  |
| 5 | `atr(14)` | `core/technical_indicators.py:53` | ✅ PASS |  |
| 6 | `wma(21)` | `core/technical_indicators.py:111` | ✅ PASS |  |
| 7 | `hma(21)` | `core/technical_indicators.py:122` | ✅ PASS |  |
| 8 | `adx(14)` | `core/technical_indicators.py:131` | ✅ PASS |  |
| 9 | `alpha ALPHA001` | `core/alpha_factors.py` | ❌ FAIL | 350, -0.3979, -0.4143 |
| 10 | `alpha ALPHA005` | `core/alpha_factors.py` | ❌ FAIL | 350, -0.0013, -0.0004962 |
| 11 | `alpha ALPHAP01` | `core/alpha_factors.py` | ✅ PASS |  |
| 12 | `alpha ALPHAP02` | `core/alpha_factors.py` | ✅ PASS |  |
| 13 | `alpha ALPHAP03` | `core/alpha_factors.py` | ✅ PASS |  |
| 14 | `alpha ALPHAP04` | `core/alpha_factors.py` | ✅ PASS |  |
| 15 | `alpha ALPHAP05` | `core/alpha_factors.py` | ✅ PASS |  |
| 16 | `alpha ALPHAP06` | `core/alpha_factors.py` | ✅ PASS |  |
| 17 | `alpha ALPHA009` | `core/alpha_factors.py` | ✅ PASS |  |
| 18 | `alpha ALPHA012` | `core/alpha_factors.py` | ✅ PASS |  |
| 19 | `alpha ALPHA014` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.5165, 0.5121 |
| 20 | `alpha ALPHA017` | `core/alpha_factors.py` | ❌ FAIL | 350, -0.124, -0.1022 |
| 21 | `alpha ALPHA019` | `core/alpha_factors.py` | ❌ FAIL | 350, -1.594, -1.171 |
| 22 | `alpha ALPHA022` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.009237, 0.007173 |
| 23 | `alpha ALPHA023` | `core/alpha_factors.py` | ✅ PASS |  |
| 24 | `alpha ALPHA025` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.4578, 0.4963 |
| 25 | `alpha ALPHA028` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.0002682, 0.0001242 |
| 26 | `alpha ALPHA030` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.1632, 0.1731 |
| 27 | `alpha ALPHA034` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.3514, 0.3848 |
| 28 | `alpha ALPHA035` | `core/alpha_factors.py` | ✅ PASS |  |
| 29 | `alpha ALPHA036` | `core/alpha_factors.py` | ❌ FAIL | 350, 3.552, 3.589 |
| 30 | `alpha ALPHA037` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.8091, 1.006 |
| 31 | `alpha ALPHA038` | `core/alpha_factors.py` | ❌ FAIL | 350, -0.3391, -0.2649 |
| 32 | `alpha ALPHA041` | `core/alpha_factors.py` | ✅ PASS |  |
| 33 | `alpha ALPHA043` | `core/alpha_factors.py` | ✅ PASS |  |
| 34 | `alpha ALPHA053` | `core/alpha_factors.py` | ✅ PASS |  |
| 35 | `alpha ALPHA055` | `core/alpha_factors.py` | ❌ FAIL | 350, 0.5168, 0.49 |
| 36 | `alpha ALPHA060` | `core/alpha_factors.py` | ❌ FAIL | 350, -0.000906, -0.0005462 |
| 37 | `alpha ALPHA101` | `core/alpha_factors.py` | ✅ PASS |  |
| 38 | `af.rank (primitive)` | `core/alpha_factors.py:69` | ❌ FAIL | 350, 0.01709, 0.01571 |
| 39 | `af.scale (primitive)` | `core/alpha_factors.py:78` | ❌ FAIL | 350, -2.214e-05, -1.023e-05 |
| 40 | `af.ts_rank(10)` | `core/alpha_factors.py:28` | ✅ PASS |  |
| 41 | `af._vwap (cumulative)` | `core/alpha_factors.py:536` | ✅ PASS |  |
| 42 | `ae.zscore_normalize expanding` | `core/alpha_ensemble.py:296` | ✅ PASS |  |
| 43 | `ae.rank_normalize window=60` | `core/alpha_ensemble.py:312` | ✅ PASS |  |
| 44 | `ae.rolling_ic (fwd-return label)` | `core/alpha_ensemble.py:327` | ❌ FAIL | 350, -0.01373, -0.01306 |
| 45 | `ae.combine_alphas composite (ic_ir)` | `core/alpha_ensemble.py:637` | ❌ FAIL | 350, -0.5076, -0.4364 |
| 46 | `ae.combine_alphas composite (ic_ir, ortho=True as live)` | `core/alpha_ensemble.py:637 + signal_orthogonalizer` | ❌ FAIL | 350, -0.4857, -0.3799 |
| 47 | `abh.apply_cost_edge_filter` | `research/alpha_backtest_harness.py:298` | ✅ PASS |  |
| 48 | `abh.apply_trend_regime_gate` | `research/alpha_backtest_harness.py:370` | ✅ PASS |  |
| 49 | `fpb._ema(50)` | `research/full_pipeline_backtest.py:1275` | ✅ PASS |  |
| 50 | `fpb._hma(21)` | `research/full_pipeline_backtest.py:1294` | ✅ PASS |  |
| 51 | `fpb._atr(14)` | `research/full_pipeline_backtest.py:1315` | ✅ PASS |  |
| 52 | `fpb._adx(14)` | `research/full_pipeline_backtest.py:1329` | ✅ PASS |  |
| 53 | `fpb._rsi(14)` | `research/full_pipeline_backtest.py:1389` | ✅ PASS |  |
| 54 | `fpb._alpha_composite_proxy` | `research/full_pipeline_backtest.py:1415` | ✅ PASS |  |
| 55 | `fpb._rolling_percentile_rank(100)` | `research/full_pipeline_backtest.py:1259` | ✅ PASS |  |
| 56 | `fd.fractional_diff(d=0.4)` | `core/fractional_diff.py:191` | ✅ PASS |  |
| 57 | `smc.detect_structure (BOS/CHoCH events <= t)` | `core/smc_structure.py:76` | ✅ PASS |  |
| 58 | `smc.swing_points (pivots <= t)` | `core/smc_structure.py:50` | ❌ FAIL | 550, 53, 54 |
| 59 | `smc.find_order_blocks (mitigated flag)` | `core/smc_structure.py:106` | ❌ FAIL | 640 |
| 60 | `smc.liquidity_pools` | `core/smc_structure.py:135` | ✅ PASS |  |
| 61 | `feature_builder.build_snapshot (perturb forming bar -1)` | `intelligence/feature_builder.py:37` | ✅ PASS |  |
| 62 | `MarketDataProvider._identify_support_resistance (perturb forming bar)` | `data/market_data_provider.py:1117` | ✅ PASS |  |

Notes: `smc.swing_points` (FAIL) is a centered pivot by design; the consumer `detect_structure` is causal (PASS). `find_order_blocks` FAIL is the `mitigated` flag using later bars — fine at the live endpoint, a leak if used historically. `liquidity_pools` judged by reading (trailing window only).

## 3. Probe index (all under `/private/tmp/la_probe/`)

| Probe | File | Demonstrates | Key number |
|---|---|---|---|
| P1 | `p1_truncation.py` (+`p1_results.json`) | truncation test, §2 | 22/62 fail |
| P2 | `p2_full_pipeline.py` | B1 fill at next close + same-bar SL/TP | 75 "SL" exits at a profit; 2.9 % fill-bar exits |
| P3 | `p3_ensemble_weights.py` | B3 full-sample IC weights on noise alphas | OOS-half IC +0.048 (t=8.1) vs causal +0.0004 |
| P4 | `p4_vbt_annualization.py` | B7 "M15"→M1 annualization; slippage unused | Sharpe(M15)=Sharpe(M1) |
| P5 | `p5_replay_engines.py` | B2 (a,b,c), B4 (d), B5 (P6), B6 (P7) | latency-0 −0.158 R; hybrid +0.36 R/trade on noise; short slippage +35 pips; 19 Wednesday-only events |

## 4. Verified clean

- `core/technical_indicators.py` `ema`, `rsi`, `macd`, `atr`, `wma`, `hma`, `adx` (and the O(1) `*_step` forms by reading): causal (P1 PASS).
- `intelligence/feature_builder.build_snapshot`: uses bars `[:-1]` and indicator index `-2`; perturbing the forming bar changes nothing (P1 PASS). Cross-market observations with `timestamp > captured_at` are rejected (`:150`); events earlier than capture are excluded (`:176`).
- `_validate_proposal` close/EMA/ADX/ATR read from the snapshot or `[-2]` (`intelligence/llm_orchestrator.py:2363-2373`); `_build_rl_state` likewise (`:2271-2285`). (Exceptions: MTF/regime/key-levels, A2.)
- Alpha columns are shifted one bar in live (`data/market_data_provider.py:1446-1451`); correlation engine records completed bars only (`:1539-1545`).
- `core/alpha_factors.py` ts_* primitives, `_vwap`, persistence alphas P01–P06, alphas 009/012/023/035/041/043/053/101 (P1 PASS).
- `core/alpha_ensemble.zscore_normalize` / `rank_normalize` (expanding/rolling, P1 PASS); `expanding_ic` causal by reading (labels still forward, see B3).
- `research/alpha_backtest_harness.apply_cost_edge_filter` (`x=z.shift(1)`, `y=fwd.shift(1)`) and `apply_trend_regime_gate` (P1 PASS). Cost level in the harness (2 bp commission + 0.5 bp "slippage" per side ≈ 2.75 pips RT on EURUSD) is conservative for majors; note `slippage_pips` is actually a fraction, not pips.
- `research/full_pipeline_backtest` indicator helpers `_ema/_hma/_atr/_adx/_rsi/_alpha_composite_proxy/_rolling_percentile_rank` (P1 PASS); swap/triple-Wednesday and session spread stress modeled (opt-in); flat-cost path charges full spread on both legs (conservative).
- `core/fractional_diff.fractional_diff` (fixed-width window, P1 PASS).
- `core/smc_structure.detect_structure` (P1 PASS, see A1 correction), `liquidity_pools`.
- `research/research_validation.purged_walk_forward_splits` + `labelled_dataset` ordered by `opened_at` (`:207`): purge test `label_start <= test_end and label_end >= test_start` is correct for forward-only folds; `research/supervised_training.py` fits standardization and coefficients inside each training fold (`:87-99`), predicts test only; threshold selection in `baseline_research` uses train rows only (`:167`).
- `core/meta_labeling` triple barrier: forward labels only, no feature leakage (realism caveats in B9).
- `research/broker_replay`: stop-first tie-break when SL and TP are in the same bar (`:181`); weekend bars skipped; malformed bars fail closed.

## 5. Status of AUDIT §3.5 items touched here (not re-reported)
- √252 on per-trade series: still present in `research/mt5_backtest_engine.py:395,400`, `research/hybrid_backtest_pipeline.py:94`, `core/cpcv_backtester.py:58`. Open.
- DSR trials: `core/cpcv_backtester.py:80` passes `num_trials=len(combos)` (15 overlapping resamples of one series), which is not a trial count either. Open.

## 6. Counts
🔴 2 (B3 ensemble/alpha full-sample leak, B5 hybrid tier-1) · 🟠 4 (B1, B2, B4, B6) · 🟡 6 (A1, A2, A3 [UNVERIFIED], B7, B8, B9) · ⚪ 2 (B10, A4). Total 14.
Truncation test: 62 tested, 40 pass, 22 fail.
