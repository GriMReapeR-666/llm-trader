# AI Trading System Roadmap

> The authoritative task status and acceptance criteria are maintained in
> [TRADING_SYSTEM_MASTER_TODO.md](TRADING_SYSTEM_MASTER_TODO.md). This document
> provides the phased engineering rationale.

Engineering and research plan for a survivable, measurable MT5 trading system.
This is not financial advice and does not establish that any strategy has an
edge. The system must prove its edge empirically before risking capital.

## Current implementation status

The repository currently has the Phase 0 execution controls and the Phase 1
journal/audit foundation implemented:

- Risk percentage normalization and capped risk fraction
- Explicit paper/demo/live mode requirement
- Live-mode environment opt-in plus confirmation
- Persistent kill switch and drawdown arbiter
- Magic-number ownership isolation
- Deterministic proposal validation
- SQLite proposal/trade journal
- Macro-event/playbook/narrative memory
- Journal integrity and baseline reporting commands
- Idempotent `journal migrate` import for the legacy `trading_rewards.json` file
- Redacted `journal security-status` preflight for tracked/untracked credential
  findings and L0 trace locations
- Guarded `journal supervised-status` logistic baseline with point-in-time
  values and purged walk-forward folds; it remains sample-gated
- Claim-first completed-candle attention for restart-safe swing/scalp triggers
- Persistent expiring tiny-live approval with a content-hashed evidence
  manifest and a 0.1% risk cap; any evidence drift permanently invalidates the
  approval until explicit re-approval, and live commands fail closed
- Broker preflight completeness validation and execution-policy fingerprint
  binding; `journal broker-preflight-status` reports missing authenticated data.
  `journal policy-candidate` and `journal policy-approve` now provide a reviewed,
  fingerprint-bound calibration workflow without granting execution authority
- Per-setup research sample qualification requiring 100 labelled closes and at
  least two known regimes through `journal sample-gate-status`
- Read-only MT5 authorization classification and credential-shape diagnostics
  through `journal auth-status`; no password values are emitted
- Durable queue backpressure telemetry with explicit running/retryable-error
  thresholds through `AgentOperations.backpressure_status()`
- Fresh-quote position-management pulses now record owned-position marks and
  run protective management between candle analyses; the continuous CLI pulse
  defaults to five seconds while LLM requests remain event-gated
- Trade-manager partial-exit/pyramid state now uses the authoritative SQLite
  journal when the manager is attached, with the JSON sidecar retained only for
  standalone compatibility
- Risk cooldowns, hourly timestamps, daily start equity, and peak equity now
  restore from SQLite; cooldown/rate usage begins only after accepted orders
- Confirmation-gated legacy-record quarantine markers preserve operator/time/note
  provenance without altering unverified trade rows; broker reconciliation is
  still required before those records can be managed or used for learning

Two Phase 0 release blockers remain operational rather than algorithmic: the
credential files are now removed from the Git index but their values still
require operator rotation/secure-store migration, and the legacy reconciliation
is now a clean slate (the historical records were retired with provenance;
there are no broker-owned legacy positions or pending orders). The fresh
authoritative journal therefore has zero reconciled closed trades. HMM, GARCH,
Kelly, and RL promotion must remain disabled until the journal gates in this
document are satisfied.

## Operating principle

The LLM proposes a structured hypothesis. Deterministic code decides whether
that hypothesis is permitted. A separate portfolio arbiter decides whether the
portfolio can accept the resulting risk. No learned model may bypass those
gates.

```text
Market data -> Features/regime -> Swing/scalp proposal
             -> Deterministic validator -> Portfolio arbiter
             -> Tagged execution -> Position manager
             -> Journal -> Labels/patterns -> Offline learning
             -> Confidence adjustment only
```

## Phase 0 — Freeze unsafe execution

Before any demo or funded execution:

- Convert a human input of `1.0%` to the numeric fraction `0.01` exactly once.
- Cap risk fraction at a configured maximum (initially 1% or lower).
- Make `paper`, `demo`, and `live` explicit modes; require
  `LLM_TRADER_ALLOW_LIVE=true` plus a separate operator confirmation for live
  automated LLM mode.
- Add a persistent kill switch and verify that every execution path checks it.
- Assign a unique magic number to every strategy and reject management of unowned/manual positions.
- Keep credentials outside source control; rotate credentials that were ever committed or shared.
- Default to fail-closed when tick, calendar, DOM, symbol, or broker data is unavailable.

**Exit gate:** a test cannot place an order when the kill switch is active, risk input is malformed, the symbol is unavailable, or the account is outside limits.

## Phase 1 — Validate and repair the trading journal

The journal is the foundation for memory, statistics, and learning. Do not add
HMM, GARCH, Kelly, or RL influence before this phase is complete.

### Required records

Every proposal, including rejected proposals, must record:

- UTC timestamp and candle identifier
- Strategy (`swing` or `scalp`), symbol, magic number, and model version
- Prompt/data hash, provider, and response latency
- Full feature snapshot available at decision time
- Regime and session tags
- Macro/news events known at that time and their release status
- Proposed side, entry, stop, target, confidence, and claimed confluence
- Independent validator result and every rejection reason
- Portfolio exposure before and after the proposal
- Broker bid, ask, spread, tick age, and symbol specifications. The local
  `market_ticks` ledger retains fresh, stale, and missing/invalid quote
  observations so data outages are measurable rather than silently discarded.

Every accepted order must additionally record:

- MT5 order/deal/position tickets
- Requested and actual entry price
- Volume, contract size, point value, and calculated money risk
- Slippage, commission, swap, and fill status
- Stop/target modifications and management actions
- Exit timestamp, exit price, exit reason, gross P&L, net P&L, and R-multiple

### Journal integrity checks

Run a daily validator that checks:

1. Every accepted proposal has an order outcome or an explicit execution error.
2. Every open journal record maps to an existing MT5 position or pending order.
3. Every closed MT5 position has exactly one journal close record.
4. Ticket, magic number, symbol, side, volume, and timestamps agree across MT5 and the journal.
5. No future data appears in the recorded feature snapshot.
6. Net P&L reconciles to broker history after commission, swap, and fees.
7. Duplicate callbacks do not create duplicate trades or labels.

### Labels

Use deterministic labels first, then human review for ambiguous cases:

- `WIN_VALIDATED`: net P&L positive and at least +1R
- `WIN_SMALL`: positive but below +1R
- `LOSS_STOP`: protective stop or stop-equivalent exit
- `LOSS_TARGET`: target exit
- `LOSS_OTHER`: negative exit not attributable to the stop
- `REJECTED_VALIDATOR`: proposal failed a hard rule
- `MISSED_SIGNAL`: optional research label; never treat as realized loss

Store both the label and the reason. Never label a rejected trade as a loss in
performance statistics.

**Minimum dataset gate:** at least 100 closed, correctly reconciled trades per
strategy/setup class, spanning more than one market regime. Until then, use
fixed conservative risk and do not estimate Kelly fractions.

## Phase 2 — Market data and feature service

Build one normalized data service used by both strategies and offline replay.

### Data requirements

- Completed candles for D1/H4/H1/M30/M15/M5
- Tick age, bid/ask, spread, depth, and DOM imbalance history
- Broker symbol mapping, trade mode, point size, volume limits, stop distance, and filling modes
- Economic calendar with event time, currency, importance, forecast, previous, and actual
- News headlines with retrieval timestamp and source
- Cross-market series such as DXY, rates, VIX, and relevant commodities where available

### Feature groups

- Returns, realized volatility, ATR percentiles, skewness, and kurtosis
- RSI, MACD slope, EMA alignment, Bollinger position, ADX, and support/resistance
- Multi-timeframe direction agreement and regime duration
- Spread expansion, DOM imbalance level and rate of change, volume confirmation
- Asian-range, London-open, and New-York-open sweep features
- Currency-driver exposure and correlation buckets
- Time-to-event and post-event cooldown features

All features need a timestamp, source, lookback window, and missing-data flag.
The offline feature builder and live feature builder must share the same code.

## Phase 3 — Independent swing and scalp engines

Run two state machines against the same risk arbiter.

| Engine | Analysis | Trigger | Stop | Risk budget |
|---|---|---|---|---|
| Swing | D1/H4 structure, H1 entry | New H4/D1 close or defined breakout | Structural invalidation | Configured slice, e.g. 60% |
| Scalp | M15/M5 structure and DOM | New M5 close or defined DOM event | ATR/wick invalidation | Remaining slice, e.g. 40% |

Each engine must:

- Analyze only a completed candle unless a formally defined breakout event exists.
- Produce a structured proposal; it cannot call the order API.
- Use its own magic number and setup namespace.
- Submit to the same validator and portfolio arbiter.
- Persist its last processed candle so restarts do not duplicate decisions.

## Phase 4 — Deterministic validator and portfolio arbiter

The validator must be executable without the LLM and replayable against the
journal:

```text
approve = market_open
       and fresh_tick
       and symbol_tradable
       and spread <= symbol_limit
       and completed_candle_or_defined_event
       and stop_distance >= broker_minimum
       and reward_risk >= 2.0
       and independent_confluence >= threshold
       and no_event_blackout
       and not_duplicate_setup
       and kill_switch_off
```

The independent confluence score must be recomputed from code. It should not
trust the LLM's claimed factors.

The portfolio arbiter must enforce:

- Sum of open R-risk below a hard portfolio ceiling
- Separate swing/scalp budgets
- Correlation-adjusted currency and driver buckets
- Daily loss limit, weekly loss limit, and maximum drawdown kill switch
- Maximum symbol and total exposure
- Optional CVaR check after a sufficiently large return sample exists

All limits must be persisted transactionally and survive restart.

## Phase 5 — Execution and position management

- Preflight every symbol and broker specification at startup.
- Prefer a limit entry at the proposed price with an expiry/cancel timeout.
- If a breakout stop is intended, require a defined breakout event and use a tagged stop order.
- Reject plans whose current price is outside the allowed entry tolerance; never silently chase with a market order.
- Select a broker-supported filling mode rather than assuming FOK.
- Record the order before and after submission with an idempotency key.
- Reconcile fills, partial fills, rejects, cancellations, and broker-side SL/TP exits.
- Position manager may trail, move to breakeven, or scale out only for its own magic number.
- Add time exits, weekend policy, and emergency close policy as deterministic rules.

## Phase 6 — Short-term and long-term memory

### Short-term memory

Maintain a bounded current-session object containing:

- Today's playbook and allowed/no-trade windows
- Current regime and regime confidence
- Key levels and active setup keys
- Recent proposals and validator decisions
- Open positions and current portfolio risk
- Upcoming events and required post-event waiting periods

Summarize old context instead of silently dropping it. Never put secrets or
unverified model claims in the durable memory.

### Long-term memory

Use a structured SQLite/Postgres journal as the source of truth. Add a narrative
store only as an index over verified journal facts. Once per session, generate a
compact narrative tagged by symbol, strategy, regime, session, event state, and
setup. Retrieve the most similar narratives by those tags on future proposals.

Examples of useful retrieved lessons:

```text
GBPJPY / scalp / London / ADX<20 / breakout:
6 losses out of 8, median -0.9R — avoid unless range sweep and close confirmation exist.
```

The narrative may inform the LLM and confidence adjustment, but never bypass a
hard validator.

## Phase 7 — Regime research: HMM

Implement HMM offline first to classify regimes such as trend-up, trend-down,
range, high-volatility, and low-volatility.

Steps:

1. Build leakage-free returns/volatility feature sets.
2. Fit candidate state counts on the training segment only.
3. Check state stability across walk-forward windows.
4. Name states by their out-of-sample characteristics, not by visual preference.
5. Measure transition lag, false transitions, and regime-specific expectancy.
6. Compare against a simple volatility/trend rule baseline.
7. Use HMM only to adjust permitted risk or strategy preference after promotion.

**Promotion gate:** stable states across multiple windows, no material look-ahead
leakage, and out-of-sample improvement after costs. HMM may never authorize an
otherwise invalid trade.

## Phase 8 — Volatility research: GARCH and targeting

Use GARCH/EGARCH or a simpler rolling-volatility baseline to estimate volatility.

```text
risk_amount = equity * base_risk_fraction * volatility_target / forecast_volatility
lots = risk_amount / (stop_distance * point_value)
```

Requirements:

- Compare forecast accuracy to rolling ATR and realized-volatility baselines.
- Cap both upward and downward sizing adjustments.
- Apply a high-volatility reduction before considering any Kelly multiplier.
- Include spread, gaps, news shocks, and fat-tail stress scenarios.
- Never widen a stop solely to preserve a target position size.

**Promotion gate:** meaningful out-of-sample volatility forecast improvement and
no increase in tail loss or drawdown in walk-forward tests.

## Phase 9 — Kelly sizing, only after evidence

Kelly is not a prediction engine. It converts an already-estimated edge into a
size and is highly sensitive to estimation error.

```text
f* = win_rate - (1 - win_rate) / payoff_ratio
live_fraction = max(0, min(cap, 0.25 * f* * regime_multiplier))
```

Rules:

- Require at least 100 closed trades per setup class, preferably more.
- Use net P&L after spread, commission, swap, and realistic slippage.
- Use Bayesian shrinkage for win rate and payoff ratio at small samples.
- Use quarter-Kelly or lower initially; never full Kelly.
- Re-estimate only in scheduled offline batches, not after every win/loss.
- If uncertainty widens or the estimate becomes negative, revert to fixed minimum risk.

**Promotion gate:** Kelly sizing must reduce or improve drawdown-adjusted results
out-of-sample; higher nominal return alone is not sufficient.

## Phase 10 — Learning sequence: supervised model, bandit, then RL

### Supervised baseline

Train a simple logistic regression or gradient-boosted model to estimate
`P(net_positive | setup_features)`. Compare it with:

- Random direction
- 50-period EMA crossover
- Fixed confluence rule
- Raw LLM proposal without learned adjustment

Use purged/embargoed walk-forward splits so overlapping trade labels cannot leak.

### Contextual bandit

Use a bandit for “take/reject this already validated setup” or a small size
adjustment. Thompson sampling is appropriate for early experimentation. It is
more data-efficient than full RL for independent entry decisions.

### RL for position management

Reserve RL for sequential decisions where state actually evolves:

- Hold, trail, breakeven, partial close, or full close
- State includes position, unrealized R, time-in-trade, volatility, spread, and event proximity
- Reward includes net P&L, drawdown penalty, transaction costs, and tail-risk penalty

Use offline replay first. Never explore randomly with live capital. A promoted RL
policy may adjust a deterministic management preference, but cannot remove SLs,
exceed risk limits, or manage another strategy's positions. The implementation
also requires the centralized `research_influence_gate.py` approval: a 100+
labelled sample, purged/OOS evidence, a bound evidence digest, and expiring
operator approval before HMM/GARCH/Kelly/RL output can influence demo/live
proposals; paper mode is research-only.

## Phase 11 — Validation and promotion gates

Every candidate must pass:

1. Unit tests for feature calculations, sizing, validator, and reconciliation.
2. Historical replay with realistic spread, commission, swap, slippage, and gaps.
3. Chronological train/validation/test separation.
4. Walk-forward testing across multiple non-overlapping market regimes.
5. Purged/embargoed cross-validation where labels overlap.
6. Monte Carlo trade-sequence and execution-cost stress tests.
7. Parameter perturbation and symbol portability checks.
8. Comparison with simple baselines.
9. Demo forward test of at least 100 closed trades per strategy/setup class.
10. Manual review of journal integrity and worst-case trades.

Suggested research thresholds are guides, not guarantees: positive net
expectancy, profit factor above 1.2 after costs, acceptable drawdown, and no
severe degradation between backtest and forward test. No single Sharpe threshold
proves safety.

## Phase 12 — Monitoring and review

Every session should expose:

- Connection, data freshness, calendar, and broker status
- Current equity, daily/weekly/max drawdown, and open R-risk
- Exposure by symbol, currency, driver, and strategy
- Proposal count, approval/rejection reasons, and execution quality
- Closed-trade labels, expectancy, R distribution, and regime breakdown
- Model version, memory version, and learning-policy version
- Kill-switch state and reason

Run a daily journal reconciliation and playbook generation job. Run weekly
research reports. Retrain only on schedule, with versioned artifacts and a
rollback path. Health reports are persisted through the local, transport-neutral
`alert_outbox` (enqueue/claim/ack/retry/recovery); external notification is
operator-owned and never an order-permission path.

## Promotion order

| Stage | Deliverable | Must be true before promotion |
|---|---|---|
| 0 | Risk normalization and kill switch | Unsafe order paths blocked |
| 1 | SQLite journal and MT5 reconciliation | Facts match broker history |
| 2 | Deterministic validator/arbiter | Invalid proposals cannot execute |
| 3 | Swing/scalp engines | Independent triggers share risk limits |
| 4 | Short/long memory and playbook | Context survives restart and is auditable |
| 5 | Offline replay/baselines | Edge is measured after realistic costs |
| 6 | HMM regime adjustment | Stable out-of-sample regime value |
| 7 | GARCH volatility targeting | Better risk-adjusted tail behavior |
| 8 | Fractional Kelly sizing | Large labelled sample and safer drawdown |
| 9 | Supervised/bandit adjustment | Beats baselines out-of-sample |
| 10 | RL position management | Offline replay, demo validation, deterministic safety intact |
| 11 | Live pilot | Manual approval, tiny allocation, continuous monitoring |

If a stage fails, revert to the last promoted stage. Do not compensate for a
negative edge by adding more model complexity or increasing frequency.
