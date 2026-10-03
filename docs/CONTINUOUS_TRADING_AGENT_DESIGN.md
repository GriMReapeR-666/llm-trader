# Continuous Trading Agent Operating Design

> Task status and acceptance evidence live in
> [TRADING_SYSTEM_MASTER_TODO.md](TRADING_SYSTEM_MASTER_TODO.md).

This design makes the trader persistent, observable, and repeatable. It does
not make a strategy profitable. The LLM proposes hypotheses; deterministic
services own safety, scheduling, risk, execution, and auditability.

## 1. Non-negotiable control flow

```text
Scheduler event
  -> restore durable state
  -> collect timestamped market/event/account data
  -> update features and regime state
  -> retrieve compact relevant memory
  -> use a versioned playbook and LLM proposal template
  -> deterministic validation
  -> portfolio risk arbitration
  -> idempotent MT5 order execution
  -> position monitoring/reconciliation
  -> append journal events
  -> write verified memory at defined boundaries
```

The validator and risk arbiter have no LLM dependency and must fail closed.
Memory can explain or adjust an LLM hypothesis, but it can never place an order
or override a hard rule.

## 2. Persistent agent state

Store the following in SQLite/Postgres, never only in RAM or a chat buffer:

- `agent_run_id`, process heartbeat, code version and configuration hash
- current mode: paper/demo/live; allowed symbols and strategy versions
- per-strategy state: `IDLE`, `WAIT_CANDLE`, `ANALYSING`, `PROPOSED`,
  `COOLDOWN`, `POSITION_OPEN`, `HALTED`, `ERROR`
- last processed completed-candle ID per symbol/timeframe/strategy
- last market-data timestamp, calendar timestamp, DOM timestamp and quality
- current session, daily playbook ID, event-window state and cooldowns
- daily/weekly counters, equity peak, realized loss, open risk and kill switch
- active order intents, MT5 tickets, reconciliation status and retries
- LLM budget counters, request IDs, provider status and retry backoff
- memory write queue, memory retrieval version and narrative generation status

On restart, restore state, reconcile all agent-owned MT5 orders/positions, and
resume from the last completed candle. Do not create a new trade merely because
the process restarted.

## 3. Event-driven attention schedule

Do not analyse every 60 seconds indiscriminately. Wake the appropriate agent
only on a meaningful event.

| Agent | Wake trigger | Typical work |
|---|---|---|
| Market-data monitor | Tick/candle arrival, stale-data alert | Update cache and health |
| Swing agent | New D1/H4/H1 completed candle | Structure and swing setup review |
| Scalp agent | New M5/M15 completed candle or defined breakout | Short-horizon setup review |
| Event agent | Scheduled pre-event, release, post-event windows | Calendar state and no-trade rules |
| Position agent | Tick, timer, SL/TP/deal change | Manage only owned positions |
| Reconciler | At start, on deal change, periodic safety interval | Match journal and MT5 history |
| Daily-review agent | Once after session close | Narrative, labels, metrics and playbook |
| Research agent | Offline scheduled job only | Validation and candidate-model training |

All triggers receive a deduplication key such as:

```text
strategy:symbol:timeframe:completed_candle_time:playbook_version
```

The database records the key before work begins. A duplicate event returns the
previous result instead of repeating an LLM call or order request.

## 4. What to journal in real time

Each record is append-only, UTC timestamped, schema-versioned, and linked by
`run_id`, `cycle_id`, `proposal_id`, `order_intent_id`, and `ticket`.

### Session journal

- session start/end, trading date, mode, account fingerprint, configuration
  hash, code commit and enabled strategies
- opening account balance/equity/margin and current kill-switch state
- daily playbook version, active symbols, active event windows and risk budget
- every configuration, model, prompt, provider, or operator change

### Data journal

- raw-data source, retrieval time, exchange/broker time, latency and hash
- completed candle IDs and OHLCV data used for a decision
- bid/ask/mid/spread/tick age/volume/DOM data and missing-data flags
- calendar/news data: source, event ID, forecast/previous/actual/surprise,
  release status and time-to-event
- feature snapshot hash plus all feature values and feature-builder version

### Decision journal

- strategy trigger and idempotency key
- retrieved memory IDs and playbook ID included in the prompt
- LLM request ID, provider/model, prompt hash, latency, token/cost estimate,
  raw response hash, parse result and retry count
- proposed side, entry, stop, target, validity period, confidence and rationale
- deterministic confluence values, validator rules evaluated and outcomes
- risk-arbiter inputs/outcome: risk, exposure, correlation bucket, margin,
  drawdown, daily/weekly limits and kill-switch result
- explicit reason for `NO_TRADE`, rejection, deferral, or execution error

### Order and position journal

- immutable order intent before sending to MT5
- requested order parameters, magic number, tolerance, expiry and idempotency
  key
- MT5 request/response/retcode, order/deal/position ticket and fill timestamps
- requested versus actual price/volume, slippage, spread, commission and swap
- every stop/target/trailing/partial-close change and its deterministic reason
- MFE, MAE, unrealized R, holding time and position-risk snapshots
- close deal data, exit reason, gross/net P&L, realized R and reconciliation
  result

### Operational journal

- terminal connection/authentication, retry, reconnection and broker errors
- database write health, backup result, memory-service health and API failures
- data staleness, calendar outage, DOM outage and all fail-closed decisions
- heartbeat, process restart, clock drift, queue backlog and alerts
- manual intervention, manual order ignored, kill-switch enable/reset and actor

## 5. Versioned plan and repeatable behaviour

The agent should not regenerate its trading philosophy each cycle. Use immutable
versions of these artifacts:

```text
RiskPolicy vN        hard caps, loss limits, ownership, emergency actions
StrategySpec vN      entry eligibility, trigger, stop and exit logic
FeatureSpec vN       inputs, lookbacks, timestamps and missing-data treatment
Playbook vN          daily levels, event windows, allowed setups, no-trade rules
PromptTemplate vN    exact LLM request/output schema
ModelVersion vN      provider/model/temperature/response parser
```

Every proposal references all six versions. A new plan is a candidate until
offline tested and operator-approved. It cannot silently replace a live plan.

### Daily plan lifecycle

1. Before the intended session, collect completed higher-timeframe data and
   calendar events.
2. Generate a candidate daily playbook with key levels, allowed setups,
   no-trade windows and uncertainty notes.
3. Validate it against risk policy and current data; persist a version.
4. During the session, agents only test whether current price action matches
   that playbook.
5. Material changes require a new version with a reason; old versions remain
   queryable for later analysis.
6. After the session, compare plan, actions and outcomes; write a narrative,
   but do not promote a lesson to core policy from one day.

## 6. LLM request-rate and attention budget

An LLM call is expensive and non-deterministic. Treat it as a budgeted service.

### Budget hierarchy

- Global minute/hour/day request and token ceilings
- Per-provider rate limit, concurrency limit and exponential backoff
- Per-strategy budget: swing calls are low frequency; scalp calls are capped
- Per-symbol cooldown after a rejected/unchanged setup
- Per-event budget: use one pre-event and one post-event assessment, not a
  burst of repeated calls
- Reserve a small emergency budget for position explanation only; it cannot
  manage risk

### Request rules

- Request only after a completed candle or precisely defined breakout event.
- Cache one response by trigger idempotency key.
- Reuse a proposal while its data hash, playbook version and candle ID match.
- Cancel queued analysis if data becomes stale, an event window begins, or the
  kill switch is active.
- Use strict structured output; invalid JSON becomes `NO_TRADE`.
- Use provider failover only for analysis, never for the deterministic path.
- Record every request, response, timeout, retry, tokens and estimated cost.

Example policy:

```text
swing: 1 call per new H1 close per symbol
scalp: 1 call per new M15 close per symbol; max 4 per hour per symbol
event: one T-30 and one T+15 assessment; no entry during protected window
same setup: no repeat call for 15 minutes unless a new completed trigger exists
```

Actual limits must be set from the chosen provider’s current quota and observed
latency, and retained as versioned configuration.

## 7. Memory read/write policy

### Read before a proposal

Retrieve only compact, timestamped, verified items:

- daily playbook and risk mode
- similar scenarios filtered by symbol/driver, strategy, session, regime,
  volatility bucket and event state
- relevant macro-event reaction summaries
- recurring validated failure patterns and data-quality warnings

Return a bounded balanced set: examples of wins, losses, rejections, and
unresolved outcomes. Include sample counts and age. A memory item without a
source ID is not used as evidence.

### Write after verified boundaries

- On a closed/reconciled trade: write a structured outcome and deterministic
  label.
- After an economic release: write event surprise and measured reaction.
- End of session: write one compact narrative tied to the day’s playbook.
- Weekly: aggregate stable patterns, mark contradictions and create candidates
  for review.
- Only after validation/review: promote a candidate to Core Memory/Playbook.

Never write an LLM’s unverified prediction as a fact. Never re-ingest retrieved
memory into itself; retain L0 source traces and write only clean new evidence.

## 8. Multi-agent responsibilities and permissions

| Agent | Can read | Can write | Cannot do |
|---|---|---|---|
| Swing/scalp proposer | data, playbook, scenario memory | proposals | place an order directly |
| Validator | data, rules, state | validator decision | call LLM or change policy |
| Risk arbiter | account, positions, limits | risk decision/kill state | trust LLM confidence |
| Execution agent | approved immutable order intent | broker outcome | alter intent/limits |
| Position manager | own tagged positions, risk policy | management events | manage manual/unowned positions |
| Memory agent | verified journal records | memory candidates | change live risk policy |
| Research agent | anonymized historical journal | candidate models/reports | influence live trading directly |
| Operator | all permitted views | reviewed policy changes | bypass audit trail |

Use distinct magic numbers and database identities for swing/scalp. Every action
must state its actor and permission scope.

## 9. Failure and restart behaviour

- Missing/stale market data, calendar, broker specification, or risk state:
  do not create new trades.
- LLM/provider outage: continue reconciliation and position safety; create no
  new LLM-assisted trades.
- Database outage: halt execution because no audit/idempotency guarantee exists.
- Memory-distillation outage: retain the local SQLite outbox and retry later;
  never discard verified evidence or block risk/execution.
- MT5 reconnect: reconcile journal, open orders, positions and recent history
  before analysis resumes.
- Repeated error threshold: persistently enable the kill switch and alert the
  operator.

## 10. Daily and weekly operating rhythm

```text
Pre-session:  reconcile -> verify broker/data -> create versioned playbook
During:       event-driven analysis -> validator -> arbiter -> execution
Every cycle:  monitor health/data/positions; no repeated unfinished-candle work
Post-event:   capture observed reaction; enforce cooldown
Post-session: reconcile all deals -> label -> write daily narrative/metrics
Weekly:       audit journal -> review drift -> validate candidate patterns
Offline only: walk-forward research -> promotion review -> versioned deployment
```

## 11. Promotion gates

Memory may influence LLM explanations immediately once source-linked. It may
adjust confidence only after a correctly labelled, reconciled, sufficiently
large journal and offline walk-forward evidence. It may never bypass the
validator, risk arbiter, or kill switch. HMM/GARCH/Kelly/RL output follows the
same rule through `research_influence_gate.py`: demo/live influence requires a
100+ labelled sample, purged/OOS evidence, a matching SHA-256 evidence digest,
and expiring operator approval; paper mode is research-only.
