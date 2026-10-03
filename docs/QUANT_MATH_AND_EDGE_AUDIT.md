# Quantitative Math, Edge & Compounding Audit

Examiner's review of every calculation the system performs — quant developer, researcher,
discretionary-quant trader, HFT execution, and agentic-orchestration perspectives.
Verified by reading implementations (`core/metrics.py`, `expectancy_ruin_and_hierarchical_risk.py`,
`hierarchical_portfolio.py`, `cointegration_and_compounding_governance.py`, `copula_tail_risk.py`,
`bocpd.py`, `kalman_statarb.py`, `cpcv_validation.py`, `fractional_diff.py`, `meta_labeling.py`,
`transaction_cost_engine.py`, `urgency_sor.py`, `risk_manager.py`, `trading_safety.py`)
and tracing what the **production loop actually consumes** (`intelligence/llm_orchestrator.py`).

---

## 1. Executive verdict

The mathematical library is **institutional-grade and largely correct**: R-standardization,
EV/SQN/WFE/DSR/IC validation, Ralph-Vince ruin physics, run-theory streak bounds,
quarter-Kelly, volatility parity, R/S Hurst, ATR percentiles, Engle-Granger ADF cointegration,
Clayton tail dependence → heat compression, BOCPD changepoints, Kalman stat-arb, CPCV/PBO
overfitting control, fractional differentiation, triple-barrier meta-labeling, Almgren-style
urgency SOR, and a learned slippage calibrator (Smith predictor) that IS live.

**The single biggest finding is not math correctness — it is a wiring gap:** the production
loop (`execute_cycle`) consumes only ~20% of this stack. The rest sits behind the harness
tools/workflows layer where an *agent must choose to call it*. Deterministic safety math must
never depend on agent choice. Details in §3.

---

## 2. Verified-correct inventory (formulas checked line-by-line)

| Domain | Implementation | Formula verified | Status |
|---|---|---|---|
| 1R standardization | `calculate_1r_dollar_risk`, `calculate_normalized_lot_size` | R$=E·f%; Size=R$/ΔP·tick; floor-quantized, refuses over-risk at broker min | ✅ |
| Realized R | `calculate_realized_r_multiple` | side-aware, guards inverted stop | ✅ |
| Expectancy | `calculate_expected_value` | EV=pW−qL−friction | ✅ |
| SQN | `calculate_sqn` | √N·μR/σR (Vince), n≥10 | ✅ |
| WFE / DSR / IC | `calculate_wfe/_deflated_sharpe_ratio/_information_coefficient` | Lo variance w/ skew-kurtosis; E[max SR] asymptotic; joint-pair IC | ✅ (notes §4) |
| Ruin physics | `calculate_risk_of_ruin`, asymmetric recovery | ((1−A)/(1+A))^U; loss→recovery l/(1−l) | ✅ |
| Run theory | streak P(k)=q^k; L_max≈ln N/ln(1/q) | ✅ |
| Kelly | `calculate_kelly_criterion/_quarter_kelly_sizing` | f*=p−q/R; 0.25K capped 2% | ✅ |
| Vol parity | equity·targetVol/(ATR·mult) | ✅ |
| Hurst | R/S log-space regression, clamped [0,1] | ✅ |
| Master EV | Σ_k P(k)·Σ_i w_i,k·EV_i,k (floor +0.20R) | ✅ formula; wiring ⚠️ §3 |
| CAGR/Sharpe/Sortino/Calmar | standard annualizations | ✅ (2 fixes applied §4) |
| Cointegration | manual OLS+DF t-stat vs EG critical −3.34 | ✅ (notes §4) |
| Tail risk | empirical λ_L=P(U≤q,V≤q)/P(U≤q); ≥0.40 ⇒ heat 4R→1R | ✅ (not consumed live ⚠️) |
| BOCPD | Adams–MacKay run-length recursion, hazard 1/λ | ✅ (sliding-Gaussian approx) |
| CPCV/PBO | combinatorial splits + PBO calculator | ✅ (research-side) |
| Fractional diff | binomial weights, ADF-guided d, OU half-life, Bouchaud capacity | ✅ (features not fed live ⚠️) |
| Meta-labeling | triple-barrier paths + logistic meta-classifier | ✅ (not wired to live confidence ⚠️) |
| Execution | alpha-decay urgency SOR, slippage Smith-predictor feedback loop | ✅ LIVE |

## 2b. Fixes applied in this pass (`core/metrics.py`, tests green)

1. `calculate_cagr`: `end_equity <= 0` now returns **−100%** instead of 0% — total ruin was
   previously rendered as "flat", hiding the worst outcome from Calmar/CAGR consumers.
2. Sharpe denominator now uses dispersion of **excess** returns (rf-path consistency).

## 3. 🔴 The wiring gap — two risk constitutions

Evidence (grep of production loop imports/usage):

```
clayton_copula/tail_dependence  NOT-IN-MAIN-LOOP     bocpd/changepoint  NOT-IN-MAIN-LOOP
kalman                          NOT-IN-MAIN-LOOP     cpcv               NOT-IN-MAIN-LOOP
fractional_diff                 NOT-IN-MAIN-LOOP     meta_label         NOT-IN-MAIN-LOOP
hurst                           NOT-IN-MAIN-LOOP     sqn/risk_of_ruin   NOT-IN-MAIN-LOOP
deflated/walk_forward/IC        NOT-IN-MAIN-LOOP     volatility_parity  NOT-IN-MAIN-LOOP
heat_budget                     NOT-IN-MAIN-LOOP     master_expectancy  NOT-IN-MAIN-LOOP
kelly                            prompt-text only     slippage calibrator USED ✔
```

Consequences:
- **Two constitutions:** the LangGraph path enforces an EV floor (Master EV ≥ +0.20R);
  the main loop's `DeterministicValidator` enforces only min_rr≥2.0 + confluence≥3 —
  a 2:1 RR coin-flip with 40% win rate (EV = 0.4·2 − 0.6·1 = **+0.2R gross**) passes even at
  breakeven-after-costs. There is no friction-subtracted EV gate on the live path.
- Copula crisis heat-compression, BOCPD regime breaks, Hurst routing, vol-parity sizing,
  and DSR promotion evidence never touch live orders unless an agent explicitly invokes
  them as tools. Deterministic protection must be unconditional.

**Remediation (P0):** promote into `DeterministicValidator`/order path:
(a) net-EV floor using rolling labeled trade stats minus measured friction;
(b) heat cap (Σ open R ≤ budget, compressed by copula state when available);
(c) regime veto hooks already partially present via event policy — extend with BOCPD break.


## 4. 🟡 Math correctness notes (minor, examiner's marks)

| Item | Finding | Severity |
|---|---|---|
| DSR E[maxSR] constant | uses −(ln π + ln ln k)/(2√(2ln k)); canonical constant is ln 4π. Direction is conservative (harder p-value). | 🟡 cosmetic |
| ADF test is DF | no augmentation lags; EG residual criticals correct for 2-step design, but autocorrelated residuals distort size. Add BIC lag selection or `statsmodels.adfuller` + EG criticals. | 🟡 |
| Cointegration p-values | 3-bucket mapping (0.001/0.04/0.25) — fine as a gate, not a probability; never feed into Bayesian weights. | 🟡 |
| RoR formula | Ralph-Vince arithmetic approximation assumes fixed-fractional even-payoff bets; conservative for skewed payoffs. Guard, not exact probability. | 🟡 documented |
| Hurst R/S | biased for short series & heavy tails (min 50 pts enforced). Prefer DFA cross-check before routing decisions. | 🟠 roadmap |
| `record_trade` R fallback | pnl/(initial·1%) ignores actual entry risk & equity drift → distorts EV/SQN/Kelly inputs when risk ≠ 1%. Persist planned 1R$ per ticket. | 🟠 P1 |
| BOCPD | sliding-window Gaussian predictive instead of conjugate NIG — approximate but stable. | 🟡 |

## 5. Compounding path analysis (does the system actually compound?)

**Live growth mechanics today:** fixed-fractional 1% equity risk per trade
(`calc_position_size` re-reads account equity every call ✔) × confidence step-down
(0 / 0.5× / 1×) × circuit step-down × daily-risk cap. Because sizing scales off live equity,
wins compound geometrically and losses de-risk automatically — **the compounding spine is
correct.** Weekly breaker (−6R ⇒ 50% scale), session halt (−3R), and max-DD kill-switch
complete the drawdown physics.

What limits the compounding curve:
1. 🔴 No friction-subtracted EV gate on the live path (§3) — costs are the #1 silent killer of
   small-edge FX systems; +0.05R gross is negative after ~0.1R round-trip friction.
2. 🟠 Sizing confidence is the LLM's self-reported number — uncalibrated. The probability-of-win
   model that should drive Kelly exists (meta-labeling + labeled history) but is never consulted;
   quarter-Kelly remains advisory-only.
3. 🟠 Copula heat compression never binds live: portfolio heat can exceed the crisis budget.
4. 🟡 No vol targeting on live sizing: constant-$ risk while ATR varies ⇒ realized R-vol varies
   with regime → variance drag (the σ_p² term the governance module itself documents).
5. 🟡 Concurrent correlated positions uncapped at book level (EURUSD+GBPUSD same-way USD ≈ one
   doubled position); `correlation_risk` feeds regime snapshots only.


## 6. Missing from the quant universe (by domain)

**Researcher:** CSCV-PBO ✔, DSR ✔, WFE ✔ — missing: White's Reality Check / SPA bootstrap for
strategy-family selection; FDR (Benjamini–Hochberg) control when many setups are scored;
purged K-fold with embargo as default for any live feature re-fit; regime-conditional
calibration reports (Brier score, reliability curves) for confidence.

**Trader (discretionary-quant):** triple-barrier labeling ✔ — missing: expectancy-by-holding-
time surface (TIME_EXIT labels exist but aren't analyzed); session-of-day edge decomposition;
label-review loop feeding setup taxonomy beyond scenario docs.

**HFT/microstructure:** urgency SOR ✔, learned slippage ✔, VWAP micro-price ✔,
Almgren-Chriss card ✔ — missing: VPIN/toxic-flow estimate feeding maker/taker choice,
HAR-RV short-horizon vol forecast for spread expectations, fill-probability model for limit
entries, per-stage latency telemetry (data→decision→route).

**Portfolio construction:** hierarchical meta-portfolio ✔ (graph path), Carver band inertia ✔ —
missing: online Ledoit-Wolf shrinkage covariance, rebalancing bands on the live book,
USD-beta netting across FX positions.

**Probability-of-winning model (core ask):** every component exists — labeled trades,
triple-barrier meta-classifier, calibrated slippage, EV machinery — but the chain breaks at two
links: (a) meta-classifier is not trained/served on rolling labelled data; (b) its output never
replaces LLM confidence in `calc_position_size`. Closing this loop converts the system from
"LLM guess + fixed fraction" to "calibrated edge + fractional Kelly" — the compounding machine
§5 describes.

## 7. Agentic-orchestration examiner notes

- Tool registry exposes the full math stack to agents — good for research autonomy, unsafe as a
  *safety* mechanism (an agent can skip calls). Rule: **deterministic gates live in the
  validator/risk layer; agent tools stay advisory.**
- Harness workflows compose copula→heat and circuit-breaker state machines correctly; reuse
  those functions from the validator instead of duplicating math.
- `QuantitativeMetricsEngine` state is in-process only — after restart, EV/SQN/streak counters
  rebuild from zero. Persist engine state like RiskManager does (`save_state`/`load_state`).

## 8. Prioritized remediation roadmap

| Pri | Action | Where | Effort | Status |
|---|---|---|---|---|
| P0 | Net-EV floor gate: rolling labeled EV − measured friction_R ≥ floor (0.0 default; cold-start <30 samples passes) | `DeterministicValidator` (`trading_safety.py`) | S | ✅ IMPLEMENTED |
| P0 | Portfolio heat cap Σopen-R ≤ budget (4.0R default; copula-compressible via `set_heat_budget_r`) enforced inside sizing so every path inherits it | `RiskManager.calc_position_size` | M | ✅ IMPLEMENTED |
| P1 | Serve calibrated P(win): logistic meta-model over labelled journal features, decile reliability calibration; blend capped at raw confidence (can only downsize) → feeds `calc_position_size(confidence=...)` | NEW `intelligence/win_probability_model.py` + orchestrator `_sizing_confidence` | M | ✅ IMPLEMENTED |
| P1 | Persist QuantitativeMetricsEngine state: `to_state/from_state/restore_state` (+`record_trade(risk_amount=)` honest R fallback) | `core/metrics.py` | S | ✅ IMPLEMENTED (engine-level; live wiring pending — engine not yet instantiated in the loop) |
| P1 | Persist planned 1R$ per ticket; use it for realized-R fallback | trade journal | S | ◑ partial (`risk_amount=` path ready; journal capture at entry pending) |
| P2 | Vol-target overlay: size multiplier = clamp(atr_target/atr, 0.5..2), opt-in kwargs | `calc_position_size(atr=, atr_target=)` | S | ✅ IMPLEMENTED (callers may now pass ATRs) |
| P2 | BOCPD changepoint veto: `market["changepoint_probability"] > 0.45 ⇒ reject`; fed once/cycle from newest bar in `_validate_proposal` | validator env + orchestrator `_regime_changepoint_probability` | S | ✅ IMPLEMENTED |
| P2 | Friction estimate (1.5×spread + learned slippage)/stop-distance wired into the EV gate per proposal | orchestrator `_estimate_friction_r` | S | ✅ IMPLEMENTED |
| P2 | Correlation-aware concurrent-risk cap using existing correlation_risk / PortfolioRiskArbiter buckets | pre-trade arbiter | M | ◑ already covered by `PortfolioRiskArbiter` bucket limits; copula compression of that budget still open |
| P3 | DFA Hurst cross-check; adfuller swap; FDR over setup families | metrics/research | M | open |

**Verification of implemented rows:** new suite `tests/test_edge_gates.py` (15 tests) covers
the EV floor (cold start / negative block / positive pass / corrupt-journal degrade),
changepoint veto (veto / absent-key compat / low-pass), heat budget bounds & paper-mode
degradation, vol-overlay multiplier bounds, model train/calibrate/separation/insufficient-data,
and metrics persistence round-trip. Regression sweep green:
`test_trading_safety + test_risk_manager_safety + test_mathematical_pillars +
test_profitability_foundation + test_opportunity_fusion_gate + test_harness_integration +
test_edge_gates` → **62 passed**.

### Design guarantees baked into the gates
- **Cold-start safety:** no gate blocks a system that has not yet earned evidence (EV floor
  needs ≥30 reconciled labelled trades; changepoint key absent ⇒ old behavior).
- **Fail-safe degradation:** corrupt journal or unmeasurable open positions degrade to the
  previous conservative behavior instead of crashing sizing.
- **Model cannot inflate risk:** `_sizing_confidence` caps the calibrated blend at the raw
  LLM confidence until the model's own history earns trust.
- **Deterministic-only authority:** all gates live in the validator/risk layer, never behind
  agent tool calls.

**Verification:** `py_compile` clean; `test_mathematical_pillars.py` +
`test_profitability_foundation.py` → 24 passed after the §2b fixes.
