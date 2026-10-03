# Master Trading System Goal List

> For the full execution-order list of every currently unchecked item, see
> [TRADING_SYSTEM_REMAINING_TASKS.md](TRADING_SYSTEM_REMAINING_TASKS.md).

Single source of truth for the trading-system build, taken from the roadmap,
memory specifications, continuous-agent design, and attached Phase 0/macro
architecture notes. This is engineering and research work, not a profitability
claim or financial advice.

## Status legend

- `[x]` Implemented and locally verified
- `[~]` Partially implemented; acceptance evidence still required
- `[ ]` Not implemented
- `[!]` External/empirical blocker; cannot be completed honestly in code alone
- `[N/A]` Intentionally excluded by a later decision

## Non-negotiable rules

1. Local SQLite/Postgres journal is the source of truth. The LLM is never the
   journal, risk engine, or order-permission layer.
2. LLMs only propose or summarise. The deterministic validator, portfolio
   arbiter, execution idempotency, and kill switch have no LLM dependency.
3. Every new order must be planned before submission: entry type/price,
   expiry, SL, TP, cost tolerance, risk, and event state are immutable in its
   order intent.
4. Missing/stale critical data means no new trade. Do not substitute a prior
   close for a live quote, DOM, calendar, or broker specification.
5. HMM, GARCH, Kelly, supervised models, bandits, and RL remain advisory until
   the promotion gates at the end of this file pass.
6. external cloud/cloud memory is `[N/A]`: the user selected local SQLite plus the
   existing configured LLM provider for memory distillation.

---

## Goal 0 — Credential, terminal, and release safety

- [x] Normalize UI risk input once (`1.0` percent becomes `0.01`) and cap it.
- [x] Require explicit paper/demo/live mode and live confirmation; automated
  live LLM mode additionally requires `LLM_TRADER_ALLOW_LIVE=true`.
- [x] Persist a kill switch and enforce daily/weekly/max-drawdown portfolio limits.
- [x] Add unique swing/scalp magic numbers and restrict management ownership.
- [x] Enforce magic ownership in the low-level SL/TP modification helpers as
  well as TradeManager; symbol-only modification now returns zero/no-op.
- [x] Prefer environment credentials over the selected `accounts.json` record.
- [x] Prevent the account-management CLI from accepting or persisting plaintext
  passwords; new account records require an uppercase `password_env` reference.
- [x] Add ignore rules and a security note for credentials/state files.
- [x] Propagate the selected PAPER/DEMO/LIVE mode into the authoritative
  runtime journal scope, bind DEMO/LIVE to the authenticated login/server
  fingerprint, and reject mutually-exclusive mode flags. Runtime status now
  reports the actual selected mode instead of treating every non-paper run as
  LIVE.
- [x] Enforce least-privilege contract coverage for every runtime agent event;
  scheduled health, macro review, reconciliation, and research writes now have
  declared actions/artifacts, and execution scope identity is rejected unless
  login/server are returned by authenticated MT5 account information.
- [x] Require the explicit `order_authority` contract bit before creating any
  durable order intent; action and artifact permissions alone cannot grant
  execution capability.
- [x] Validate journal scope before operational-table initialization so an
  invalid or mismatched DEMO/LIVE identity cannot leave a seemingly usable
  execution database after startup fails closed.
- [x] Reject live snapshots whose candle timestamps are missing or later than
  the capture time; point-in-time ordering is now a deterministic data-quality
  rule with regression coverage.
- [x] Validate feature-snapshot containers, finite scalar values, source identity,
  completed-bar index, and completed-bar-versus-capture timestamps before a
  point-in-time feature snapshot is accepted; malformed snapshots fail closed.
- [x] Make deterministic setup classification fail closed on malformed,
  non-finite, or negative indicator inputs instead of raising or inventing a
  setup ID; malformed setup reasons are now journal-auditable.
- [x] Make currency-aware event policy fail closed on malformed relevant
  calendar importance or non-object event records, while ignoring malformed
  records that are provably for unrelated currencies.
- [x] Reject non-finite or negative broker/cost-calibration inputs before net
  reward/risk calculation; NaN/Infinity can no longer appear as an approved
  cost assessment.
- [x] Reject non-finite quote, spread-limit, entry-tolerance, expiry, and tick
  age inputs before pending-order planning or pre-submit quote validation.
- [x] Make broker preflight reject non-finite/malformed account flags,
  leverage, tick/contract values, volume ranges, stop/freeze levels, and order
  modes before a broker policy can be reviewed.
- [x] Make execution-policy validation reject non-finite calibration values,
  empty broker fingerprints, and non-integer expiry/tick-age limits before a
  policy can become approved.
- [x] Make setup data-dependency checks fail closed on malformed snapshots,
  calendar flags, DOM depth/capture metadata, and cross-market payloads.
- [x] Make broker replay specifications and intent indices type-safe and
  finite before synthetic fills, costs, or outcomes are calculated.
- [x] Make risk sizing fail closed on malformed configuration/equity/position
  inputs and refuse broker-minimum volume rounding when it would exceed the
  requested dollar risk; direct risk configuration is capped at 1%.
- [x] Make the integrated margin-safety gate reject non-finite or malformed
  policy/account/risk/position-estimate inputs before a live intent is made.
- [~] Rotate MT5/API credentials and keep `.env`, `accounts.json`, and
  `config.ini` outside the workspace/Git tracking. Plaintext `.env` and legacy
  config files are now moved to recoverable storage outside the project;
  `accounts.json` is sanitized to an environment-variable reference. A
  recreated locked `.env` was sanitized in place without exposing contents, and
  the current scan has zero production credential findings. Provider/broker
  rotation, Git-history review, and approved secret-store migration remain
  operator actions.
- [x] Authenticate the intended MetaQuotes-Demo account without persisting
  credentials. The authenticated probe confirmed the login/server identity,
  trade permission, zero open positions, and zero pending orders; readiness now
  uses the explicit `AUTHORIZED` probe status while keeping execution authority
  false.

**Done when:** `account-info` connects to the intended demo account, secrets are
not tracked, and live safety tests show a kill switch prevents order submission.

## Goal 1 — Broker and data-quality preflight

- [x] Preflight broker symbol mapping/visibility/tradability at startup.
- [x] Reject stale ticks, untradable symbols, invalid spread, invalid stop
  distance, absent news safety, and non-completed-candle triggers.
- [x] Refuse Yahoo, random, or mock OHLC/DOM/news fallback in the trading
  analysis path. Missing MT5 data now produces an auditable no-trade error;
  replay or paper fixtures must be supplied explicitly and are never presented
  as broker observations. The `data --paper` CLI also fails closed instead of
  generating random candles. Mock LLM decisions are rejected for any broker
  cycle, and source-status output reports unavailable data rather than
  “simulated” market availability. The AUTO scanner likewise returns no
  opportunity while MT5 is unavailable instead of selecting Yahoo/random
  candidates, and authenticated wildcard scanning uses a deterministic order
  instead of random sampling so restarts produce comparable evidence. Paper
  quote mechanics no longer override missing calendar/data-quality evidence in
  the deterministic validator.
- [x] Ensure legacy headline/news helpers cannot fail open. The API manager's
  blackout check now requires a fresh structured calendar cache, validates
  timestamp/currency/importance fields, and treats missing or malformed data
  as a blackout; the execution cycle delegates final permission to the
  source-bound calendar/EventPolicy gate.
- [x] Make API source-health status truthful: an API integration is `ONLINE`
  only when its client is instantiated; unconfigured public endpoints remain
  `OFFLINE` and cannot satisfy data dependencies.
- [x] Make LLM pair selection fail closed: malformed/provider-failed responses
  produce no opportunities, while successful output is normalized,
  deduplicated, and allow-listed against the registered symbol universe.
- [x] Require pair selection's declared calendar, news, and macro dependencies
  to be healthy before scanning; do not treat a calendar-only snapshot as
  sufficient context.
- [x] Record pair-selection LLM requests in the scoped budget/telemetry ledger
  with hashes, latency, parse result, and provider/model identity.
- [x] Replace runtime metric placeholders with durable rolling Sharpe/Sortino
  calculations and correct max-drawdown units in the dashboard payload.
- [x] Enforce the pending-entry boundary independently of upstream proposal
  validation: require owned magic, finite protective stop/target geometry,
  timezone-aware future expiry, and an initialized broker for live submission.
- [x] Make pending cancellation ownership-safe by matching the persisted intent
  magic to the broker/paper order before removal.
- [x] Recheck pending limit/stop geometry against the live bid/ask immediately
  before submission and reject a crossed or marketable stale plan.
- [x] Make position-manager partial closes and pyramids broker-contract aware:
  require symbol volume min/max/step and a fresh quote, normalize volume to the
  broker step, preserve strategy magic, and refuse manual/unowned positions.
- [x] Resolve broker filling-mode flags for owned management and emergency-exit
  requests (IOC/FOK mapping); fail closed when no supported market mode exists.
- [~] Capture broker contract size, point, digits, lot step/range, stop/freeze
  level, filling/order mode, margin/leverage, account constraints, and a
  stable broker-spec fingerprint through `broker-preflight`. Required fields
  are validated, `journal broker-preflight-status` exposes the gate, and
  per-symbol policies must match the current fingerprint. Explicit and
  AUTO/scanner-selected symbols now receive one-time preflight before their
  first cycle. Malformed envelope/report/account/symbol shapes now return an
  invalid non-authoritative validation result instead of raising. Authenticated
  report evidence and policy review remain.
- [~] Add a dedicated data-quality gate for OHLC consistency, price outliers,
  malformed OHLC/DOM containers, finite live bid/ask/spread values, insufficient history, source identity, local
  clock drift, candle gaps, and DOM age/depth for DOM-required setups. Live
  tick clock observations persist local-versus-broker offset and reject stale
  or skewed timestamps. Live snapshots now bind a non-secret MT5 provider/server
  identity and bounded in-run candle fingerprints reject changed OHLC/revision
  payloads for an already-seen candle. Live proposals now compare the broker
  tick `provider_time` with local capture time; authenticated replay evidence
  remains. An initialized MT5
  terminal now fails closed when bars are unavailable instead of using a
  fabricated or external fallback series.
- [~] Set symbol-specific spread/slippage/stop/entry-tolerance policies from
  actual broker specifications rather than universal constants. `execution_policy.py`
  now requires an approved per-symbol calibration artifact and live/demo plans
  fail closed without it. `journal policy-candidate` and `journal policy-approve`
  provide a fingerprint-bound, reviewer-recorded workflow; policy loading also
  requires explicit valid preflight evidence and a timezone-aware, non-future
  review timestamp plus the exact symbol contract in the validated envelope.
  Authenticated broker calibration and replay evidence remain.
- [~] Add fail-closed calendar/DOM health, quote-gap and microstructure filters.
  Registered setup data dependencies now reject unavailable calendar, DOM or
  macro/cross-market inputs and preserve the requirement decision in the
  proposal snapshot. Independent provider health, timestamp/revision checks
  and outage drills remain. The MT5 calendar wrapper now normalizes signed
  provider errors and ctypes/mock records under regression coverage. Live
  proposal validation now requires the current snapshot's `calendar_source` to
  be a successful configured or MT5 calendar feed; merely having a calendar
  object in memory cannot bypass the outage gate. DOM observations now reject
  missing timestamps and non-finite/negative spread, depth, or imbalance data.
 - [~] Persist provider circuit tier/failures and degrade to deterministic-only
   after repeated LLM failures. Provider-specific health, latency thresholds,
   and controlled failover remain. Valid model HOLD responses count as
   successful provider calls, while no-response, parse-error, unexpected-error,
   and unknown-trace outcomes now count as failures for durable telemetry and
   circuit escalation; the selected model still fails closed to HOLD. Pair
   selection uses the same provider-circuit accounting and emits health events.
   Malformed provider usage metadata is normalized to safe zero values and a
   failed request outcome, preventing stuck reservations or false health resets.

**Done when:** a preflight report lists exact broker constraints per symbol and
every corrupted/stale/missing critical input deterministically produces `NO_TRADE`.

## Goal 2 — Point-in-time market and feature journal

- [x] Persist source-hashed proposal-input and proposal-output snapshots.
- [x] Persist playbook and risk-policy versions referenced by a proposal.
- [x] Persist proposal/trade/order-intent records and operational events.
- [~] Capture full D1/H4/H1/M30/M15/M5 OHLCV, bid/ask/mid, spread, tick age,
  tick volume, DOM, and missing-data flags in snapshots. The authoritative
  journal now also retains a `market_ticks` ledger for position-attention
  fresh/stale/missing quote outcomes; full authenticated MT5 replay remains.
- [~] Persist exact completed-candle values through `feature_snapshot_v1` for
  return, realized volatility, ATR, EMA level/slope, RSI, MACD, Bollinger,
  ADX, support/resistance, candle/wick, tick-volume, and VWAP. Completed-ATR
  percentile, SMA(20), and explicit feature-source lineage are now persisted
  and schema-checked; replay proof remains.
- [~] Persist completed-candle liquidity-sweep, round-number proximity,
  equal-high/low and Asian-range/breakout features. Completed-candle
  London-open, New-York-open, overlap, future high-impact event-proximity,
  point-in-time cross-market summary, and driver-bucket identity fields are now
  persisted. The runtime retrieves only non-stale, same-cutoff observations
  from the completed-return ledger; authenticated multi-market feed replay
  remains.
  Sweep/reversal
  proposals now require a separate next-completed-candle confirmation and
  volume/directional rejection evidence.
- [~] Store feature calculation version, source, point-in-time completed-bar
  timestamp, lookbacks, and warm-up count with each scalar snapshot. Per-field
  source lineage and schema migration/replay tests remain.
- [~] Add schema migration/version checks through `feature_schema.py`: every
  new feature snapshot carries a deterministic schema hash and proposals fail
  closed on unknown versions, hash mismatches, or missing required fields.
  Historical legacy payload migration/replay evidence remains.

**Done when:** any accepted or rejected decision can be replayed from a single
immutable snapshot without using future data.

## Goal 3 — Planned execution and market-microstructure protection

- [x] Create idempotent order intents before submitting an order.
- [x] Reject proposal execution when price moves outside entry tolerance.
- [x] Record intent, broker result, and filled order ticket where available.
- [~] Default the LLM path to deterministic planned limit/stop entries. An
  at-market plan is rejected rather than converted into a market order; intents
  store expiry and the reconciler cancels unfilled expired orders. Broker fill
  reconciliation remains; an explicit ticket/magic/reason-bound emergency
  market-exit policy now exists and cannot create a new position. The generic
  live `place_market_order` helper now fails closed as well, so no direct live
  market-entry escape hatch remains; paper-only simulations remain available.
  The low-level planned-entry boundary also requires a normalized symbol,
  positive integral quote timestamp, and integral expiry.
- [~] Calculate pre-trade spread, commission, swap and slippage and require
  net reward/risk >= 2R. `cost_estimator.py` rejects live/demo execution when
  broker tick value/size or calibrated cost assumptions are unavailable and
  records the full assessment in the order intent. Broker/symbol calibration
  and replay evidence remain.
- [~] Add a pre-submit revalidation of broker trade mode, quote structure, tick
  age/clock drift, and spread immediately before freezing a pending plan. Margin
  checks and broker-demo evidence remain.
- [~] Reject weekend, Friday-close, conservative rollover/maintenance,
  low-volume and missing required-DOM liquidity conditions; spread/news gates
  already apply. Broker-calibrated transition/holiday rules and replay evidence
  remain. Holiday detection now halts trading without terminating continuous
  maintenance/reconciliation loops.
- [~] Record requested/accepted/filled price and volume, retcode, requote,
  partial fill, execution latency, spread-at-submit, spread-at-fill, and
  implementation shortfall. The execution ledger now has normalized columns
  for these fields; authenticated partial-fill/requote replay remains.
- [~] Add cancellation/expiry reconciliation for pending orders. The runtime
  reconciler now cancels expired pending tickets, records `CANCELLED` and
  `EXPIRED`, and escalates an unconfirmed cancellation to `UNRESOLVED`; live
  broker cancellation/fill replay evidence remains.

**Done when:** every entry has a pre-existing immutable intent and an auditable
cost/slippage comparison; no trade can chase a materially moved price.

## Goal 4 — Deterministic proposal permission and portfolio risk

- [x] Separate LLM proposal from deterministic validator and portfolio arbiter.
- [x] Enforce market/tick/symbol/spread/candle/stop/RR/confluence/news/cooldown/
  kill-switch checks.
- [~] Persist cooldown, hourly-rate, daily-start-equity, and peak-equity state
  in the authoritative SQLite journal; `RiskManager.set_store()` restores it
  and accepted orders register usage only after broker acceptance. Multi-process
  and authenticated restart replay evidence remain.
- [x] Enforce maximum open risk, currency-driver bucket risk, daily/weekly loss,
  and maximum-drawdown kill-switch checks.
- [~] Record every passed and failed validator rule in a complete machine-readable
  validation trace. `ValidationResult.rule_trace` now records each deterministic
  rule with pass/fail status and evidence, and the trace is persisted in the
  proposal, setup-instance, and lifecycle payloads; portfolio-arbiter and
  authenticated replay evidence remain.
- [~] Add deterministic MTF alignment, session/rollover, low-volume and
  DOM-liquidity inputs alongside existing event/broker-margin checks. Volatility
  and net-cost RR rules, broker-calibrated thresholds, and replay evidence remain.
- [~] Add correlation matrix estimation and conservative fallback driver buckets.
  Completed-bar local rolling-return correlations now identify same-direction
  exposure when enough aligned samples exist; sparse data falls back to driver
  buckets. A stricter correlated-risk ceiling is enforced. Multi-timeframe
  calibration and broker replay evidence remain. Correlation configuration now
  fails closed for unsafe thresholds/lookbacks/staleness values, and malformed
  bar timestamps force the conservative fallback.
- [~] Add margin/liquidation-distance concentration checks. `MarginSafetyGate`
  uses a fresh MT5 account/stop-out snapshot plus broker-calculated gross
  position margins to reject weak projected margin/free-margin/stop-out buffers
  and symbol/driver concentration. It records account-currency stop-out loss
  buffer and planned-R distance without inventing a liquidation *price*.
  Authenticated broker-mode replay and threshold calibration remain.
- [~] Add versioned registered setup constraints and duplicate prevention keyed
  by deterministic setup/candle/strategy. Durable setup instances now track
  candidate, validation, acceptance, fill, missed and close states. Playbook-
  version keying, explicit invalidation analytics and replay evidence remain.

**Done when:** every rejection is replayable with exact failed rules and no LLM
output can bypass hard portfolio or broker constraints.

## Goal 5 — Complete trade lifecycle and reconciliation

- [x] Model proposal/open/close state in the durable journal.
- [x] Reconcile durable open tickets against MT5 positions/history on restart.
- [x] Do not fabricate a zero-P&L close when broker history is unavailable.
- [x] Queue reconciled closed trades for local memory processing.
- [~] Persist a daily bot-magic MT5 deal-versus-journal reconciliation report.
  It flags missing position IDs and material net-P&L differences, and counts
  manual/unowned broker deals separately. The reconciler now maps distinct
  order/deal/position identifiers through the append-only execution ledger and
  preserves missing, ambiguous, or non-finite broker mappings/costs as
  discrepancies. Production mode also filters the requested UTC window and
  rejects missing/malformed owned timestamps or close-entry markers.
  Authenticated MT5 execution, full order-history and external broker-statement
  comparison remain.
- [~] Add immutable lifecycle events. `PROPOSED`, `VALIDATED`, `REJECTED`,
  `SUBMITTED`, `ACCEPTED`, `PENDING`, `FILLED`, `PARTIAL`, `CANCELLED`,
  `CLOSED`, `ERROR`, `EXPIRED`, and `UNRESOLVED` are recorded on implemented
  paths. An append-only broker execution ledger separately records observed
  order/deal/position IDs and requested/accepted/filled prices and volumes;
  SQLite append-only triggers now reject edits/deletes for lifecycle,
  execution, position-observation, and market-tick evidence; full live
  netting/hedging replay evidence remains. Linked setup instances now enforce
  `FILLED` → `PARTIAL`/`MANAGED` → `CLOSED` transitions and reject management
  after terminal states.
- [~] Record position modification, partial/full close management requests, and
  their success/error. `TradeManager` filters every operation by its owned
  magic numbers and records unowned observations/refused actions separately in
  `manual_activity`. SL modifications and partial/full close requests now also
  write lifecycle plus broker-execution evidence, with the position magic copied
  into the event. Close reconciliation preserves the broker `time_msc`/`time`
  timestamp when available rather than substituting the local reconciliation
  wall clock.
  into partial-close requests. Trailing, break-even, scale, hedge, manual
  statement matching, time exit, event reduction, and emergency exit remain.
- [~] Record sampled unrealized P&L/R and derive MFE/MAE from qualified fresh
  broker-quote/execution marks only. Each mark has source, tick age, spread,
  quality, and maximum-gap evidence; stale marks remain operational evidence
  but cannot improve an excursion metric. Commission, swap, financing,
  tax/rebate, holding time, and broker-statement reconciliation remain.
- [~] Ensure every accepted proposal resolves to one broker outcome or an
  explicit terminal execution error; new order intents are idempotent and
  reviewable through filled/partial/cancelled/expired/unresolved/error states.
  The transition table rejects invalid/backward mutations while allowing only
  documented unresolved recovery.
  Existing legacy proposals still require reconciliation evidence.
- [~] Escalate unresolved broker intents to a durable, operator-owned review
  record with detection timestamps/count, severity and explicit
  acknowledge/resolve/quarantine disposition. `AgentOperations` now applies
  durable age/detection-count escalation during position reconciliation without
  resolving or permitting an order. Bounded exponential broker-history retry
  claims are now durable and read-only; they never resubmit an order. Live
  recovery evidence remains.
- [x] Reconcile the existing 20 legacy open journal records against the
  authenticated MetaQuotes-Demo account. Every ticket had zero matching broker
  position/order/deal history; the rows are preserved and retired through an
  explicit evidence-hashed marker, with no fabricated close, P&L, or label.
  The stale rewards sidecar was archived and replaced with an empty active
  history, and a fresh DEMO-scoped journal was created for the authenticated
  account fingerprint.

**Done when:** journal totals match the broker statement and every ticket has
one lifecycle, one owner, one final outcome, and no unresolved stale records.

## Goal 6 — Continuous agents, scheduling, and rate budgets

- [x] Persist agent runs, heartbeats, tasks, LLM requests, order intents, and
  memory outbox records.
- [x] Deduplicate completed-candle analysis and LLM request triggers.
- [x] Use separate durable LLM request budgets for signal and memory work while
  retaining a shared global request count.
- [x] Persist signal-request and bounded local-memory-batch prompt/response
  hashes, retry/attempt count, parse status, response size, latency, and
  provider/model identity; keep token/cost estimates distinct from realized
  usage. OpenRouter/Cerebras SDK usage metadata is now extracted into numeric
  token/cost telemetry without retaining content; reservation/completion input
  validation now rejects malformed identities, non-finite costs, and fractional
  counters. Abandoned `RESERVED` requests now expire after a durable lease
  timeout so a crashed worker cannot permanently consume a provider/model
  concurrency slot; they remain counted as attempts. Live provider
  verification, alerting, and quota reconciliation remain.
- [x] Persist HMM/GARCH advisory state from completed candles only.
- [~] Run swing/scalp on their own candle triggers with shared risk arbitration.
  The runtime now uses strategy/timeframe-specific completed-bar candidates and
  claims the durable analysis task before any checkpoint is advanced, so a
  crash/provider failure cannot silently skip a candle. `strategies=both` now
  preserves both state machines even when a single explicit timeframe is
  supplied; broker replay remains.
- [~] Add durable schedule records for market health, macro-event monitor,
  reconciliation, session/daily review and weekly research; due jobs are
  journaled after restart. Due schedules now execute before the no-new-candle
  HOLD return: health persists source/alert state, event monitoring persists
  calendar state, reconciliation performs broker-safe checks, and session
  review emits memory metrics. Claims now preserve the original cadence across
  downtime, report missed windows, and recover malformed schedule timestamps to
  a known future point without crashing the pulse. Dedicated executors, backup
  and fault-injection tests remain.
- [~] The persisted circuit breaker can degrade to deterministic-only, and the
  old silent model fallback now returns `HOLD`. It now journals health state,
  uses tiered cooldowns and requires an explicit operator reset after terminal
  escalation. `llm_router.py` refuses silent provider substitution and only
  allows explicitly configured provider failover; versioned fallback policy,
  alerts and live drills remain. Persisted malformed tiers, failure lists, or
  naive/invalid timestamps now fail closed into manual-reset mode instead of
  raising or reopening the LLM path.
- [~] Add safe retry/backoff, queue health, process heartbeat alerts, clock-drift
  checks, and restart recovery tests. Persisted task leases and retry caps block
  duplicate work while a
  lease is valid, expired/failed tasks can be reclaimed within a bounded cap,
  and recovery/queue-health counts are exposed. `AgentOperations.backpressure_status()`
  now reports explicit running/retryable-error thresholds and a reproducible
  `HEALTHY`/`BACKPRESSURE` status. Agent runs now heartbeat on every market
   pulse, close explicitly on shutdown, and recover abandoned heartbeat records
   on restart. `health_alerts.py` now reports stale heartbeats, expired leases,
   and exhausted task/review retries. Backoff-policy and fault-injection
   evidence remain. Clock-observation thresholds and
   latest-per-source drift alerts are now validated and fail closed; external
   clock synchronization evidence remains. A durable local `alert_outbox` now
   supports idempotent enqueue/claim/ack/retry/recovery through the journal CLI;
   `alert_delivery.py` adds fail-closed file/webhook delivery with crash-
   recovery and retry/block regression coverage via `journal alerts-deliver`;
   production endpoint configuration remains.
- [~] Apply a request budget based on actual provider quotas/latency. Do not run
  160 LLM decisions per minute; use event-driven calls, caching, and defined
  high-priority windows instead. Provider/model local reservations now cap
  requests, concurrency, estimated/actual tokens and costs; actual response
  usage metadata, alerts and live quota calibration remain.
- [~] Implement explicit agent contracts for `MacroAnalyst`, `TechnicalNet`,
  `MarketModel`, `RiskController`, and `QuantitativeCoach`; all communicate via
  the local journal/task store and have least-privilege write permissions.
  `agent_contracts.py` validates scheduler identities and the checked event
  gateway rejects proposal-agent order-intent writes. Remaining work is routing
  all existing strategy writes through it and exercising it in recovery drills.
- [~] Implement a dual-velocity pipeline: fast local market/position monitor
  versus slower macro research/session-review jobs. The durable scheduler has
  separate 30/60-second monitor and hourly/daily/weekly research cadences;
  `dual_velocity.py` now provides bounded fast/slow executors with keyed
  deduplication, backpressure, non-propagating worker errors, and result
  telemetry. Memory distillation is dispatched through the slow lane and the
  pulse polls results without granting execution authority. Operations status
  now recovers stale agent heartbeats before reporting active runs and exposes
  the recovery count. Broker-thread safety, full schedule migration, and
  sustained load tests remain.

**Done when:** restart/reconnect tests demonstrate no duplicate analysis/order,
and every task is resumable or explicitly failed for review.

## Goal 7 — Memory layers and local LLM distillation

- [x] Local SQLite is authoritative; no external cloud/cloud-memory connection is active.
- [x] Store local narratives, macro events, daily playbooks, trade labels,
  verified source-linked memories, and operational evidence.
- [x] Distil verified outbox records using the same configured trader LLM.
- [x] Retrieve local narrative/verified memory before a proposal.
- [~] Implement immutable append-only local L0 storage with recursive secret/
  login redaction and source/redacted hashes; LLM prompt/response and MT5
  order request/response/error paths are attached. Recursive inline redaction
  now covers free-form password/API-key/bearer-token/account-ID text as well as
  sensitive keys. Operational events are also redacted and carry a stable
  payload hash. Point-in-time operational events, snapshots, configuration
  versions, and clock observations now reject UPDATE/DELETE mutations. Full
  tool/operator coverage, access roles, and retention controls remain. The
  combined audit export now includes bounded redacted operational events, L0
  traces, snapshots, configuration versions, and clock observations.
  The export also validates each stored payload hash and reports per-layer
  integrity status. `journal audit-retention-status` reports records outside
  the configured retention window without deleting append-only evidence.
- [~] Implement source-linked L1 atomic facts with validity/expiry fields,
  confidence, observed/inferred/proposed status, contradiction and
  supersession. Writes now reject empty source IDs and fact keys; proposal
  retrieval excludes proposed, superseded, expired, and future-dated facts.
  Extraction/review workflow and bounded live-context evidence remain.
- [x] Reject non-finite atomic-fact confidence and preserve malformed calendar
  importance as an explicit memory-quality marker instead of crashing memory
  persistence or allowing invalid event data into a playbook.
- [x] Apply the same finite [0,1] confidence validation to verified and core
  memory writes so L2/L3 memory cannot store invalid certainty values.
- [~] Implement L2 scenario documents for strategy/symbol/side/setup/regime
  with evidence tickets, sample counts, and balanced wins/losses where data
  exists. Candidate scenarios are excluded from retrieval until explicit
  review approval, rebuilds preserve approved status, and rejected/unresolved
  setup/order-intent examples are retained with separate counts. Session/event
  fields, review evidence, and live evidence remain.
- [~] Implement reviewed L3 candidate/approved/retired memory for trading
  personality, risk policy, strategy rules, stable lessons, skills/wiki and
  access policy. Only approved items enter LLM context; audit-event linkage,
  supersession UI and production review evidence remain.
- [~] Candidate LLM-distilled narratives and verified-memory summaries are now
  excluded from live retrieval until explicitly reviewed; deterministic daily
  journal summaries retain source ticket IDs, and retrieved narrative records
  retain source IDs/tags/timestamps/approved status. Daily/weekly/monthly durable
  maintenance schedules now generate weekly deterministic summaries and only
  reviewable `STABLE_LESSON` candidates when closed data exists; live recovery
  and provenance replay evidence remain. `journal weekly-memory` and
  `journal monthly-memory` expose the same restart-safe workflows, with
  explicit ISO-date/Monday validation for period boundaries.
- [~] Memory distillation is now source-gated: only allow-listed durable
  closed-trade/event facts may reach the configured LLM; unverified outbox
  entries are quarantined as `BLOCKED` before inference and are visible in
  memory/operations metrics. Full provenance replay evidence remains.
- [~] Add working-memory/task-graph compaction without dropping errors or risk
  decisions; retain raw source evidence locally. `AgentOperations` now provides
  redacted, revisioned, restart-safe working-memory entries with explicit
  point-in-time retrieval, bounded prompt size, safety-kind retention, and
  deterministic compaction summaries; durable claimed-task edges remain
  replayable after restart. The continuous pulse retrieves strategy plus shared
  event context, event-monitor updates persist short-term state, and journal
  CLI status/context/compact commands expose the workflow; recovery-load and
  long-run throughput evidence remain.
- [~] Add structured local L2 retrieval with explicit win/loss balance metadata;
  it never treats retrieved cases as permission. Approved scenarios now prefer
  verified balanced wins/losses and mark unbalanced fallback evidence;
  semantic JSON tokenization is consistent. Semantic search now enforces exact
  symbol/strategy/side/setup/regime/session/timeframe/event filters before
  vector ranking. Larger-scale indexing and quality metrics remain.
- [~] Record local retrieval telemetry: source IDs, layer, result count,
  latency, bounded prompt-size estimate, stale/contradictory fact counts and
  scenario balance. Retrieval IDs are linked to reconciled labels, and every
  layer now enforces/records the point-in-time `as_of` cutoff used for replay
  and proposal context. `memory_quality_metrics()` now adds per-layer p50/p95
  latency, zero-result and association rates, outcome counts, prompt
  character/token estimates, and malformed/future cutoff error counts. No
  causal memory-performance claim is made; live closed samples and provider
  token data remain.
- [~] Add candidate/approved/retired memory review workflow and monthly stale
  memory/contradiction archival. Review commands and `journal
  memory-maintenance` enforce candidate-only retrieval and retirement; scheduled
  recovery and contradiction-resolution evidence remain.

**Done when:** every retrieved claim has source IDs, date, confidence, outcome
distribution, and cannot modify permissions, risk, or order execution.

## Goal 8 — Macro research, causal graph, and event playbooks

- [x] Store macro-event records and generate/reuse daily playbooks.
- [~] Add local source ingestion with content hash, time/version/rights fields;
  runtime calendar records are stored only as candidate sources. Source policy
  now blocks unverified causal edges and LLM extraction, while retaining tier
  and rights metadata; malformed source identity/payloads are rejected or
  classified unverified. Approved central-bank/statistics/news ingestion and
  operator rights review remain.
- [~] Use the existing LLM provider to extract only candidate structured facts:
  `macro_extraction.py` enforces strict JSON/source spans, allow-listed source
  policy, candidate-only graph/L1 writes, and persists source-span/rights
  provenance in every fact; provider-backed ingestion and review evidence
  remain.
- [~] Build a local source-linked entity/edge graph with sign, confidence,
  validity, contradiction link and candidate/approved/retired review state.
  `macro_graph.py` now records append-only proposal/review/revision history and
  forces amended edges back through candidate review; non-finite confidence and
  malformed event-reaction identities are rejected. Domain data population and
  source-cited causal hypotheses remain.
- [~] Add an event-reaction schema supporting actual/forecast/previous surprise
  and 1m/5m/15m/1h/4h/1d return-window records. Local tracking now starts only
  when a numeric actual plus forecast/previous is observed and records due
  windows from an explicitly labelled post-release point-in-time price anchor.
  The continuous event monitor also ingests deduplicated calendar observations
  during quiet periods. Measurement-window validation now rejects malformed,
  non-finite, zero, or fractional durations. Historical precedents,
  broker/calendar timing replay and validation remain.
- [~] Add multi-variable retrieval: structured state filters + causal graph +
  historical-event similarity, with dated sources and contrary precedents.
  Similar-event retrieval is source-linked, returns differences/uncertainty,
  and now enforces the proposal snapshot's point-in-time cutoff; approved
  causal edges and recent reactions use the same cutoff. Historical regime
  templates and complete populated datasets remain.
- [~] Add pre-event, release, and post-event playbooks/no-trade windows; do not
  treat macro narrative or crowd psychology as a deterministic prediction.
  The deterministic currency-aware event-policy blackout exists; planned
  setup-specific playbooks, post-event review and broker replay evidence remain.
- [~] Add 48-hour post-event/post-trade review that writes a candidate amendment,
  not an automatic policy change. Durable due-review tracking and candidate
  narrative writes exist; operator review and populated evidence remain.
- [~] Add explicit causal-graph edge type, direction, sign, confidence, source,
  validity period, and contradiction fields. Examples are hypotheses requiring
  evidence, not permanent claims such as “real yields inverse to gold”. The
  schema and candidate review state exist; cited edge population remains.
- [~] Make pair selection use current timestamped calendar/news/macro context;
  static “check economic calendar” placeholders are removed and selection fails
  closed when calendar data is unavailable. Provider freshness evidence remains.
  FRED observations remain macro context only and cannot be converted into
  synthetic release events; when the configured wrapper returns no events, the
  authenticated MT5 calendar is queried independently, with source and event
  count retained in the market snapshot.

**Done when:** a live macro thesis cites current data and dated historical
precedents, displays similarities/differences, and declares uncertainty.

## Goal 9 — Labels, metrics, dashboards, and pattern discovery

- [x] Deterministically label basic closed outcomes and calculate baseline metrics.
- [x] Add integrity, operational, memory, and research-status commands.
- [~] Calculate per-setup R expectancy, Bayesian Kelly eligibility, and
  chronological walk-forward folds.
- [~] Complete deterministic target, stop, time, manual, event, breakeven and
  generic win/loss labels with label-rule version and auto-review status.
  `setup_labels` now persists immutable AUTO labels for rejected, missed,
  invalidated and unresolved setup/order-intent outcomes at runtime and during
  rebuild, plus distinct cancelled, expired, and data-quality-blocked labels.
  `memory-review setup-label` now records operator/timestamp review metadata;
  data-quality policy refinement remains.
- [~] Compute qualified MFE/MAE R summaries, R/net expectancy, holding-time,
  intent status and recorded slippage from the durable journal. MFE/MAE now
  excludes stale/unknown marks and reports a qualified sample count. Cost-complete
  count is displayed; execution metrics now include status rates, latency
  percentiles, spread, shortfall, requotes and broker-reported costs.
  Calibration and confidence-bucket performance require reconciled samples.
- [~] Add a qualified journal report with sample/date/cost/data/OOS status,
  trade, execution and portfolio summaries. Descriptive R-based Sharpe,
  Sortino and max drawdown are now exposed, with sample-gated CVaR(5%) and
  quarter-Kelly eligibility fields, Calmar/Omega and an assumption-labelled
  bootstrap risk-of-ruin estimate; margin stress and sufficient-sample
  dashboards remain evidence-gated. The report now has a strict
  `research_quality` section sourced only from deterministic labels and
  point-in-time feature hashes; top-level sample size is research-qualified,
  while the raw closed-row count is explicitly descriptive.
- [~] Add confidence calibration to the qualified journal report. Descriptive
  confidence bins, Brier score, log loss and 0.5-threshold accuracy are now
  emitted when point-in-time confidence is present; strict false-positive and
  false-negative rates plus proposal approval/rejection coverage are also
  emitted. Raw-vs-validated expectancy and baseline lift remain unavailable
  without labelled counterfactual replay; promotion still requires walk-forward
  evidence. Strict monthly setup-decay comparisons now expose expectancy
  degradation and operator-review flags with a 30-trade minimum; no automatic
  setup mutation is allowed.
- [~] Segment every result by setup, symbol, side, strategy, timeframe, session,
  regime, volatility, spread, event class, model/prompt/feature version.
  `qualified_metrics()` now emits explicit UNKNOWN buckets, sample/date/cost
  qualifiers, and no-hindsight monthly regime comparisons; populated closed
  trades and dashboard presentation remain.
- [~] Display sample size, date range, cost model, data mode (backtest/demo/live),
  and in-sample/validation/out-of-sample status beside every metric.
  Journal metrics and segment reports carry these qualifiers; the new
  read-only `journal dashboard-status` report assembles research, proposal,
  trade, execution, portfolio, period, memory, alert, and readiness panels
  with those qualifiers. Rich interactive panels and validated result
  artifacts remain.
- [~] Calculate monthly regime-aligned growth/change metrics only when the
  compared samples are comparable; never present a small-sample win-rate change
  as learning progress. Comparisons are sample-gated and marked descriptive;
  `qualified_metrics()` now exposes growth quotient, MAE-based drawdown
  compression, and explicit macro-bias/trade-side alignment efficiency while
  excluding unknown bias. Current journal has no closed sample.
- [x] Attach an advisory per-cycle survival/edge audit to every decision cycle.
  `core.metrics.calculate_survival_edge_audit` composes EV, Ralph Vince
  risk-of-ruin, quarter-Kelly and run-theory L_max from the durable journal's
  labelled closed-trade R-multiples (the same source as the validator's rolling
  EV) plus a Hurst regime character of the completed-bar closes.
  `LLMOrchestrator._attach_quant_survival_audit` refreshes it once per completed
  candle before the LLM decision, bounds the projection through the
  `quant_audit` key of `toon_lite`, journals `cycle_survival_audit_attached`,
  and fails closed to INSUFFICIENT_DATA below 20 finite samples. Outputs are
  advisory evidence only: they never grant execution authority or raise a
  deterministic cap.

**Done when:** no performance figure can be read without its sample, cost model,
date range, regime/setup segmentation, and data-status qualification.

## Goal 10 — Supervised learning, bandit, RL, and model promotion

- [x] Attach closed-trade callback and correct the confluence feature path.
- [x] Prevent live execution authority for HMM/GARCH/Kelly/RL outputs.
- [x] Implement advisory online HMM/GARCH and Bayesian fractional-Kelly gates.
- [~] Persist RL proposal state/action but do not promote current Q-learning.
- [~] Replace invalid RL `next_state = state` with actual exit/transition state;
  reconciled terminal state now includes costs-aware P&L/R, duration, adverse
  excursion/drawdown, and flat-position state. Further management-state
  simulation remains offline-only.
- [~] Verify all successful orders and closes are journaled before any RL update;
  broker-confirmed fills are recorded in the monitor and un-reconciled closes
  are refused by the RL callback.
- [~] Remove placeholder/forming-bar RL features. The research state now uses
  the last completed feature snapshot and explicitly records S/R proximity,
  volatility, trend strength, macro-event state and news sentiment/surprise;
  the state-key builder preserves missing numeric features as `unknown` rather
  than silently treating them as neutral zeroes; promotion remains offline and
  sample-gated.
- [~] Build leakage-safe supervised baseline from point-in-time features;
  deterministic no-skill/random/EMA baselines are available offline, but no
  supervised model may train or influence execution until labelled samples and
  walk-forward evidence exist. `research_validation.setup_metrics()` and
  `promotion_gate()` now exclude raw legacy/unlabelled closes, malformed or
  non-finite point-in-time feature values/outcomes, and require a purged
  walk-forward fold for setup eligibility. Malformed, reversed, or future
  feature/label timestamps are also excluded before any metric is computed.
- [~] Train the supervised probability baseline only after the labelled-data
  gate; `supervised_training.py` and `journal supervised-status` fit a
  deterministic logistic model only inside purged walk-forward folds and
  never grant execution authority. Reports now include point-in-time/cost-model
  metadata, a training-data hash, and explicit out-of-sample status. Current
  sample is insufficient, so no artifact exists. Baseline, bandit, and
  supervised entry points reject weak or malformed research configurations
  before any artifact can be trained or written.
- [~] Compare against no-skill, random, and simple EMA/trend baselines net of
  realistic spread, commission, swap, slippage, and latency. Offline scoring is
  available through `baseline_research.py`/`journal baseline-status`; no
  promotion is possible until reconciled labelled folds exist. Every baseline,
  supervised, and bandit status invocation is also recorded in the
  authoritative `research_validation_runs` ledger with immutable dataset and
  report hashes; SQLite append-only triggers reject mutation/deletion, and
  `journal validation-runs` is evidence-only readback. Promotion packages now
  include the immutable run records so research evidence cannot be omitted
  from operator review; `journal audit-export` exports the same records with
  operational evidence and no execution authority. The authoritative
  `TradingStore` schema creates the ledger and append-only triggers at startup.
  Promotion and setup-governance calls also reject requested sample thresholds
  below the mandatory 100-trade policy floor instead of weakening the gate.
- [~] Add contextual bandit for take/skip/confidence only after supervised gate;
  offline setup-aware fold evaluation is available, but the sample/promotion
  gate remains unmet and execution authority is disabled. Reports now carry
  cost-model, point-in-time, training-hash, and out-of-sample metadata.
- [x] Restrict RL to simulated/offline position management initially; live
  cycles do not expose RL context. A non-paper cycle cannot opt in using only
  `TRADING_RL_ENABLE_UPDATES=true`; it also requires the fail-closed
  `research_influence_gate.py` sample/OOS/evidence-hash/expiring-approval gate.
  LIVE runtime cycles do not update the Q-table; live learning belongs to a
  separately controlled offline job.
- [~] Add model registry, feature schema version, experiment tracking, rollback,
  drift monitoring, and operator approval. The local registry is durable and
  reviewable; approval now requires explicit walk-forward, purged-validation,
  point-in-time, sample-gate, cost-validation, and artifact-reference flags;
  promotion remains disabled until validation evidence exists.
- [~] Evaluate candidate model families (logistic/gradient boosting first;
  random forest, XGBoost/LightGBM, sequence models, and ensembles only when
  sample size and a justified hypothesis support them). `model-family-status`
  now emits the ordered offline evaluation plan and sample/fold gate; actual
  fitting and out-of-sample comparison remain blocked by the empty journal.
- [~] Treat Optimal-f/Kelly/volatility-targeted sizing as offline research; use
  only conservative fractional sizing after stability and drawdown tests. The
  advisory HMM/GARCH state is restart-persistent and candle-deduplicated,
  bounded into LLM/proposal evidence, and protected from malformed closes;
  Kelly remains sample-gated and cannot bypass deterministic risk rules.
  Model-family/setup-governance thresholds and HMM/GARCH/Kelly state inputs
  fail closed on malformed/non-finite values.
  Adaptive state restoration also fails closed to unavailable advisory output
  when persisted HMM/GARCH sections are malformed. The same influence gate
  now covers HMM/GARCH/Kelly and RL outputs on demo/live paths; paper mode is
  the only automatic research exception.

**Done when:** a model beats baselines out-of-sample in repeated walk-forward
tests and is promoted only to a capped confidence/size adjustment.

## Goal 11 — Validation, backtesting, and rollout

- [x] Add research gate and chronological fold helper.
- [~] Implement purged/embargoed K-fold and walk-forward split helpers that
  remove overlapping open-to-close label horizons. Point-in-time dataset
  extraction is exposed through `labelled_dataset` and `journal dataset-status`;
  event horizons and recorded validation results remain. Invalid split inputs
  and malformed promotion metrics/stress fields fail closed to empty folds or
  explicit floor reasons.
- [~] Implement broker-realistic backtest: `broker_replay.py` provides explicit
  symbol specs, spreads, commissions, swap, slippage, latency, conservative
  stop-first fills, market closures, and event spikes. Exact broker calibration
  and replay-vs-demo discrepancy evidence remain. Replay now rejects
  malformed/non-finite OHLC and invalid stop/target geometry, and uses
  timestamp-derived swap duration with an explicit interval fallback. It now
  records direction-correct MFE/MAE in price, currency, and R units from the
  same completed-bar path used for exits. The
  direct paper broker also refuses NaN/zero quotes instead of creating
  corrupted synthetic positions.
- [~] Add parameter perturbation, multi-period, multi-symbol, bootstrap, and
  Monte Carlo sequence stress tests; `research_stress.py` provides offline
  sequence/cost-buffer stress and remains disabled as evidence until the
  labelled sample gate is met. It now rejects non-finite R-values, unsafe risk
  fractions, malformed paths/seeds, invalid cost buffers, and sample thresholds
  below the mandatory 100-trade floor.
- [~] Add demo forward-test reconciliation and backtest-versus-demo discrepancy
  report. `forward_test_report.py`/`journal forward-test-status` now enforces a
  hard 100-observation floor on both sides, rejects malformed/non-cost-aware
  evidence, and records cost-model compatibility; authenticated demo
  collection remains.
- [~] Gather at least 100 correctly reconciled closed trades per strategy/setup
  class across multiple regimes before Kelly/model promotion. `sample_gate.py`
  and `journal sample-gate-status` enforce the per-setup sample/regime gate;
  current authenticated data is still absent.
- [~] Produce out-of-sample evidence and operator approval before any funded
  use. `promotion_package.py` and `journal promotion-package` now assemble a
  fail-closed review package with integrity, security, sample, research,
  stress, forward-test, and approval gates, plus a schema version and
  deterministic package hash; no package can grant execution authority and the
  current journal remains insufficient. `journal promotion-verify` recomputes
  the schema/hash and rejects tampered review artifacts.
- [~] Start funded rollout only with an explicit approval, tiny capped risk, and
  a tested manual kill-switch/recovery procedure. `live_rollout_gate.py` and
  `journal live-approval` now enforce a persistent, expiring approval and risk
  cap; file-backed evidence stores content hashes, and any drift or missing
  manifest permanently invalidates the approval until a fresh explicit
  approval (the `--approval-action verify` command exposes diagnostics).
  Reference-only approval evidence is rejected; at least one existing file
  artifact is required. Malformed persisted approval manifests remain
  inactive, and malformed policy state is rejected. Evidence and the operator
  action remain required.
- [x] Run CPCV/DSR/PBO robustness on the loop's own weekly cadence. The
  `weekly_research` schedule now evaluates realised journal R-multiples with
  `run_combinatorial_purged_backtest` when at least 40 samples exist and
  journals `weekly_cpcv_robustness_evaluated` as research evidence feeding the
  promotion-gate review trail; below that floor it reports the explicit sample
  shortfall instead of a score. Every branch carries execution_authority false.

**Done when:** every promoted strategy has a reproducible validation package and
forward-demo evidence consistent with the cost-aware backtest.

## Goal 12 — Security, operations, and data governance

- [x] Add secret ignore rules and security documentation.
- [~] Move secrets to a secure environment/secret manager; rotate exposed keys.
  `security_audit.py` and `journal security-status` provide a redacted
  tracked/untracked/L0-trace preflight; operator rotation and secure-store
  migration remain required. The scanner now catches `#`-containing values,
  ignores normal source-code secret references, and separates non-production
  fixture/build findings from production blockers; no credential values are
  emitted. Explicit redaction/type-annotation code is ignored. It also reports
  risky credential-file paths found in Git history; history scrubbing or formal
  operator review remains required.
- [~] Add encrypted backups, restore drills, retention policy, audit exports,
  access roles, and separation of demo/live/research databases. AES-256-GCM
  encrypted SQLite create/verify/restore (with PBKDF2-HMAC-SHA256), plaintext
  hash/integrity checks, and CLI actions are now implemented and tested;
  dry-run-first retention planning with explicit apply/delete and minimum-copy
  preservation is now implemented. `journal audit-export` writes a complete
  evidence JSON report without granting execution authority; access roles and
  deployment path provisioning remain.
- [~] Enforce journal scope identity at startup. PAPER/RESEARCH may use an
  explicit non-account scope, while DEMO/LIVE require a non-empty account
  fingerprint and reject mismatches; incomplete existing DEMO/LIVE rows report
  `INVALID` instead of being treated as safe. OS/database access-role
  provisioning and separate deployment databases remain operator work.
- [~] Redact credentials/login IDs from the new L0 trace store and retain only
  hashes plus redacted payloads. Account-ID policy coverage and external-LLM
  payload redaction tests remain. The security preflight now fails closed when
  an on-disk L0 trace directory is present, in addition to scanning tracked
  and untracked credential-like literals.
- [~] Add incident runbooks for broker outage, LLM outage, calendar outage,
  corrupted database, stuck order, duplicate order, and emergency stop;
  `INCIDENT_RUNBOOKS.md` is now the operator procedure, and
  `incident_drills.py` covers broker-free deterministic broker/LLM/calendar,
  data, duplicate-order, and restart fault-injection scenarios; authenticated
  broker fault injection remains required.
- [~] Add observability alerts for health, stale data, error rate, provider cost,
  drift, unresolved tickets, kill switches, and backup failure;
  `health_alerts.py`/`journal alerts` provide read-only threshold evaluation,
  including blocked unverified-memory alerts, while external delivery and
  backup probes remain. The local alert outbox is restart-safe and
  transport-neutral; the continuous market-health schedule enqueues alerts
  idempotently without changing order authority; external delivery and
  acknowledgement evidence remain.
- [~] Add an operator handoff/readiness summary that combines independent
  evidence gates without granting authority. `journal readiness-status` now
  reports authorization, broker preflight, journal integrity, legacy review,
  labelled-sample, and tiny-live approval/evidence blockers in one fail-closed
  artifact, together with the redacted credential/trace security review. MT5
  authorization attempts are bounded per terminal path and retain timeout
  evidence, and `MT5Client.login()` applies the same bound to real connection
  attempts, so an unavailable terminal cannot stall the handoff indefinitely.
  Evidence-source exceptions and malformed preflight are converted to blocked
  records instead of crashing or implying readiness. The report also includes
  the centralized research-influence gate, preventing adaptive HMM/GARCH/
  Kelly/RL approval from being omitted from readiness.

**Done when:** recovery and security drills are documented and repeatably pass.

---

## Current completion snapshot

| Area | Status | Blocking evidence |
|---|---|---|
| Core safety and deterministic gating | `[~]` | Broker-specific policies and full validation trace remain |
| Durable local journal/operations | `[~]` | Complete lifecycle and current legacy reconciliation remain |
| Local LLM memory | `[~]` | L0/L1/L2/L3 governance and local hybrid retrieval remain |
| Macro/event research | `[~]` | Ingestion, causal graph, precedent data, event studies remain |
| HMM/GARCH/Kelly/RL | `[~]` | Advisory code exists; empirical promotion data absent |
| Backtest/demo/live validation | `[!]` | Sufficient closed outcomes and authenticated forward-demo evidence absent |
| external cloud/cloud memory | `[N/A]` | Replaced by local SQLite + existing LLM provider |

## Commands used as acceptance evidence

```powershell
python mt5_trader.py journal integrity
python mt5_trader.py journal metrics
python mt5_trader.py journal dashboard-status
python mt5_trader.py journal operations
python mt5_trader.py journal memory-status
python mt5_trader.py journal research-status
python mt5_trader.py journal policy-status
python mt5_trader.py journal auth-status
python mt5_trader.py kill-switch status
```

## Definition of “finished”

The code portion is finished only when every non-external checkbox is `[x]` and
automated tests/replay tests pass. The trading system is finished only when the
external gates are met: authenticated MT5 demo operation, clean reconciliation,
sufficient labelled sample, realistic out-of-sample validation, forward-demo
agreement, and explicit operator approval. A checklist item is never marked
complete merely because an LLM generated a plausible explanation.

---

## Requirements traceability and design coverage

This table proves that the requirements appearing in the attached architecture
documents are represented in the master goal list. Repeated attachments are
treated as one requirement source; the latest local-memory decision supersedes
the earlier external cloud/cloud-memory suggestion.

| Source requirement family | Goal(s) and design location | Completion evidence |
|---|---|---|
| Phase 0 data-quality gate: freshness, OHLC logic, spread, outliers, source trust | Goals 1–2; `TRADING_MEMORY_SPEC.md` | Unit/replay tests reject each invalid case |
| API tiers, circuit breaker, latency/cost/160 RPM control | Goals 1 and 6; `CONTINUOUS_TRADING_AGENT_DESIGN.md` | Persisted provider-health records and fail-closed drills |
| Persistent state/database schema, position registry, narrative memory | Goals 2, 5, 6, 7, 12 | Journal audit and restore/restart test |
| Deterministic validator and risk engine/volatility sizing | Goals 3–4 and 10 | Rule-by-rule replay and portfolio stress test |
| Planned entry, limit/stop order, broker gateway, slippage/commission/swap | Goals 3 and 5 | Intent-to-fill reconciliation report |
| Monitoring, kill switch, terminal/API health | Goals 0, 6, 12 | Emergency-stop and outage runbooks pass |
| Swing/scalp engines and shared arbiter | Goals 4 and 6 | Concurrent demo test without excess correlated exposure |
| Liquidity pools, stop-hunt/sweep, DOM, session/macro filters | Goals 1–3 and 8 | Point-in-time feature data plus rule replay |
| L0/L1/L2/L3, persona, skills, wiki, code graph, access controls | Goal 7; `TRADING_MEMORY_SPEC.md` | Source-linked/reviewed retrieval and permission tests |
| Semantic/episodic/structural macro memory and historical precedents | Goals 7–8 | Dated-source retrieval with similarity/difference evidence |
| MacroAnalyst/TechnicalNet/MarketModel/RiskController/QuantitativeCoach | Goals 6, 8, 9 | Agent contract, schedule, audit and least-privilege tests |
| Full operations journal and all signal/execution/portfolio metrics | Goals 2, 5, 9 | Completeness audit and dashboard reconciliation |
| HMM/GARCH/Kelly/Optimal-f/ensemble/Supervised/Bandit/RL | Goal 10 | Promotion package and model registry approval |
| Purged walk-forward, Monte Carlo, realistic backtest, demo/live rollout | Goal 11 | Reproducible validation report and forward-demo agreement |
| external cloud/cloud memory reference in older notes | Goal 7 `[N/A]` | Local SQLite + existing-provider LLM design approved by user |
