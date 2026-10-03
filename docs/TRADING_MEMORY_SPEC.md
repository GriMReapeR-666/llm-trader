# Trading Memory and Market-Data Specification

> Task status and acceptance evidence live in
> [TRADING_SYSTEM_MASTER_TODO.md](TRADING_SYSTEM_MASTER_TODO.md).

This specification defines how the system records, labels, retrieves, and
learns from trading situations. Memory is evidence and context; it is not an
order-permission mechanism. The SQLite/Postgres journal remains authoritative.

## 1. Memory layers

| Layer | Lifetime | Contents | Allowed consumer |
|---|---|---|---|
| Tick/candle cache | minutes to days | Raw OHLCV, ticks, spread, DOM | Feature builder |
| Cycle snapshot | one proposal | All features and data-quality flags | LLM and validator |
| Trade journal | permanent | Proposal, execution, management, close, costs | Audit and research |
| Event memory | permanent | Macro event, surprise, reaction windows | Regime/event analysis |
| Situation memory | permanent | Similar historical setup summaries | LLM context |
| Core playbook | versioned | Stable, repeatedly validated rules | Proposal context only |

Semantic memory must never be the only copy of an order, P&L, risk, or broker
event. Every memory item must include a source journal ID and timestamp.

## 1A. Complete layered memory hierarchy

The trading agent should implement every layer below. Higher layers are
compressed conclusions; lower layers remain the evidence used to verify them.

### L0 — Raw conversation and execution trace

Immutable, append-only source material:

- original user/operator instructions
- original LLM prompt and response
- tool calls and returned market data
- MT5 requests, responses, retcodes and broker errors
- validator decisions and rejection reasons
- order-management actions and operator overrides
- macro-news payloads and retrieval timestamps

L0 is not injected wholesale into prompts. It is retained for audit, replay,
dispute resolution, and rebuilding higher memory layers.

### L1 — Atomic memory

Small, independently retrievable facts. Each item must contain:

```text
fact_id, type, content, confidence, valid_from, valid_to,
source_ids, symbol/currency, strategy, regime, created_at, supersedes
```

Trading examples:

- `EURUSD H1 trend was bearish at 2026-08-05 08:00 UTC`
- `US CPI release occurred at 12:30 UTC`
- `GBPJPY setup 1842 closed LOSS_STOP at -1.0R`
- `Spread exceeded the configured limit during NY open`
- `ADX was below 20 when this breakout failed`
- `Account maximum daily loss state was active`

Atomic memory is retrieved frequently, but the prompt must receive only a
small filtered set. Facts must be marked as observed, inferred, or proposed.
Only observed facts may be used as evidence.

### L2 — Scenario memory

Longer documents grouped by a repeatable situation. Use one scenario document
per symbol/setup/regime/session combination, for example:

```text
scenarios/
  EURUSD/
    london_asian_sweep_range.md
    london_breakout_high_adx.md
    cpi_release_reaction.md
  XAUUSD/
    ny_open_real_yield_shock.md
```

Each scenario should contain:

- situation definition and eligibility conditions
- representative winning, losing, rejected, and unresolved examples
- distribution of R-multiples and costs
- indicator ranges, volatility, spread, and event context
- what invalidated the setup
- last validation date and sample size
- links to L1 facts and L0 evidence
- version history and contradictory evidence

Scenario memory is a research aid. It must not be treated as proof of future
profitability.

### L3 — Core memory and personality

This is the stable identity and operating policy of the agent. It changes only
through a reviewed, versioned update—not after a single trade.

#### Trading personality

- conservative, evidence-first, and uncertainty-aware
- never claims a trade is certain or guaranteed
- prefers no trade to low-quality or incomplete data
- explicitly separates observation, inference, and hypothesis
- explains rejection reasons in plain language
- does not chase price or revenge-trade after losses

#### Risk personality

- capital preservation is the primary objective
- deterministic limits outrank model confidence
- fixed hard caps on per-trade, portfolio, daily, weekly, and drawdown risk
- no Kelly, HMM, GARCH, bandit, or RL influence before promotion gates
- no increase in size to recover a loss

#### Research personality

- records rejected signals, not only executed trades
- seeks disconfirming examples
- compares against simple baselines
- treats small samples as uncertain
- retires memories when out-of-sample evidence contradicts them

#### Communication personality

- concise trade thesis, invalidation, evidence, and uncertainty
- always reports data age and missing-data flags
- never presents retrieved memory as a live market fact without timestamp

Core memory should include the current risk policy, system mode, approved
strategies, account scope, and model-promotion status. It must not contain
mutable live P&L; that belongs in the journal.

### Short-term working memory

This is the current task/session state, not long-term learning:

- current objective and cycle ID
- latest completed candle IDs
- current market snapshot and data age
- active playbook and event windows
- proposals considered in the current cooldown window
- open positions and pending orders owned by this agent
- current exposure and kill-switch status
- last few tool results and unresolved errors

When the context grows, compress it into a structured task graph while keeping
the raw trace in L0. Never silently drop a risk decision or unresolved error.

### Task graph / execution canvas

Represent long-running work as explicit nodes and dependencies:

```text
session_start
  -> fetch_market_data
  -> fetch_calendar
  -> build_features
  -> retrieve_similar_situations
  -> propose_setup
  -> deterministic_validation
  -> portfolio_permission
  -> execution
  -> position_management
  -> broker_reconciliation
  -> label_trade
  -> write_situation_memory
```

Each node has status, start/end time, inputs, outputs, retry count, and error.
This makes a restart resumable and prevents the agent from assuming a failed
step succeeded.

### Persona, preference, and policy memory

Keep these separate because they have different authority:

- **Persona:** how the agent behaves and communicates
- **Preferences:** user-selected pairs, sessions, risk display, and interface
- **Policies:** hard safety and compliance rules
- **Objectives:** current research or trading objective
- **Permissions:** demo/live mode, allowed symbols, allowed magic numbers

Policies and permissions must be stored in signed/versioned configuration and
checked by code. They cannot be changed by an LLM-generated memory.

## 1B. Reusable memory assets

Treat reusable knowledge as versioned local assets. Map those assets to the
trader as follows:

### Chat Memory

Conversation and operator-decision history. Store only cleaned original user
and assistant messages; do not re-save recalled memory inside a new message.

### Skill Memory

Versioned procedures that have passed testing, for example:

- `calculate_position_size_v3`
- `validate_london_sweep_v2`
- `reconcile_mt5_history_v1`
- `label_closed_trade_v1`

Each skill needs inputs, deterministic outputs, tests, owner, version, and
promotion status. A skill is not learned merely because it appears in a trade
narrative.

### Trading Wiki

Structured reference pages:

- broker symbol specifications
- economic-event definitions
- strategy documentation
- indicator definitions and lookbacks
- known failure modes
- risk and operational procedures

Wiki pages are versioned and cited by proposals; they are not mixed with
unverified memories.

### Code Graph

Index the code relationships among market-data adapters, feature builders,
validators, risk arbiter, execution, journal, and learning jobs. Use it to
answer “what changes if this field or rule changes?” It must never be used as a
substitute for runtime tests.

### Team/agent loadout and access control

Keep separate scopes for:

- swing agent
- scalp agent
- research/backtest agent
- monitoring agent
- operator/admin

The execution agents may read approved playbooks but should not write core risk
policies. The research agent may create candidate scenarios, but only a review
process may promote them to core memory. Private account data must not be
shared between accounts.

## 2. Data recorded for every proposal

Store rejected proposals as well as accepted ones.

### Identity and lineage

- UTC timestamp, symbol, strategy, timeframe, candle open/close time
- strategy version, feature-builder version, model/provider/model version
- prompt hash, response hash, data snapshot hash, code commit hash
- session (`Asia`, `London`, `New York`, overlap), account and magic number

### Market state

- bid, ask, mid, spread, tick age, tick count, volume and liquidity flags
- OHLCV for D1, H4, H1, M30, M15, M5 and the trigger timeframe
- broker symbol specification: point, digits, contract size, minimum stop,
  lot step, minimum/maximum volume, trade mode and filling mode
- trading-day and session boundaries, weekend/rollover flags

### Technical and statistical features

- returns over 1, 3, 5, 10, 20 and 50 bars
- ATR and ATR percentile relative to a rolling 90-day distribution
- realized volatility, volatility percentile, skewness and kurtosis
- EMA/SMA alignment and slopes, RSI, MACD line/signal/histogram slope
- Bollinger position and bandwidth, ADX/+DI/-DI
- swing highs/lows, support/resistance distance, round-number distance
- multi-timeframe direction agreement and regime duration
- candle body/wick ratios, gap, breakout/rejection and close-confirmation flags
- volume percentile, volume spike, VWAP distance where available

### Microstructure and liquidity

- bid/ask spread expansion and rolling spread percentile
- tick direction imbalance and its rate of change
- DOM bid volume, ask volume, imbalance, imbalance change and data age
- equal highs/lows, Asian range, London/NY sweep state
- distance to recent liquidity pools and prior-day high/low
- slippage estimate and execution-price tolerance

### Cross-market and fundamental state

- DXY, rates/yields, VIX, gold, oil and correlated-pair returns
- currency-driver exposure and correlation bucket
- event ID, currency, importance, forecast, previous, actual and surprise
- minutes to event, minutes since event, event-release status and news-source
- missing-data flags; unknown data is never represented as a safe value

### Proposal fields

- side, entry, stop, target, expiry, confidence and claimed confluence
- deterministic confluence score and every validator decision
- risk fraction, money risk, calculated volume and reward/risk
- portfolio exposure before/after, correlated-bucket exposure and kill-switch
  state

## 2A. Complete trading-operations ledger

The system should record every operation below as an append-only event with
`event_id`, `event_time_utc`, `account_id`, `strategy`, `symbol`, `source`,
`correlation_id`, payload hash, and software version.

### Account and broker operations

- terminal start/stop, connection attempts, login result, server and build
- account login, account type, currency, leverage, balance, equity, margin,
  free margin, margin level, credit, and stop-out level
- authorization failures, terminal path, network latency, reconnect count
- broker trading sessions, market-open state, holidays and rollover window
- symbol discovery, suffix mapping, visibility, trade mode and specification
  changes
- deposit, withdrawal, balance correction, swap adjustment and broker fee

### Market-data operations

- candle request parameters, returned count, first/last timestamp and gaps
- tick arrival time, broker time, local receive time and latency
- bid/ask/mid, spread, spread percentile and spread expansion
- tick volume, real volume, quote count and missing-bar count
- DOM snapshot, depth, imbalance and imbalance rate of change
- calendar/news request, provider, retrieval latency, event revision and data
  quality status
- stale, conflicting, delayed, missing, or out-of-order data events

### Feature and regime operations

- feature-builder input snapshot and output hash
- indicator values, lookbacks, warm-up status and NaN/missing flags
- regime classification, probabilities, transition, duration and confidence
- volatility forecast, realized volatility, ATR percentile and stress state
- session state, liquidity sweep state, event proximity and cooldown state
- cross-market returns, correlations, currency-driver exposure and hedge state

### Decision operations

- every LLM/provider request, model, prompt hash, response hash, token usage,
  latency, timeout, retry and parse error
- proposed direction, entry, stop, target, expiry, confidence and rationale
- independent confluence components and score
- deterministic validator result and every failed rule
- portfolio arbiter result, exposure before/after, risk budget and kill-switch
- duplicate/cooldown decision and reason a setup was ignored or revisited

### Order-routing operations

- order intent, idempotency key, magic number and parent/child relationship
- requested order type, entry tolerance, volume, price, stop, target,
  expiration and filling policy
- request send time, broker retcode, server response, retry and cancellation
- rejected, requoted, partial, accepted, filled, expired and manually changed
  states
- requested versus accepted versus filled volume and price
- slippage, spread at submission/fill, queue delay and execution latency

### Position-management operations

- position opened, resized, hedged, netted, partially closed or transferred
- stop/target creation, modification, rejection and distance from price
- break-even move, trailing-stop calculation, scale-in and scale-out
- time stop, volatility stop, event reduction, weekend close and emergency exit
- operator intervention and reason for every override
- position ownership check and ignored unmanaged/manual position

### Close and reconciliation operations

- broker deal history request and reconciliation window
- entry/exit deals, commissions, swaps, financing, taxes and rebates
- gross P&L, net P&L, realized R, close reason and broker statement match
- unresolved/vanished position, late history, duplicate deal and correction
- journal-versus-MT5 differences and resolution status

### Risk and safety operations

- pre-trade, open-trade and correlated-bucket R-at-risk
- margin usage, liquidation distance and concentration
- daily, weekly and monthly loss counters
- equity peak, drawdown, recovery, volatility stress and exposure breach
- kill-switch trip, persistence, reason, operator reset and reset approval
- credential/configuration change, live-mode confirmation and emergency stop

### System-health operations

- process heartbeat, CPU/memory/disk, queue depth and database latency
- LLM/API availability, rate-limit and cost usage
- MT5 terminal health, order-manager health and journal write failures
- clock drift, timezone conversion errors and restart recovery status
- backup/export success, schema migration and data-retention actions

## 3. Execution and lifecycle records

Record each state transition, not only the final order:

```text
PROPOSED -> VALIDATED/REJECTED -> SUBMITTED -> FILLED/PARTIAL/CANCELLED
         -> MANAGED -> CLOSED/EXPIRED/ERROR
```

For each transition record:

- MT5 order, deal and position tickets
- requested versus actual price, volume and fill time
- slippage in points and money, spread at fill, commission and swap
- stop/target changes, partial closes, trailing actions and operator actions
- close price/time, exit reason, gross P&L, net P&L, MFE, MAE and holding time
- planned R, realized R, outcome label and reconciliation status

Never infer a close from a missing position alone. Confirm it through MT5
history; otherwise keep the record in `UNRESOLVED` state for retry.

## 4. Deterministic labels

Labels must be produced from recorded facts, not an LLM opinion:

- `WIN_TARGET`, `WIN_VALIDATED`, `WIN_SMALL`
- `LOSS_STOP`, `LOSS_TARGET`, `LOSS_OTHER`
- `BREAKEVEN`, `CANCELLED`, `REJECTED_VALIDATOR`, `UNRESOLVED`

Each label stores its rule, reviewer status, and R-multiple. A rejected or
missed proposal is never counted as a realized loss.

## 5. Derived metrics

### Trade-level

- R-multiple, expectancy contribution, net cost in R
- MFE/MAE, time to MFE/MAE, adverse excursion before recovery
- planned versus actual entry/stop/target deviation
- stop distance in ATR, target distance in ATR, spread/ATR ratio
- fill probability, cancellation rate, slippage distribution

### Setup and regime-level

- sample count, win rate, average win/loss, expectancy, profit factor
- median and percentile R, Sharpe/Sortino, max drawdown and recovery time
- performance by symbol, side, timeframe, session, regime, event class,
  volatility bucket, spread bucket and setup version
- calibration: predicted confidence versus realized win frequency
- false-positive rate, rejection rate and missed-signal rate

### Portfolio-level

- gross/net currency exposure and correlation-adjusted R-at-risk
- daily/weekly/monthly P&L, equity peak, drawdown and kill-switch events
- CVaR only after sufficient history; never use it as a substitute for a hard
  exposure limit

## 2B. Complete result and quality metrics

### Signal quality

- proposal count, approval rate, rejection rate by rule and missed-signal rate
- confidence calibration, Brier score, log loss and probability buckets
- directional accuracy, precision, recall and baseline lift
- expectancy of raw proposals versus validated proposals
- edge decay by model version, prompt version and data provider

### Execution quality

- fill rate, cancellation rate, rejection rate and partial-fill rate
- average/median/p95 decision, network, queue, broker and fill latency
- signed slippage, adverse selection, spread cost and implementation shortfall
- requested-versus-filled entry/stop/target and price-chasing rate
- order-type performance and broker/venue comparison

### Trade and setup quality

- win rate, loss rate, average win/loss, payoff ratio and expectancy in R
- profit factor, geometric growth, median R and R percentiles
- MFE, MAE, capture ratio, recovery after adverse excursion
- hold-time distribution, time-to-target/stop and time-exit frequency
- result by symbol, side, strategy, setup, timeframe, session, regime,
  volatility, spread, event type, weekday and model version

### Equity and portfolio quality

- cumulative net P&L, equity curve, CAGR and monthly return distribution
- maximum/average drawdown, drawdown duration and recovery factor
- Sharpe, Sortino, Calmar, Omega and downside deviation
- tail loss, expected shortfall/CVaR, worst day/week/month
- exposure concentration, correlation-adjusted risk and turnover
- risk-of-ruin simulations under realistic costs and slippage

### Operational quality

- journal completeness, reconciliation rate and unresolved-ticket age
- percentage of trades with complete feature snapshots and labels
- duplicate-event rate, missing-data rate and stale-data rejection rate
- memory retrieval hit rate, contradiction rate, freshness and latency
- model drift, feature drift, regime coverage and backtest/live degradation
- uptime, restart recovery time, backup recovery test and incident count

Every metric must carry its sample size, time range, cost model, strategy
version, and whether it is in-sample, validation, out-of-sample, demo, or live.
Never display a Sharpe ratio or win rate without those qualifiers.

## 6. Situation memory

After a trade closes, generate a compact situation document:

```json
{
  "situation_id": "sha256:...",
  "source_ticket": 123456,
  "symbol": "EURUSD",
  "strategy": "scalp",
  "regime": "range_low_adx",
  "session": "london",
  "event_context": "CPI_30m_before",
  "setup": "asian_range_sweep",
  "features": {"adx": 17.2, "atr_percentile": 81, "spread_atr": 0.12},
  "label": "LOSS_STOP",
  "r_multiple": -1.0,
  "explanation": "Sweep rejected but confirmation close was absent.",
  "source": "trading_state.db"
}
```

Retrieve by structured filters first, then semantic similarity:

1. Same symbol or currency driver
2. Same strategy and timeframe
3. Same session and regime
4. Similar volatility, spread and event state
5. Vector/keyword similarity within the filtered set

Return a small balanced sample: winning, losing, rejected, and unresolved
examples. Returning only winners creates confirmation bias.

## 7. Macro-event memory

For every event, store forecast, previous, actual, surprise, release timestamp,
affected currencies, pre-event volatility, and reaction at 1m/5m/15m/1h/4h.
Build event-pattern records such as:

```text
US CPI upside surprise -> USD strength, gold drawdown, spread expansion,
high stop-out rate for entries within 10 minutes of release.
```

The playbook should use these records to define no-trade windows and reduced
risk. It must not predict an event reaction as a certainty.

## 8. Learning loop

1. Validate journal integrity and reconcile broker history.
2. Label closed trades deterministically.
3. Build point-in-time feature snapshots; prohibit future fields.
4. Split chronologically with walk-forward and purged/embargoed folds.
5. Compare against random and simple EMA/trend baselines after costs.
6. Train supervised probability models first.
7. Use contextual bandits only after adequate labelled samples.
8. Reserve RL for position management, not unrestricted entry decisions.
9. Allow a promoted model to adjust confidence or size only within hard caps.
10. Version every model and monitor live-versus-backtest drift.

Minimum gate: 100 correctly labelled closed trades per setup class and regime
coverage before estimating Kelly or allowing learned influence. More data is
required for high-dimensional models and RL. The runtime implementation
enforces this through `research_influence_gate.py`: demo/live influence also
requires qualifying purged/OOS evidence, a matching evidence digest, and
expiring operator approval; paper mode remains research-only.

## 9. Local LLM memory integration

Keep every raw journal row, feature snapshot, and lifecycle event in local
SQLite/Postgres. The same LLM provider configured for the trader may distil
only verified, source-linked records from the local memory outbox into compact
situation narratives and candidate playbook lessons. No external or other
memory server is required.

Memory distillation runs under its own conservative request budget, outside the
order path. It writes locally to `trading_memory.py`; a provider outage leaves
the outbox queued for retry and never changes risk/execution. Retrieval happens
before an LLM proposal, never inside the deterministic validator.

## 10. Quality controls

- Reject records with missing timestamps, symbol, side, or source ID.
- Hash raw payloads so later edits are detectable.
- Keep UTC everywhere and retain the original broker timestamp.
- Separate paper, demo, live, and backtest environments.
- Redact credentials and personal data before cloud upload.
- Never let retrieved memory directly issue an order.
- Delete or supersede stale memories; preserve the original evidence.
- Monitor retrieval hit rate, contradictory memories, latency and failures.

Powerful memory is not “more text.” It is point-in-time data, verified labels,
source links, balanced retrieval, and a promotion process that prevents the
system from learning false patterns.
