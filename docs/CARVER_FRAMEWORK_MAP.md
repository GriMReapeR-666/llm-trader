# Carver (2015) → this repo: framework map and gap list

Source: Robert Carver, *Systematic Trading*, Harriman House, 2015 (326 pp.).
Book text extracted with `pypdf`; page references below are the book's own
pagination. Repo claims were verified by reading the named files — file:line
anchors are included so each one can be re-checked.

This is a map, not a rewrite. It answers: *which of the book's mechanisms
already exist here, which are missing, and which of the repo's own
implementations duplicate each other.*

---

## The framework in one page

The book's chain, end to end:

| Step | Rule | Book |
|---|---|---|
| Rule → forecast | Each rule emits a raw forecast per instrument | Ch. 6-7 |
| Rescale | Lift the forecast so mean \|forecast\| ≈ 10; `scalar = 10 / mean\|forecast\|` | App. D, p.297 |
| Combine | Equal weight preferred; cap any single rule at **2× the equal weight** | Ch. 8 |
| Cap | Combined forecast bounded at **±20** | p.201 |
| Volatility target | Cash volatility target, typically **10% annualised**; daily target = target / √periods | Ch. 9, p.172 |
| Volatility scalar | `scalar = daily cash target / instrument value volatility`, where instrument value volatility = `price vol (in %) × block value × FX` | Ch. 10, Table 30 |
| Subsystem position | `(forecast / 10) × volatility scalar` — **absolute, in instrument blocks** | Ch. 10, Table 31 |
| Portfolio position | `subsystem × instrument weight × diversification multiplier` | Ch. 11, Table 32 |
| Max position | `2 × volatility scalar × instrument weight × diversification multiplier` | p.201 |
| Diversification | `1 / √(W·H·Wᵀ)`, **negative correlations floored at zero** | App. D, p.297 |
| Volatility estimate | 25-day SMA of returns' stdev, or EWMA with `A = 2/(1+L)`, L=36 → A=0.054 | App. D, p.298 |
| Execution | Round the target; skip trades while the target is within **10%** of the current position (position inertia) | Ch. 11, p.174 |
| Speed limit | Costs in SR units; `speed_limit = 0.13 SR / standardised cost`; keep max position ≥ 4 blocks | Ch. 12, p.187 |
| Rule quality | Ideas first, simple, explainable; realistic Sharpe 0.5-1.0 | Ch. 2 |
| Fitting | In/out-of-sample, noise reduction, parameter capping, bootstrapping | Ch. 3 |

---

## Chapter-by-chapter map

| Ch. | Prescription | Repo coverage | Status |
|---|---|---|---|
| 1 | Cognitive biases; avoid narrative fallacy | `core/meta_loop.py` (pattern corpus, decay mining) | covered (different mechanism) |
| 2 | Ideas-first rules; simple; realistic Sharpe 0.5-1.0 | `core/alpha_factors.py:78` `scale`, `:554` `_ALPHA_REGISTRY`; `core/alpha_discovery.py` | **partial** — discovery is data-first, see gap 1 |
| 3 | Fitting discipline: in/out-of-sample, noise reduction, parameter capping, bootstrapping | `core/overfit_detector.py:89` `deflated_sharpe_test`, `:173` `minimum_backtest_length`, `:252` `measure_is_oos_divergence`, `:303` `detect_stepwise_fitting`, `:367` `cv_stability_test`; `core/metrics.py:255` DSR, `:150` WFE; `research/backtest_dsr_integrator.py:124`; `tests/test_backtest_cost_model_cpcv.py` | **stronger than the book** |
| 4 | Portfolio allocation; diversification multiplier; drawdown budget | `core/harness/extracted_module_plugins.py:290` `allocate_capital`; `core/spmc_regime_filter.py:266` `get_sizing_multiplier` | partial — rule-based per regime, no correlation-driven multiplier |
| 5 | Framework overview | — | n/a |
| 6 | Instruments: size, diversification, costs, skew | `core/alpha_capacity.py` (turnover velocity) | partial |
| 7 | Forecasts: rescale to mean \|f\| ≈ 10 | `core/alpha_ensemble.py` z-score/rank normalisation | **covered** (mechanism differs) |
| 8 | Combine forecasts; cap each rule at 2× equal weight | `core/alpha_ensemble.py:512` `compute_ic_weights` (James-Stein shrinkage toward equal), `:648` `combine_alphas`; `core/signal_orthogonalizer.py:559` | partial — shrinks toward equal, but **no 2× cap** |
| 9 | Volatility targeting: 10% cash target, daily conversion | `core/metrics.py:727` `calculate_volatility_parity_size` (ATR-based, per instrument) | partial — no portfolio-level target, no cash-target form |
| 10 | Position sizing: forecast scaling, absolute positions | — | **missing** — repo has multipliers only, see gap 3 |
| 11 | Portfolios: instrument weights, diversification multiplier, position inertia, rounding | `research/alpha_miner/dnn_aggregator.py:245` `get_portfolio_weights` | **missing** — no multiplier, no inertia |
| 12 | Speed and size: costs in SR units, speed limits, max positions | `research/alpha_backtest_harness.py` (cost model), `core/alpha_capacity.py` | partial |
| 13-15 | Personas: semi-automatic, asset allocator, systems trader | `core/auto_quant_tools.py:2272` `_run_account_sizing`; `intelligence/llm_orchestrator.py` | n/a — different product shape |
| 16-17 | Review and conclusion | `core/meta_loop.py:418`/`:464`/`:504` (pattern mining) | covered |

---

## The five gaps that matter

**1. The repo is data-first; the book is ideas-first.** `core/alpha_discovery.py`
and `research/alpha_miner/` search for profitable patterns and then control the
over-fitting afterwards. Carver's Ch. 2 argument is that the control is weaker
than not generating the pattern in the first place: data-first fitting produces
rules whose profitability has no explanation, so there is no way to know
whether the source of return will repeat. The repo's DSR/CPCV/overfit-gate
machinery is a genuine answer to the *statistics* of data snooping, but not to
the *story* problem. Cheapest honest move: for any new rule, write the
hypothesis before the search, and record it next to the result.

**2. No caps on combined-forecast weights.** `compute_ic_weights` shrinks
weights toward equal weight (λ=0.3) but nothing bounds a single alpha's
contribution. A covariance-solved weight set can put a large share on one
correlated family. Carver's cap (2× equal weight, Ch. 8) is four lines and
bounds the damage.

**3. No absolute position sizing.** Six paths exist, all multiplicative:
`core/account_sizing.py:102` (risk-per-trade + stop distance),
`core/alpha_sizing.py:67` (composite → 0.25-1.35×), `core/metrics.py:553`/`:565`/
`:607`/`:632` (Kelly family), `core/metrics.py:727` (vol parity),
`research/alpha_miner/cvar_risk.py:162` (CVaR),
`research/alpha_miner/alpha_risk.py:195` (IC-quality multiplier), plus regime
multipliers from `core/spmc_regime_filter.py:266`. None of them can say "this
position is 12% of capital because the forecast is +12 and the instrument's
volatility is 1.1%". Carver's chain does, and it is auditable end to end.

**4. No portfolio-level risk target or diversification multiplier.** Volatility
targeting is per-instrument and ATR-based; there is nothing that checks the
*portfolio's* volatility, and no `1/√(W·H·Wᵀ)` anywhere in the sizing path.
(`tools/scrapers/process_transcripts.py:379` computes a diversification ratio,
but that is knowledge-base text for the LLM, not code that sizes anything.)
This is the gap that most directly controls "am I taking too much risk with
six correlated alphas on one symbol".

**5. Rule quality is not measured for individual signals.** The repo measures
strategy-level quality (DSR, WFE, CV stability) but never asks whether *a
specific signal* — a single candlestick pattern, a single alpha — has an edge
that survives an out-of-sample split. That is the test Carver applies to
trading rules in Ch. 2-3. `research/fluey_fitness.py` (added below) applies it
to the FLUEY catalogue.

---

## Sizing paths: six implementations, one decision

| Path | Style | Multiplier or absolute? | Verdict |
|---|---|---|---|
| `core/account_sizing.py` | risk-per-trade, stop distance | absolute units, but not forecast-linked | keep — it is the execution-side safety net |
| `core/alpha_sizing.py` | alpha composite → 0.25-1.35× | multiplier | keep as a *confidence* adjustment, not a sizing method |
| `core/metrics.py` Kelly family | win rate / payoff | absolute fraction of equity | keep for expected-value work, not for signal sizing |
| `core/metrics.py` vol parity | ATR | absolute units | keep — it is the closest thing here to a volatility target |
| `research/alpha_miner/cvar_risk.py` | CVaR | absolute units | keep — portfolio-risk view |
| `core/spmc_regime_filter.py` | regime switch | multiplier | keep — orthogonal to signal sizing |
| `core/forecast_sizing.py` *(new)* | Carver chain | absolute, forecast-linked | keep — the reference implementation |
| `core/sizing_spine.py` *(new)* | one order of operations: absolute size, then bounded multipliers, then the cap | absolute, capped | keep — the consolidation target |

**Recommendation:** adopt `core/sizing_spine.py` as the single *signal-to-
position* path, and demote the multiplier paths to what they actually are —
confidence and regime adjustments applied *after* an absolute size is chosen.
Do not delete anything yet: the multipliers are wired into live code paths
(`data/market_data_provider.py:1577`), so consolidation is a staged migration,
not a cleanup.

The migration is measurable rather than arguable: the route never risks more
than the live rule does. The live rule spends a flat `risk_per_trade` of
equity on every trade; the route spends at most that and usually less, because
a small forecast buys a small position. `tests/test_sizing_spine.py` asserts
it across the forecast range, and asserts that no combination of multipliers
can push the risk above the limit.

Carver's Ch. 2 is blunt about this: the best system is the simplest one that
works, and "as simple as possible" means few moving parts and no weird
interactions. Six sizing paths that multiply into each other is the opposite.

---

## Added by this change

Two new modules, both opt-in and unreferenced by the live path:

- **`core/forecast_sizing.py`** — the Carver chain: forecast rescaling, capped
  forecast combination, cash volatility target, volatility scalar,
  forecast-scaled absolute position, diversification multiplier, position
  inertia. Verified against the book's own worked example (Tables 30-32,
  p.172-173): a EUR 1,000,000 account at a 10% cash volatility target produces
  the book's positions of 6.42, -2.62 and -4.91 blocks.
  See `tests/test_forecast_sizing.py`.

- **`research/fluey_fitness.py`** — the fitness test from gap 5: FLUEY labels
  → forward returns in ATR units → purged, embargoed in-sample / out-of-sample
  split → net-of-cost verdict per pattern, plus `FlueyFitnessGate` to say
  which patterns may influence a position. Eligibility requires a positive
  out-of-sample edge, a t-statistic ≥ 2, enough signals, and agreement
  between the two blocks.
  See `tests/test_fluey_fitness.py`.

- **`core/sizing_spine.py`** — the consolidation target: one order of
  operations for a signal to become a position. Absolute size first (the
  Carver chain), then the bounded confidence and regime multipliers, then
  the risk cap. Reached from the live path only through the evidence gate
  below. See `tests/test_sizing_spine.py`.

- **`core/sizing_route.py`** — the authority layer: the autonomous decision
  of which sizing route is in force, made from the contract's own Phase 2
  exit criteria rather than from preference. See
  `tests/test_sizing_route.py` and `tests/test_risk_manager_sizing_route.py`.

The first three are not wired into the trading path: turning any of them on
changes live sizing, which is a product decision, not a refactor. The fourth
is different — it is the mechanism that makes that decision without a human.

---

## Governance

`docs/PROJECT_SUCCESS_CONTRACT.md` is binding on this repository, and it
sequences the work: *no new capability until the evidence spine is honest,
and no promotion until the evidence exists.* Phase 1 (honest evidence spine)
is pending and Phase 2 (the track record) is instrumented but not collected —
0 broker-reconciled demo fills out of 200. The two modules above are therefore
built as **research capability with a governed promotion path**, not as
trading capability.

What the new modules do about the contract:

| Element | Requirement | How it is met |
|---|---|---|
| 8 | A missing dependency reads as refusal, never as no objection | `FlueyFitnessGate` approves nothing when the trial count is absent or short; `FitnessConfig.cost_r` unset refuses every verdict and reports no edge |
| 11 | Every hypothesis tested is a trial; DSR uses the true count | `record_pattern_trials` writes one hash-chained trial per pattern into `core/trial_ledger.py`; the gate refuses to approve a pattern whose hypotheses were not counted |
| 12 | Purged, embargoed train/test separation | `split_samples` drops an in-sample signal whose forward window reaches into the out-of-sample block, and an out-of-sample signal formed from in-sample bars |
| 13 | Cost before promise | Every reported edge number is net of the stated cost; with no cost stated, no number is reported at all |
| 20 | Published claims come from the tree | `tests/test_carver_map_integrity.py` resolves every file:line citation in this document and fails when one moves; it also asserts the document stays portable |
| 21 | No absolute paths in evidence | Same test; the map contains no operator paths |

What is **not** done, deliberately:

- The consolidation above is a *policy* recommendation, and the target route
  now exists (`core/sizing_spine.py`), but the live route still sizes by
  risk-per-trade: `data/market_data_provider.py:1603` feeds `risk_per_trade`
  into the live path, and `core/trading_safety.py` still owns the 1% cap.
- No FLUEY pattern has been measured on real prices, so no pattern can be
  promoted. The fitness harness needs a real OHLCV series and a stated cost
  assumption before its first verdict means anything.
- `core/forecast_sizing.py` computes positions; it does not place orders. Any
  future use must go through the single order door and the deterministic
  validator, with the 1% risk cap left where it is.

### The authority decision is autonomous

The switch between the legacy route and the forecast-driven route is not a
person's judgement and does not need a contract amendment, because **no
contract criterion is relaxed by it** — the new route is capped at the same
1% limit, cannot raise risk, and cannot place an order. What the switch
needs is *evidence*, and the contract already says what that evidence is:
200+ broker-reconciled demo fills on a frozen symbol, an honest evidence
spine, and a granted rollout approval.

`core/sizing_route.py` is that sentence as a machine check:

| Precondition (contract) | Source |
|---|---|
| 200+ broker-reconciled demo fills, admissible rows at the floor | the demo track record |
| the journal grants execution authority | the same report |
| the rollout approval is in force and its evidence has not drifted | the live rollout gate |
| no journal row without provenance | the authoritative journal |
| no artifact carrying an operator path | the artifacts tree |

All five must hold. If any is missing, stale, unreadable or unproven, the
route stays legacy — fail-closed, never a guess, never an exception in the
hot path. The evidence is read from a small snapshot
(`artifacts/sizing_route.json`) written by `refresh_route_snapshot`, so a
trade never pays for a directory scan and a stale snapshot cannot authorise
anything.

The live path reaches the new route through
`_apply_autonomous_sizing_route` (`core/risk_manager.py:1588`), and only as a
**reducer**: when the route is the forecast-driven one, the smaller of the two
risk amounts is used. So the switch can only ever make the book smaller, never
larger — the property that makes an autonomous switch safe.

---

## What I could not verify

- The book's worked example rounds its intermediates to two decimals; the
  implementation keeps full precision, so positions agree to within half a
  cent of block, not to the last decimal.
- Table 30's arithmetic multiplies price volatility expressed in *percent* by
  block value, which is 100× the cash value of the block's daily move. The
  implementation reproduces the book's published tables (the unit cancels
  inside the volatility scalar), but the underlying unit is not reconciled in
  the book.
- No claim here is backed by this repo's own data: the fitness harness is
  unit-tested on synthetic series, and no FLUEY pattern has been measured on
  real prices yet.
