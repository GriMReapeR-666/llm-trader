# Remaining Trading-System Tasks

This is the execution-order companion to `TRADING_SYSTEM_MASTER_TODO.md`.
Every unchecked item is genuinely unfinished. Do not mark an item complete
without the stated evidence.

## A. Immediate safety and broker reality

- [x] Fix MT5 terminal authorization with the intended broker/demo account.
  The authenticated MetaQuotes-Demo probe confirmed the configured login/server,
  trade permission, and an empty position/order slate without printing or
  persisting credentials. Readiness uses the explicit probe `AUTHORIZED` state.
- [~] Rotate all exposed MT5/LLM/news credentials and untrack local secret files.
  Plaintext `.env` and legacy config files are moved to recoverable storage
  outside the workspace, and the active account profile contains only
  `password_env`; the account-management CLI rejects plaintext `--password`.
  A recreated locked `.env` was sanitized in place without printing its
  contents; `journal security-status` now reports no production credential
  findings but remains `REVIEW_REQUIRED` for Git-history rotation and
  secure-store migration, which remain operator actions.
- [x] Verify account currency, leverage, stop-out, contract size, tick value,
  point, digits, lot step/range, filling mode, and stop/freeze levels per symbol.
  Authenticated `broker-preflight` captured these values for XAUUSD, EURUSD,
  and GBPJPY and persisted the stable broker-spec fingerprint in the fresh DEMO
  journal; execution authority remains false until policy review.
- [~] Persist broker specifications with a timestamp/fingerprint and flag a
  contract change. `broker-preflight` now captures and validates required
  account/symbol fields; `journal broker-preflight-status` exposes the result,
  and execution policies are fingerprint-bound. Authenticated broker output
  and policy review evidence remain. AUTO/scanner-selected symbols now receive
  the same one-time preflight capture before their first cycle. Non-finite or
  malformed contract values are rejected before policy approval, and malformed
  envelope shapes now return blocked validation evidence rather than raising.
- [~] Create symbol-specific spread, slippage, stop-distance, event-window, and
  entry-tolerance policies; the cost gate now requires explicit calibrated
  commission/slippage/swap inputs before live/demo execution. `execution_policy.py`
  and `journal policy-status` now fail closed until an approved per-symbol
  artifact exists. `journal policy-candidate --policy-symbol SYMBOL` now creates
  a current-preflight-bound template, and `journal policy-approve --policy-symbol
  SYMBOL --policy-json FILE` persists an explicit reviewer approval; broker-
  specific calibration/replay evidence remains required. Non-finite or negative
  broker/cost inputs are rejected before net-RR calculation, and approved
  policy validation rejects non-finite values, empty fingerprints, fractional
  expiry/tick-age limits, naive/future review timestamps, and policy loading
  without explicit valid broker-preflight evidence or the exact symbol snapshot.
- [~] Add a single read-only readiness report for operator handoff. `journal
  readiness-status` now aggregates authorization, broker-preflight, journal
  integrity, legacy-record, labelled-sample, and tiny-live approval/evidence
  gates plus the redacted credential/trace security review. Stored broker
  preflight evidence is ready and legacy reconciliation is clean, but the
  current terminal probe is unavailable; the report remains blocked by that
  probe, insufficient labelled outcomes, absent tiny-live evidence, and pending
  credential/history rotation. Each MT5 path now has a bounded 5-second
  initialization timeout and preserves timeout evidence; the live
  `MT5Client.login()` path uses the same bound. Source exceptions and malformed
  preflight are represented as blocked evidence rather than crashing the report.
  The report now also includes the centralized research-influence gate, so
  adaptive HMM/GARCH/Kelly/RL approval cannot be omitted from readiness.
- [~] Revalidate trade mode, bid/ask, tick age/clock drift, and spread directly
  before a pending-order plan is frozen. Live MT5 tick observations now persist
  local-versus-broker offset and fail closed beyond five seconds. Margin
  revalidation and live MT5 acceptance evidence remain. Pending-entry and
  pre-submit quote paths now reject non-finite prices, limits, timestamps, and
  tick-age values.
- [~] Make limit/stop entries the LLM execution default; they carry an immutable
  expiry and cancel-if-unfilled reconciliation. Fill reconciliation and the
  authenticated live fill path remain. The emergency exit path is now explicit,
  ticket/magic/reason bound, and refuses to create a new position. The generic
  live market-entry helper now fails closed as well; only paper fixtures may
  create direct market fills outside the planned-entry path.
  The low-level planned-entry boundary also requires a normalized symbol,
  positive integral quote timestamp, and integral expiry; authenticated broker
  fill evidence remains.
  The low-level pending-entry boundary now independently requires a positive
  bot magic number, finite protective stop/target geometry, a timezone-aware
  future expiry, and an available MT5 module before it can submit anything.
  Pending cancellation now also verifies the intent's magic against the broker
  or paper order before issuing removal.
  Live submission additionally rejects a crossed/marketable limit or stop entry
  when the current bid/ask has moved through the planned level.
- [x] Position-manager partial closes and pyramids now require broker symbol
  volume min/max/step and a fresh quote, normalize to broker lot steps, preserve
  strategy magic, and refuse unowned/manual positions.
- [x] Owned management and emergency-exit requests now map broker filling-mode
  flags to MT5 IOC/FOK values and fail closed when no supported market mode is
  advertised.
- [~] Record all broker retcodes, requested/accepted/filled price/volume,
  commission, swap, and final deal IDs. A normalized append-only local
  `broker_execution_reports` ledger now records acceptance, fill/partial,
  cancellation, close, reject and error paths with separately modelled order,
  deal and position identifiers. It now also normalizes execution latency,
  spread at submission/fill, implementation shortfall and requote count. Live
  MT5 replay and broker-specific retcode coverage remain.

**Evidence:** broker preflight report and a demo order/rejection/cancel test.

## B. Data quality and anti-manipulation protection

- [x] Add candle timestamp continuity/gap checks when a provider exposes candle
  timestamps; reject duplicate, out-of-order, malformed, and abnormal-gap bars.
- [~] Validate source identity and local snapshot/DOM timestamps; candle
  sequence/duplicate/gap validation exists. A bounded in-run fingerprint guard
  now detects bar revisions, and live proposals compare broker `provider_time`
  to local capture time; a replay test against actual MT5 data remains. If an
  MT5 terminal is initialized but returns no bars, `fetch_ohlc()` now fails
  closed instead of using Yahoo or synthetic candles; an unavailable terminal
  also fails closed. The paper analysis path no longer fabricates random
  candles, DOM, or macro events—explicit broker-replay/test fixtures are
  required; the `data --paper` CLI rejects implicit random data as well. Live
  cycles also reject `--mock-llm`, and source health reports unavailable data
  instead of simulated market availability. The offline AUTO scanner also
  returns no candidate rather than using delayed or random data. Live snapshots
  now also receive a stable scanner ordering so retries do not silently change
  the candidate universe. Paper quote fixtures cannot override missing
  calendar/data-quality evidence in the validator.
  reject missing candle timestamps and timestamps later than capture time;
  authenticated replay remains. Snapshot container types, finite OHLC values, DOM depth, and live
  bid/ask/spread values now fail closed before any proposal reaches risk.
- [x] Validate feature-snapshot containers, finite scalar values, source/index
  metadata, and completed-bar-versus-capture timestamps before feature hashes
  are trusted; malformed point-in-time snapshots fail closed.
- [~] Persist DOM capture timestamp/depth and retain missing-DOM state rather
  than fabricating a quote; latest spread/imbalance and quote-gap state are
  retained. The authoritative SQLite journal now also has a `market_ticks`
  ledger for every position-attention bid/ask observation, including
  `FRESH`, `STALE`, and `MISSING_OR_INVALID` rows with quote age and spread.
  Full broker tick-rate/DOM replay and authenticated evidence remain.
- [~] Add DOM imbalance level/rate-of-change with an explicit retail-DOM
  incompleteness/spoofability warning. Malformed timestamps, spreads, depths,
  and imbalance values now fail closed; broker replay evidence remains.
- [~] Detect/reject abnormal spread expansion, quote/DOM gaps, low-volume and
  liquidity withdrawal. Quote-flicker and broker-specific threshold replay
  remain.
- [~] Encode completed-candle prior high/low, equal highs/lows, round-number
  proximity, Asian range/breakout, sweep, wick-rejection and volume-confirmation
  features. Completed-candle London-open, New-York-open, and overlap flags are
  now persisted. SMA(20), completed-ATR percentile, explicit feature lineage,
  point-in-time cross-market summaries, future-observation rejection counts,
  and hashed driver-bucket identity are now persisted and validated when
  present. The live cycle populates these summaries from the completed-return
  ledger before proposal construction; authenticated broker/multi-market replay
  remains.
- [x] Require a completed confirmation candle before a sweep/reversal entry.
  The point-in-time feature builder requires a prior completed sweep candidate,
  next completed reversal close, directional body and volume confirmation; the
  forming bar is excluded.
- [~] Add deterministic weekend, Friday-close, rollover/maintenance, low-volume
  and DOM-required liquidity filters. Broker-specific session/holiday policy
  calibration and live replay evidence remain. A detected holiday now halts
  trading without terminating a continuous process, preserving maintenance.
- [~] Add currency-aware event windows with pre-release protection, release
  blackout, post-release cooldown, and actual/forecast/previous/revision data.
  `event_policy.py` now blocks only relevant symbol currencies for 30 minutes
  before and 15 minutes after an event, accepts normalized calendar time
  aliases, fails closed for malformed relevant event records/importance, and
  ignores malformed records proven unrelated to the symbol. Broker-specific
  timing calibration, revision capture and replay evidence remain. The MT5
  calendar wrapper now normalizes ctypes/mock records and signed error codes;
  its regression suite passes.
- [~] Declare calendar, DOM and cross-market dependencies per registered setup
  and reject a proposal when a required source is unavailable. Per-source
  timestamp/rights/revision checks and live outage drills remain. The
  dependency evaluator now validates snapshot/flag types, DOM depth and
  capture metadata, and macro payload shape; malformed input fails closed.
  Live validation also binds calendar readiness to the current snapshot's
  successful `calendar_source`, not merely to an instantiated wrapper.
- [~] Replace static pair-scanner macro placeholders with a timestamped,
  calendar/news/FRED context. `LLMOrchestrator._pair_selection_context()` now
  blocks pair selection when the required calendar is unavailable; authenticated
  provider freshness and authenticated cross-market replay remain. FRED observations are kept
  as macro context only and cannot become synthetic release events; market
  analysis independently falls back to the authenticated MT5 calendar and
  records its source/event count.
  The legacy `APIManager.is_news_blackout()` helper is now fail-closed when no
  structured calendar cache exists and ignores headline-only sentiment; the
  execution cycle no longer treats that legacy helper as a permission gate.
  Source-health reporting no longer claims an uninstantiated SEC client is
  online merely because the public endpoint needs no credential.
  LLM pair-selection parse/provider failures now return an empty opportunity
  set instead of silently defaulting to majors, and accepted symbols are
  normalized, deduplicated, and restricted to the registered universe.
  Pair selection now also requires all declared calendar, news, and macro
  sources to be available; a calendar alone cannot authorize a macro-aware
  opportunity scan.
  Pair-selection LLM requests now use the scoped request reservation/finish
  ledger with prompt/response hashes, latency, parse status, and provider/model
  budgeting instead of bypassing LLM telemetry.
  `APIManager.get_status()` now fails closed for partially initialized provider
  objects and preserves the legacy status shape when optional OpenBB is absent.
  Runtime dashboard metrics now use durable rolling Sharpe/Sortino calculations
  and label max drawdown as currency; placeholder zeroes and percent/currency
  confusion were removed.

**Evidence:** replay tests demonstrate rejection on stale/gapped/manipulated-
like input and acceptance only after defined confirmation.

## C. Setup library and improvement loop

- [~] Define versioned trend-pullback, range-reversal, liquidity-sweep,
  Asian-range breakout and macro-continuation setup specifications. Mean
  reversion, observed feature detectors and empirical eligibility remain.
  Malformed/non-finite setup indicator inputs now fail closed as
  `unclassified_v1` rather than raising or being classified as a trade setup.
- [~] Give registered setups trigger, invalidation, stop/target style, minimum
  RR, holding window, session suitability and event exclusion metadata. Exact
  broker-price rule replay and evidence remain.
- [x] Assign a deterministic, versioned `setup_id` before validation; reject
  unclassified proposals rather than allowing an LLM-created setup name.
- [~] Record deterministic setup instances through candidate, rejected,
  validated/accepted, filled, missed and closed states in the local journal.
  Explicit invalidation triggers and broker-replay evidence remain.
- [~] Compare setup performance by strategy/symbol/side/setup ID after closed
  records exist. Regime, volatility, spread, session, event, feature/model
  version, and broker-cost segmentation remain.
- [~] Require minimum samples and out-of-sample validation before changing a
  setup rule; `setup_governance.change_gate()` enforces the sample/OOS/version
  prerequisites without mutating the registry.
- [~] Retire/deactivate a setup when live or out-of-sample performance violates
  a predeclared decay threshold. A durable lifecycle state machine
  (`strategy_lifecycle`) now tracks every setup with append-only history; a
  scheduled evaluation consumes the strict labelled dataset and the
  retirement gate to auto-demote DEPLOYED->DECAYING->FROZEN with operator
  alerts, and FROZEN/RETIRED setups are blocked by the deterministic
  validator (fail-closed on gate errors). Promotions, unfreezes, and
  retirements remain explicit operator CLI actions; retirement_gate() returns FREEZE_AND_REQUIRE
  _OPERATOR_REVIEW and never silently disables a strategy. Live closed-sample
  evidence accumulates as trades reconcile.

**Evidence:** versioned setup registry and per-setup research report.

## D. Full lifecycle, costs, and reconciliation

- [~] Implement remaining lifecycle events: `PROPOSED`, `ACCEPTED`, `PARTIAL`,
  `CANCELLED`, `EXPIRED`, `UNRESOLVED`, and detailed `MANAGED` actions.
  Proposal, broker acceptance/pending/rejection, expiry cancellation,
  unresolved, terminal partial-fill and management records are now written.
  Order-intent transitions are now enforced by a durable state machine with
  idempotent repeats and bounded unresolved recovery.
  Setup instances now transition durably through `FILLED` → `PARTIAL`/
  `MANAGED` → `CLOSED`, with terminal-state protection. Broker replay for each
  path and detailed partial-fill/management economics remain.
- [~] Link order, deal, and position tickets correctly for both netting and
  hedging accounts. The execution report ledger now preserves all three IDs
  independently and reconciliation records every observed entry deal; live
  broker-mode replay and statement matching remain.
- [~] Capture open/close/move timestamps, holding time, commission, swap,
  financing, rebates/taxes where exposed, and gross versus net P&L; durable
  trade/execution/lifecycle schemas and reconciliation write these fields when
  broker history supplies them. Close reconciliation now uses the broker deal
  timestamp when available instead of the local wall clock. Existing legacy
  rows still lack trustworthy provenance and authenticated close evidence.
- [~] Record MFE/MAE with broker-quote/execution source, tick age, spread,
  quality and maximum-gap evidence; stale marks are retained but excluded from
  excursion metrics. Position marks and owned protective management now run on
  every fresh quote pulse between candle analyses, while all quote outcomes are
  retained in `market_ticks`; authenticated multi-session mark-frequency
  calibration remains.
- [~] Record manual/unowned observations and refused management attempts in a
  separate `manual_activity` table. TradeManager filters by owned magic numbers
  before every management action, validates broker stop/freeze distances, and
  persists partial/pyramid action state in the authoritative SQLite journal.
  The low-level paper/live SL/TP helper now also requires a positive magic
  number and refuses symbol-only modification.
  SL modifications and partial/full close requests now also write lifecycle and
  broker-execution evidence; authenticated broker replay and complete manual
  statement matching remain.
- [~] Add unresolved-ticket retry, age alert, operator review state, and a
  documented resolution workflow. Reconciliation now creates durable
  `execution_reviews` with first/last detection, count, severity, reason and
  explicit acknowledge/resolve/quarantine CLI actions. Durable age/detection-
  count escalation now runs during position reconciliation without granting
  execution authority. Bounded exponential broker-history retries are now
  durable, restart-safe, and never resubmit an order; broker replay evidence
  remains.
- [~] Persist a daily MT5 bot-magic deal-versus-journal reconciliation that
  reports missing positions and net-P&L differences, while separately counting
  unowned/manual activity. `daily_reconciliation.py` now resolves distinct
  order/deal/position identifiers through the append-only execution ledger and
  preserves missing/ambiguous mappings and malformed/non-finite owned broker
  fields as discrepancies. Production strict mode also filters the requested
  UTC window and rejects missing/malformed owned timestamps or close-entry
  markers; authenticated MT5 execution, order-history and external
  broker-statement comparison remain.
- [x] Reconcile the existing legacy open records with provenance. An
  authenticated MetaQuotes-Demo history/position/order check found no broker
  match for all 20 tickets; each original row is preserved in the archived
  legacy database and retired through an explicit evidence-hashed marker. The
  active DEMO journal is clean, and the stale rewards sidecar was archived and
  replaced with an empty active history; no close, P&L, or label was fabricated.

**Evidence:** daily reconciliation has zero unexplained differences.

## E. Persistent runtime and task orchestration

- [~] Version every proposal with immutable `RiskPolicy`, `StrategySpec`,
  `FeatureSpec`, `DailyPlaybook`, `PromptTemplate`, and `ModelVersion` artifacts;
  configuration hashes are now stored in proposal-input snapshots and proposal
  features. Historical migration and replay evidence remain.

- [~] Persist cooldowns, hourly trade timestamps, daily start equity and peak
  equity in the authoritative journal. `RiskManager.set_store()` now restores
  this state and accepted pending orders register cooldown/rate usage only after
  broker acceptance; authenticated multi-process/restart replay remains. Risk
  sizing now rejects malformed inputs and refuses minimum-volume rounding that
  would exceed the requested dollar risk.

- [~] Implement restart-safe durable cadence records for market health, event
  monitor, reconciliation, session/daily review and weekly research. Dedicated
  job executors, position-manager/backup/archive schedules and fault drills
  remain. The continuous loop now has a configurable five-second market pulse;
  LLM work remains deduplicated and completed-candle gated. Worker heartbeats
  now run on every pulse, runs close as `STOPPED`/`FAILED`, and a new worker
  marks abandoned heartbeat records `STALE_RECOVERED` before starting. All
  scheduled branches now run independently of new-candle eligibility: health
  persists data-source/alert state, event monitoring persists timestamped
  calendar state, position reconciliation runs broker-safe checks, and session
  review records memory metrics. `dual_velocity.py` now dispatches bounded
  fast/slow jobs with deduplication, backpressure, and failure telemetry;
  memory distillation uses the slow lane. Broker-thread safety, full schedule
  migration, and sustained load evidence remain. Schedule claims now preserve
  cadence across downtime, expose missed-run counts, validate claim limits, and
  recover malformed timestamps without crashing the pulse. The operations
  status path now also recovers stale agent heartbeats before reporting active
  runs and exposes the recovery count.
- [~] Give every agent a restricted contract: `agent_contracts.py` defines
  read/write/action capabilities for MacroAnalyst, TechnicalNet, MarketModel,
  RiskController, and QuantitativeCoach; `AgentOperations` enforces both action
  and artifact permissions before durable writes or order intents. Runtime
  scheduled health, macro-review, reconciliation, and research event pairs are
  now contract-covered and regression-tested; authenticated runtime fault-
  injection coverage remains. Order intents additionally require the explicit
  `order_authority` bit, not merely a matching action/artifact name. Journal
  scope validation also runs before operational table creation.
- [~] Add task leases, retry limits, stale-task recovery, queue backpressure,
  heartbeat alerts, and idempotency tests. Durable task leases, bounded
  reclamation, stale recovery and queue-health counters now exist and are smoke
  tested. `AgentOperations.backpressure_status()` now emits explicit running
  and retryable-error thresholds and a reproducible `BACKPRESSURE` status.
  `health_alerts.py` now reports stale run heartbeats, expired leases, and
  exhausted task/review retries; external alert delivery and process-fault
  drills remain. Clock-observation thresholds and latest-per-source drift
  alerts are now validated and fail closed; external clock synchronization
   evidence remains. A durable local `alert_outbox` now provides idempotent
   enqueue/claim/ack/retry/recovery state and the journal CLI exposes the
   transport-neutral handoff. `alert_delivery.py` now delivers claimed alerts
   through explicit, fail-closed `file` (JSONL) or `webhook` transports via
   `journal alerts-deliver`, reusing the durable lease protocol: a crash
   between claim and acknowledge is recovered exactly once by lease expiry,
   per-alert failures retry with backoff and then BLOCK without silent loss,
   and configuration is validated before anything is claimed. Regression
   coverage includes a live local-HTTP webhook test and a crash-recovery
   drill; production endpoint configuration remains an operator action.
- [~] Add provider-specific rate/concurrency/token/cost budgets and record the
  active provider/model on each request. Local provider/model reservations now
  enforce RPM/RPD, concurrency, token and cost ceilings using the configured
  provider name and persist estimates/actual telemetry. Provider usage from
  response metadata is now extracted from OpenRouter/Cerebras SDK responses and
  persisted as numeric token/cost fields without retaining content; alerts and
  live quota validation remain. Signal requests
  and bounded local-memory batches now persist prompt/response hashes, retry or
  attempt count, parse status, response size, and latency; provider-native
  token/cost metadata still needs a live-provider response audit. Reservation
  and completion telemetry now reject malformed identities, non-finite costs,
  and fractional counters before budget accounting. Stale `RESERVED` request
  leases now expire after bounded recovery, freeing concurrency after a crash
  without erasing the attempted request from rate accounting. A valid model
  HOLD is distinguished from a failed response for completion and provider-
  circuit telemetry; no-response/parse-error/error outcomes remain safe HOLDs
  but now count toward health escalation. Pair-selection records also persist
  provider-reported token/cost usage when available, with safe zero defaults
  when a provider exposes no usage metadata. Invalid provider usage (fractional,
  negative, non-finite, boolean, or non-object) fails the request safely with
  zero numeric usage instead of leaving completion unresolved.
- [~] Blocked the previous silent free-model fallback: a failed selected model
  now returns `HOLD`. `llm_router.py` refuses silent provider substitution and
  only traverses configured fallbacks when `allow_failover` or the explicit
  `LLM_TRADER_ALLOW_PROVIDER_FAILOVER=true` flag is present; provider/model
  versioning and live failover drills remain.
- [~] Add circuit health telemetry, failure tiers, cooldown, and manual reset.
  The persistent circuit now exposes states, journals transitions, applies
  tiered 60s/300s cooldowns, escalates to manual-reset deterministic-only mode,
  and has a `provider-circuit` status/reset CLI. Persisted malformed tiers,
  failure lists, or naive/invalid timestamps now fail closed into manual-reset
  mode instead of raising or reopening the LLM path. Valid model HOLD responses
  are counted as successful calls, while no-response, parse-error,
  unexpected-error, and unknown-trace outcomes are counted as failures for
  circuit escalation. Pair-selection success/failure telemetry now uses the
  same circuit, so scanner outages cannot bypass escalation. Live provider
  drills and alert delivery remain.
- [~] Test process restart during analysis, during submission, after fill, and
  during reconciliation without duplicate orders or missed closes;
  `journal restart-status` verifies persisted kill-switch/task/order-intent
  state and duplicate-claim blocking. MT5 network fault injection and full
  fill/close replay remain.

**Evidence:** fault-injection/restart test report.

## F. Local memory completion

- [~] Implement append-only redacted L0 trace storage with source/redacted
  hashes; prompts, responses, MT5 request/response/error paths now write to it.
  Operational-event payloads are redacted and hashed as well. Operator input
  coverage is append-only for operational events, snapshots, configuration
  versions, and clock observations. Access roles, retention/export controls,
  and live execution evidence remain; `journal audit-export` now includes the
  bounded redacted operational evidence bundle alongside trading metrics and
  reports per-layer hash-integrity results. `audit-retention-status` provides
  read-only age/retention counts; deletion is disabled until an operator-
  approved archive policy exists.
- [~] Implement L1 atomic-fact records with observed/inferred/proposed state,
  source, confidence, validity/expiry fields, contradiction and supersession.
  Writes now reject empty source IDs and keys; retrieval excludes proposed,
  superseded, expired, and future-dated facts from proposal context. Fact
  extraction/review rules and live retrieval evidence remain.
- [~] Implement L2 scenario documents from labelled closed trades, with tags,
  sample counts, dated evidence, and balanced win/loss examples. Rebuilds now
  also capture rejected/missed/invalidated setup instances and unresolved order
  intents with separate counts and evidence lists. Reviewer workflow and live
  evidence remain.
- [~] Implement L3 candidate/approved/retired core memory for trading
  personality, risk policy, strategy rules, skills/wiki/access policy, and
  stable lessons. CLI review is required before retrieval; operational audit
  events, supersession UI, and live evidence remain.
- [~] Implement local semantic/vector search only after structured-filter
  retrieval, with source citations and bounded context; `semantic_memory.py`
  uses deterministic local hashed-token vectors over approved SQLite records,
  and proposal retrieval records the semantic layer separately. L2 scenarios
  now follow the same reviewed-only rule: candidates are not retrieved,
  `memory-review scenario` is required for approval, and rebuilds preserve an
  existing approval. JSON query/tag tokenization now matches consistently;
  approved scenarios prefer verified win/loss-balanced evidence and mark
  unbalanced fallbacks explicitly. Semantic retrieval now applies exact
  symbol/strategy/side/setup/regime/session/timeframe/event filters before
  cosine ranking and returns parsed tag metadata. Embedding quality and live
  closed-trade outcome evidence remain.
- [~] Add scenario and memory quality metrics: hit rate, retrieval latency,
  stale/contradictory items, outcome balance, and prompt-token usage. Every
  proposal-context retrieval now records layer, source IDs, result count,
  latency and prompt-size estimate; IDs flow through the order lifecycle to a
  reconciled trade label. Retrieval now also records and enforces an explicit
  point-in-time `as_of` cutoff across structured and semantic layers, so future
  approved outcomes cannot enter replay prompts. The report deliberately calls
  this outcome association, not causal memory hit rate. `memory_quality_metrics()`
  now reports per-layer p50/p95 latency, zero-result rate, association coverage,
  outcome counts, prompt-character/token estimates, and malformed/future cutoff
  errors. Live closed-trade samples and provider-token usage remain required.
- [~] Implement daily narrative, weekly consolidation, monthly stale-memory
  review, and candidate/approved/retired memory workflow. Durable daily,
  weekly and monthly schedules now generate deterministic daily/weekly journal
  summaries, create only reviewable weekly `STABLE_LESSON` candidates when
  closed data exists, rebuild scenarios and archive only stale unreviewed
  candidates; `journal weekly-memory` and `journal monthly-memory` expose the
  workflows with explicit ISO-date/Monday period validation, while live
  schedule/recovery evidence remains.
- [~] Ensure LLM memory distillation uses verified local records only and does
  not recursively store recalled content as new evidence. Distillation consumes
  only allow-listed closed-trade/event facts, blocks unverified outbox sources
  before the LLM call, and writes candidates that cannot be retrieved until
  explicitly approved. Completed macro event reactions now enter the same
  verified outbox; provenance/replay coverage remains.

- [~] Add restart-safe short-term working memory and deterministic task-graph
  compaction. `AgentOperations` now stores redacted, revisioned working-memory
  entries, applies explicit point-in-time and prompt-size bounds, preserves
  safety/error/position entries during compaction, and emits a source-linked
  compaction summary. The continuous pulse retrieves strategy/shared event
  context, event-monitor updates persist the latest calendar state, and journal
  CLI status/context/compact commands expose the workflow. Recovery-load and
  long-run throughput evidence remain.

**Evidence:** every retrieved memory item has source ID, timestamp, status,
confidence, and evidence link.

## G. Macro research and causal knowledge

- [~] Select permitted primary sources and rights/retention rules for central
  bank releases, official statistics, FRED/IMF/BIS datasets, and news;
  `macro_source_policy.py` records PRIMARY/RUNTIME_FEED/UNVERIFIED tier and
  retention metadata in each source payload. Historical source catalog and
  operator rights review remain.
- [~] Build local source ingestion with payload hash, time/version/rights fields.
  `macro_source_policy.py` metadata is now required before causal-edge creation
  or LLM extraction; unverified sources remain observation-only. Structured
  facts retain source span, source policy, rights, and extractor provenance;
  malformed source identity/payloads are rejected or classified unverified;
  provider ingestion and rights review remain.
- [~] Create a local causal graph entity/edge schema with confidence, sign,
  validity, counter-evidence link and review status. `macro_graph.py` now keeps
  an append-only proposal/review/revision history and forces amended edges back
  to candidate review; it now rejects edges whose source policy is not
  allow-listed. Revision-history UI and cited source ingestion policy remain.
- [~] Define source-linked historical-regime research templates for 1970s
  inflation, 2008 GFC, 2020 pandemic, 2022 inflation/rates, and 2023 banking
  stress (`historical_regimes.py`, `journal historical-status`). They contain
  no fabricated observations and are never execution authority; operator
  ingestion of point-in-time observations, full datasets, and regime replay
  evidence remain.
- [~] Create event-reaction records for actual/forecast/previous, surprise and
  arbitrary 1m/5m/15m/1h/4h/1d window payloads. The local event tracker now
  automatically measures those windows after a numeric release is observed,
  preserving actual elapsed time and anchor quality. The continuous event
  monitor also ingests deduplicated calendar observations as source-linked
  records even when no analysis candle closes; malformed/non-finite/fractional
  measurement windows fail closed. Verified historical data and broker/calendar
  timing replay remain.
- [~] Retrieve current macro state by quantitative fingerprint plus semantic and
  causal filtering; `MacroGraph.similar_event_reactions()` now returns dated
  surprise similarity, differences, source links, and uncertainty. Macro
  reaction, causal-edge, and runtime proposal retrieval now enforce an explicit
  point-in-time cutoff. Semantic regime/context filters and verified historical
  coverage remain.
- [~] Produce a daily macro playbook and a 48-hour post-event review that creates
  only a candidate lesson; the scheduler now tracks completed reactions by
  source-linked ID and writes unapproved narrative candidates without changing
  permission. Source-policy and historical-regime evidence remain. Macro graph
  confidence and event-reaction inputs now reject non-finite or malformed
  values, and malformed calendar importance is retained as an auditable memory
  quality marker rather than crashing the cycle. All durable memory confidence
  fields now share the same finite [0,1] validation.

**Evidence:** a live macro synthesis can be reproduced from cited, dated source
data and does not treat causal claims as certainty.

## H. Metrics, labels, and dashboards

- [~] Add deterministic target, stop, breakeven, time, event, manual, generic
  win/loss labels with rule/version and auto-review status. `setup_labels` now
  also persists immutable AUTO labels for rejected, missed, invalidated and
  unresolved setup/order-intent outcomes at runtime and during rebuild, plus
  distinct cancelled, expired, and data-quality-blocked labels; human review
  workflow now records operator/review timestamps through `memory-review
  setup-label`; data-quality policy refinement remains.
- [~] Calculate journal fill/pending/expiry/rejection/unresolved rates and
  recorded requested-versus-accepted slippage. `qualified_metrics()` now also
  emits partial/cancellation/expiry/rejection/unresolved rates, broker latency
  percentiles, spread-at-submit/fill, implementation shortfall, requotes, and
  execution-reported commission/swap; adverse-selection analysis still requires
  authenticated broker fills.
- [~] Calculate MFE/MAE R summaries, R expectancy and holding-time from durable
  marks/closed trades, with explicit nulls where unavailable. The qualified
  report now also exposes sample-gated Calmar/Omega and a reproducible,
  assumption-labelled 10%-ruin bootstrap estimate; time-to-event and
  distribution breakdowns require broker-reconciled samples.
- [~] Calculate calibration, Brier/log loss, false positives, baseline lift,
  raw-proposal versus validated-proposal expectancy, and setup decay;
  `baseline_research.py` reports Brier/log loss/accuracy/expectancy and
  train-only threshold selection on untouched folds; `qualified_metrics()` now
  reports descriptive confidence bins/Brier/log loss when trade confidence is
  present, plus strict confidence-based false positives/negatives and a
  proposal-quality report. Raw-vs-validated lift remains explicitly
  `INSUFFICIENT_DATA` without a labelled counterfactual replay; setup decay and
  live samples remain. Strict monthly setup-decay comparisons now include a
  30-trade minimum and operator-review flag without automatic retirement.
- [~] Enforce a pre-trade projected margin/free-margin/stop-out buffer and
  symbol/driver margin concentration gate from fresh MT5 data. Historical
  portfolio exposure, margin-stress, drawdown/recovery, CVaR after adequate
  data, and risk-of-ruin analytics remain. `margin_safety.py` now rejects
  malformed/non-finite policy, account, risk, position-margin, and bucket
  inputs; the live execution path records the assessment before intent
  creation.
- [~] Build session/day/week/month dashboard with sample/date/cost/model/data-
  mode/in-sample/out-of-sample labels on every result; `qualified_metrics()`
  now emits day/week/month period quality with explicit sample and status
  qualifiers, plus sample-gated growth quotient, MAE drawdown compression,
  and explicit macro-bias/trade-side alignment efficiency. The new read-only
  `journal dashboard-status` report assembles qualified research, proposal,
  trade, execution, portfolio, period, memory, alert, and readiness panels;
  the desktop dashboard still needs richer interactive session panels and
  external alert delivery.
- [~] Add alert thresholds for unresolved tickets, stale data, missing journal
  fields, abnormal slippage, provider costs, drawdown, drift, and backups;
  `health_alerts.py` and `journal alerts` now evaluate durable thresholds.
  It also alerts on blocked unverified-memory sources. The active DEMO journal
  is clean but has zero closed research observations, so learning remains
   blocked by the sample gate rather than by legacy rows. `journal
   alerts-enqueue/status/claim/ack/recover` now persists and reconciles an
   idempotent operator-alert outbox, and `journal alerts-deliver` now performs
   the acknowledgement through explicit file/webhook transports with
   retry/block and crash-recovery regression coverage; a production endpoint
   and live delivery evidence remain operator-configured. The continuous
   market-health schedule writes the same
   outbox idempotently after each health pulse without changing order authority.

**Evidence:** a reproducible report matches journal and broker history.

## I. Research, ML, and promotion

- [~] Build leakage-safe labelled research dataset from point-in-time snapshots;
  `research_validation.labelled_dataset()` and `journal dataset-status` now
  fail closed on missing labels, legacy/unknown feature hashes, malformed
  payloads, invalid/non-finite risk or outcomes, non-finite feature scalars,
  or a missing/mismatched hash of the exact scalar values used at proposal
  time. Reversed/malformed label horizons and feature timestamps later than
  entry are rejected as future-data leakage. Synthetic fixture rows are now
  excluded unconditionally (payload flag, generator source markers, reserved
  exit reason) with an auditable exclusion counter, and the demo sample
  generator refuses journals holding broker-reconciled trades unless
  explicitly overridden; the active journal's 360 legacy synthetic rows were
  backed up and quarantined. `setup_metrics()` and
  `promotion_gate()` now consume only this dataset and require a purged
  walk-forward fold before reporting a setup as eligible.
  Historical close/reconciliation evidence and a sufficiently large
  current-schema sample remain required.
- [x] Prevent qualified metrics from treating legacy/unlabelled closes as
  research evidence. `journal_audit.qualified_metrics()` now exposes a strict
  `research_quality` view with deterministic-label, point-in-time, cost-model,
  date-range and exclusion metadata; raw operational counts remain explicitly
  descriptive.
- [~] Implement purged/embargoed K-fold and chronological walk-forward helpers
  using each trade's open-to-close label horizon. Dataset extraction is now
  exposed through `labelled_dataset`; event horizons and recorded validation
  runs remain. Invalid split parameters/horizons now fail closed to empty folds,
  and malformed promotion metrics/stress fields return explicit floor reasons.
- [~] Run simple deterministic baselines and cost-aware benchmark tests first;
  `baseline_research.py` and `journal baseline-status` now score no-skill,
  fixed-random, and EMA-alignment models only on purged chronological folds.
  Each run is now appended to the authoritative `research_validation_runs`
  ledger with dataset/report hashes and configuration; `journal
  validation-runs` lists the immutable evidence, and database triggers reject
  mutation/deletion. Promotion packages now carry the same run records under
  `research_validation_runs`, and `journal audit-export` includes them in the
  combined evidence artifact. `TradingStore` now creates this ledger during
  normal journal initialization. Current status is
  `INSUFFICIENT_DATA` because no reconciled closed trades have passed the
  labelled-dataset gate. Baseline, bandit, and supervised entry points now
  reject weak (<100) or malformed research configurations before evaluation.
- The promotion gate and setup-governance APIs reject any requested sample
  threshold below the mandatory 100-trade policy floor, returning a blocked
  configuration result rather than weakening the research requirement.
- [~] Train supervised probability baseline only after adequate labelled samples.
  `supervised_training.py` and `journal supervised-status` now implement a
  deterministic, point-in-time logistic baseline with purged walk-forward
  folds; reports include cost-model/point-in-time metadata, training-data hash,
  and out-of-sample status. Reports are durably recorded in
  `research_validation_runs`; current journal evidence is `INSUFFICIENT_DATA`,
  so no artifact is trained or promoted. Weak or malformed training settings
  are rejected before any artifact path is touched.
- [~] Add calibration/threshold selection on validation data; the baseline
  report selects take/skip thresholds on training folds only and reports Brier,
  log loss, accuracy, expectancy, and net R on untouched test folds. A real
  model calibration artifact still requires adequate labelled data.
- [~] Add contextual bandit only for take/skip/confidence after baseline success;
  `contextual_bandit.py` and `journal bandit-status` evaluate setup-aware
  take/skip decisions on purged chronological folds, but current sample size
  is insufficient. Reports include dataset hash/cost model and explicit
  out-of-sample status and are persisted in `research_validation_runs`; no
  bandit output can affect execution.
- [~] Repair RL transitions with actual exit state and use RL first for offline
  position-management simulation; confirmed fills now enter the monitor,
  reconciled closes carry terminal exit state/risk/R/duration, and unverified
  closes cannot update RL. Offline promotion is still blocked.
- [~] Build the RL research state from completed, point-in-time values rather
  than forming bars or placeholders. `_build_rl_state()` now includes completed
  RSI/MACD/EMA/ATR/ADX, S/R proximity, volatility regime, trend strength,
  higher-timeframe trend, event state and signed news surprise/sentiment;
  offline sample validation remains required.
- [x] Keep RL updates and RL context out of live broker cycles by default;
  paper/research cycles may opt in. Even when the environment switch is set,
  non-paper cycles now require the separate research-influence gate with a
  current 100+ labelled sample, qualifying purged/OOS evidence, a SHA-256
  evidence digest, and expiring operator approval. LIVE runtime cycles do not
  update the Q-table; live learning belongs to a separately controlled
  offline job.
- [~] Add model/experiment registry, reproducibility seed, training data hash,
  feature schema, promotion/rollback, drift, and human approval;
  `model_registry.py` persists candidate/review/rollback evidence and now
  requires explicit walk-forward, purged-validation, point-in-time, sample-
  gate, cost-validation, and artifact-reference evidence before approval. The
  journal CLI exposes status/review/drift, but no registry status grants live
  execution authority.
- [~] Apply HMM/GARCH/Kelly/Optimal-f only as capped advisory adjustments after
  stability, drawdown, and walk-forward evidence; HMM/GARCH state is now
  restart-persistent and deduplicated by completed-bar timestamp, while its
  bounded state is now included in LLM/proposal evidence. Malformed close
  inputs fail closed, Kelly remains sample-gated, and all outputs remain
  advisory. Model-family/setup-governance thresholds and HMM/GARCH/Kelly state
  inputs now also fail closed on malformed/non-finite values; empirical
  promotion evidence remains required. Adaptive state restoration now returns
  unavailable advisory evidence instead of crashing on malformed persisted
  HMM/GARCH sections. `research_influence_gate.py` now blocks adaptive, RL,
  and Kelly influence on demo/live proposals unless the current labelled
  sample/OOS gates, evidence digest, and expiring operator approval all pass;
  paper mode remains available for research.

**Evidence:** repeated out-of-sample results beat random and simple baselines
net of costs, with sufficient samples and approval.

## J. Backtest, demo, and funded rollout

- [~] Implement broker-realistic simulation for exact symbols: `broker_replay.py`
  now requires explicit symbol/cost assumptions and models stops, commissions,
  swaps, spread-event spikes, slippage, latency, fills, closures, and weekends.
  Replay now rejects malformed/non-finite OHLC and invalid stop/target geometry,
  and derives swap duration from bar timestamps with an explicit interval
  fallback. It records direction-correct MFE/MAE in price, currency, and R
  units from completed bars without future-data access. Replay specifications
  also reject non-finite costs, invalid latency or weekend flags, and malformed
  intent indices. Broker-specific calibration
  and replay-vs-demo evidence remain.
  The direct paper broker refuses NaN/zero quotes instead of creating invalid
  positions.
- [~] Add parameter perturbation, bootstrap, Monte Carlo sequence, regime,
  cross-symbol, and stress testing; `research_stress.py` and
  `journal stress-status` provide deterministic sequence/cost-buffer stress,
  but correctly labelled sample size is currently insufficient. Stress inputs
  now fail closed for non-finite R-values, unsafe risk fractions, malformed
  paths/seeds, invalid cost buffers, and thresholds below the 100-trade policy
  floor.
- [~] Run demo forward test and produce backtest-versus-demo discrepancy
  report; `forward_test_report.py` now enforces a hard 100-observation floor on
  both cost-aware sides, rejects malformed metrics/weak thresholds, and records
  cost-model compatibility, while authenticated MT5 demo collection remains.
- [~] Collect 100+ correctly reconciled closed trades per setup class across
  multiple regimes before learned promotion. `sample_gate.py` and `journal
  sample-gate-status` now qualify each setup by labelled sample count, known
  regime count, date range, and point-in-time dataset status; current evidence
  remains insufficient because no reconciled closed rows exist.
- [~] Require explicit operator approval for a tiny live rollout; verify kill
  switch, reconnect, reconciliation, and manual recovery first. `live_rollout_gate.py`
  and `journal live-approval` now enforce a persistent, expiring approval with
  content-hashed file evidence manifest and risk cap; evidence drift or a
  missing manifest permanently invalidates the approval until re-approval,
  while reference-only evidence is rejected and
  `journal live-approval --approval-action verify` exposes the diagnostic
  checks. `journal promotion-package` combines integrity, security, sample,
  research, stress, forward-test, and approval gates into a fail-closed review
  artifact with a schema version and deterministic package hash without
  granting execution authority; approval and status responses now also expose
  `execution_authority=false`. Actual operator approval and recovery evidence
  require at least one existing file artifact and remain external;
  `promotion-verify` is a fail-closed artifact verification
  command. Malformed persisted approval manifests or policy state remain
  inactive/rejected rather than crashing verification.

**Evidence:** documented, reproducible validation and forward-demo agreement.

## K. Security and operations

- [~] Move secrets to a secure secret store/environment, rotate old values, and
  confirm no credentials are committed or captured in L0 traces. `security_audit.py`
  and `journal security-status` provide a redacted preflight; secure-store
  migration, rotation, and post-rotation verification remain external. The
  scanner now preserves `#` in password values, filters code references and
  explicit redaction/type-annotation code, and reports test/build fixture
  findings separately from production blockers; it also fails closed when an
  on-disk L0 trace directory is present and reports
  risky credential-file paths found in Git history. History scrubbing/formal
  review remains an operator action.
- [~] Separate paper/demo/live/research databases and access roles;
  `journal_scope.py` records mode/account fingerprints and fails closed on a
  mismatch, with `journal scope-init`/`scope-status` CLI checks. Deployment
  path provisioning and OS/database access roles remain. DEMO/LIVE scopes now
  require a non-empty account fingerprint and incomplete live/demo scope rows
  report `INVALID` instead of appearing safely scoped.
- [~] Implement encrypted backup/export, restore tests, retention, deletion,
  redaction, audit export, and data-quality lineage policy; `journal_backup.py`
  now creates hashed SQLite backups plus AES-256-GCM encrypted backups, writes
  manifests, verifies integrity, and refuses unverified/non-explicit restores.
  CLI encrypted-backup create/status/restore actions keep passphrases in an
  environment variable. `backup-prune` is a dry-run-first, recognized-artifact
  retention planner with an explicit apply flag and minimum-copy guard.
  `journal audit-export` writes the integrity, baseline, qualified-metric and
  memory-quality reports as one evidence artifact. Deployment-level lineage
  policy remains.
- [~] Write and test incident runbooks for broker/LLM/news/database outage,
  stuck/duplicate order, bad data, state corruption, and emergency stop;
  `INCIDENT_RUNBOOKS.md` documents the kill-switch-first recovery order and
  commands. `incident_drills.py` now runs broker-free kill-switch, duplicate-
  intent, provider-circuit, malformed-calendar/news, bad-data, market-closed,
   and restart-persistence drills; authenticated broker fault injection
   remains. Alert-delivery fault drills now cover crash-between-claim-and-
   acknowledge lease recovery, retry-then-block, and live local-webhook
   delivery through `alert_delivery.py`; production endpoint configuration
   remains.

**Evidence:** restore and incident drills pass with documented results.
