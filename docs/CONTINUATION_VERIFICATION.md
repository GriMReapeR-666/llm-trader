# Continuation verification

This report records the latest code-controlled work. It does not claim broker
connectivity, profitability, or live readiness.

- `trading_safety.py`: Added explicit fail-closed numeric/bounds validation for `equity`, `new_risk`, `bucket_risk`, and `correlated_risk` parameters in `PortfolioRiskArbiter.permit()`. Fixed `equity_peak` persistence initialization so peak equity is saved reliably on first evaluation. 5 focused tests in `test_trading_safety.py` pass.
- `macro_extraction.py`: same configured LLM provider can extract bounded,
  source-span-cited candidate principles, causal edges, precedents, and
  uncertainties. Writes are `PROPOSED`/`CANDIDATE_REVIEW_REQUIRED` only, and
  unverified sources are rejected before the LLM call.
- `macro_source_policy.py`/`macro_graph.py`: runtime calendar and allow-listed
  primary sources retain tier/rights metadata; unverified sources remain
  observation-only and cannot create causal edges. Five focused source-policy
  and causal-graph tests pass.
- Candidate L1 macro facts now retain source span, URL/publisher, rights note,
  source-policy tier, and extractor schema instead of dropping citation data.
- `TradingMemory.record_fact` now rejects empty source IDs and fact keys, with
  source-preservation regression tests, so L1 records cannot be created without
  an attributable evidence handle.
- `AgentOperations.event` now redacts operational payloads and stores a stable
  payload hash; regression coverage confirms secret text is not persisted.
- Point-in-time operational events, data snapshots, configuration versions, and
  clock observations now have immutable UPDATE/DELETE triggers; append-only
  mutation tests pass.
- `journal audit-export` now writes trading audit/metrics plus a bounded,
  redacted operational bundle containing events, raw traces, snapshots,
  configuration versions, and clock observations. It validates stored payload
  hashes and reports per-layer integrity; the export remains explicitly
  non-authoritative.
- `journal audit-retention-status` now reports age-based retention counts for
  operational events, raw traces, snapshots, configuration versions, clock
  observations, and LLM requests. It is read-only and explicitly disables
  deletion of append-only evidence.
- `supervised_training.py` and `contextual_bandit.py` now report dataset
  point-in-time status, cost model, training-data hash, and explicit
  out-of-sample status; these fields do not grant execution authority.
- `trading_memory.py`: verified-memory review now records reviewer and review
  timestamp; long-horizon monthly archive uses timezone-safe `timedelta` math.
- `journal_audit.py`: qualified metrics now include explicit segmentation by
  setup/symbol/side/strategy/timeframe/session/regime/volatility/spread/event
  and model/prompt/feature versions, plus sample-gated monthly comparisons.
- `model_family_gate.py`: ordered offline family plan with minimum sample and
  purged-fold gate; no family is trained or promoted automatically.
- `execution_policy.py`: live/demo plans require an approved per-symbol broker
  calibration artifact; missing policy fails closed.
- `forward_test_report.py`: backtest-versus-demo discrepancy report with a hard
  100-observation floor on both sides, malformed/non-finite metric rejection,
  and explicit cost-model comparison; it remains descriptive only.
- `journal_migration.py`: migration now initializes both authoritative trade
  tables and memory tables before importing legacy rows.
- `risk_manager.py`/`PerformanceMonitor`: fixed step-size floating-point
  truncation, added explicit drawdown check, capped default confidence sizing,
  and restored the history/trades compatibility view.
- `security_audit.py`: added a non-destructive, redacted scan for exposed
  credential-like values, risky tracked files, and L0 trace directories;
  `journal security-status` never prints secret values and never grants
  execution authority. Assignment scanning preserves password characters such
  as `#`, ignores source-code references such as `os.environ.get(...)`, and
  reports test/build fixture matches separately from production findings so a
  fixture cannot mask the real credential-rotation gate.
- `supervised_training.py`: added a deterministic logistic baseline that uses
  only labelled point-in-time rows and purged walk-forward folds; it refuses
  to train or write an artifact until 100 training rows plus a test fold exist.
  `journal supervised-status` currently reports `INSUFFICIENT_DATA`.
- `mt5_trader.py`: completed-candle attention now separates observation from
  durable idempotency. A strategy claims its candle task before advancing any
  checkpoint, allowing lease recovery after a crash or provider failure instead
  of silently skipping that candle.
- The direct `PaperBroker` path now fails closed on non-finite/zero quotes and
  refuses to create synthetic positions whose P&L/R/MFE/MAE would be invalid;
  broker-realistic cost behavior remains in the explicit replay harness.
- `broker_replay.py` now validates every completed OHLC bar and every intent
  before scoring, rejects invalid stop/target geometry, and calculates swap
  duration from timestamps (or its declared bar-duration fallback). This is
  replay safety only; it does not substitute for broker reconciliation. Replay
  specifications now reject non-finite costs, non-integer latency, and
  malformed weekend flags; malformed intent indices are rejected per intent.
- `data_requirements.py` now validates setup identifiers, snapshot shape,
  calendar flags, DOM depth/capture metadata, and cross-market payloads. A
  malformed DOM or macro payload fails closed instead of raising or being
  treated as available.
- `margin_safety.py` now validates margin policy bounds, finite broker account
  values, risk inputs, position-margin estimates, and driver-bucket shapes.
  Invalid margin evidence is rejected rather than skipped; the live planning
  path already records the assessment before creating an order intent.
- `macro_graph.py` now rejects non-finite causal-edge confidence and malformed
  event-reaction identities, while event numeric normalization converts NaN
  values to explicit unknowns rather than persisting invalid surprises.
- `macro_source_policy.py` and source ingestion now normalize malformed URL,
  publisher, rights, title, and payload inputs to an unverified/rejected state;
  malformed source identity cannot become an approved causal source.
- `trading_memory.py` now rejects non-finite fact confidence and records an
  auditable malformed-importance marker instead of crashing on bad calendar
  payloads; playbook generation ignores malformed event records. Verified and
  core-memory confidence is governed by the same finite [0,1] check.
- `agent_operations.py` and the pending-order reconciler now persist bounded
  exponential broker-history retry state (`retry_count`, retry timestamps,
  next retry) for unresolved outcomes. Retries only re-check broker evidence;
  they cannot resubmit orders or grant execution authority.
- `health_alerts.py` now validates alert thresholds and reports stale agent
  heartbeats, expired task leases, exhausted task retries, and exhausted
  execution-review retries as durable operational alerts.
- `agent_operations.py` now validates clock-observation source/threshold
  inputs; `health_alerts.py` evaluates the latest clock observation per source
  and raises a fail-closed drift/malformed alert. `data_quality.py` rejects
  malformed or non-finite clock offsets and policy thresholds before a live
  snapshot can pass.
- `AgentOperations.reserve_llm()` and `finish_llm()` now reject malformed
  provider/model identities, non-finite costs/latency, and fractional token,
  attempt, or response-size counters before telemetry enters budget queries.
- `data_quality.py` now rejects malformed OHLC/DOM containers, non-finite OHLC
  values, malformed DOM depth, and non-finite bid/ask/spread values before a
  market snapshot can pass the live-data gate.
- `feature_schema.py` now rejects malformed feature containers, non-finite
  scalar values, invalid source/index metadata, malformed timestamps, and any
  completed bar later than capture time; this preserves point-in-time training
  boundaries before feature hashes are trusted.
- `execution_planning.py` and `mt5_trader.py` now provide a separate emergency
  market-exit path that requires an existing ticket, positive owned magic number,
  explicit side/volume, and an auditable reason; ordinary planned entries remain
  pending limit/stop orders.
- Low-level SL/TP modification now requires a positive magic number and filters
  both paper and live positions by ownership, preventing symbol-only changes to
  manual or other-strategy positions.
- `live_rollout_gate.py` adds a persistent, expiring tiny-live approval with an
  evidence manifest/hash and a maximum 0.1% risk fraction; file-backed
  evidence is re-hashed on every status/permission check, so drift or a
  missing manifest permanently invalidates the approval until re-approval;
  `journal live-approval
  --approval-action verify` exposes the diagnostic checks. Every `--live`
  command still requires both this approval and the existing environment
  opt-in.
- `incident_drills.py` and `journal incident-drills` run broker-free fault
  injection for kill-switch persistence, duplicate-intent idempotency, provider
  outage degradation, malformed calendar/news and market data, market-closed
  filters, and restart recovery; the report explicitly carries no execution
  authority.
- `broker_preflight.py` now validates required account/symbol contract fields;
  `journal broker-preflight-status` reports missing data without inventing it,
  and `execution_policy.py` rejects stale or mismatched broker fingerprints.
- `sample_gate.py` and `journal sample-gate-status` qualify each setup class by
  100 labelled closed rows, at least two known regimes, date range, and the
  point-in-time dataset gate; the current journal has zero eligible classes.
- `mt5_auth_diagnostics.py` and `journal auth-status` report account-file field
  shape and classify terminal probes without printing credentials. The current
  local shape is complete, but both default and explicit terminal probes still
  return `-6` authorization failure.
- `execution_policy.py` now exposes a fail-closed candidate/approval workflow:
  `policy-candidate` binds a template to the current validated broker
  fingerprint, while `policy-approve` requires a reviewer and persists the
  calibrated artifact. Approval records calibration evidence only and does not
  grant execution authority.
- `AgentOperations.backpressure_status()` now reports durable queue counts,
  explicit running/retryable-error thresholds, and a reproducible
  `HEALTHY`/`BACKPRESSURE` status for worker gating.
- `legacy_reconciliation.py` now supports a confirmation-gated
  `legacy-quarantine` marker that records operator provenance without closing
  or rewriting an unverified trade; MT5 reconciliation remains mandatory.
- Protective position management now runs on fresh MT5 quote pulses between
  candle triggers, records fresh MFE/MAE marks for owned tickets, rejects stale
  quotes, and validates broker stop/freeze distances before SL changes. Every
  position-attention quote, including stale and missing/invalid outcomes, is
  retained in the SQLite `market_ticks` ledger. The LLM loop exposes
  `--poll-interval` (default five seconds) but remains completed-candle/event
  gated.
- Trade-manager partial-exit and pyramid state now reloads from the authoritative
  SQLite `state` table when attached; JSON sidecar state remains only as a
  compatibility fallback for standalone legacy use.
- `fetch_ohlc()` now refuses Yahoo or synthetic fallback whenever an MT5
  terminal is initialized but returns no bars; this prevents an authenticated
  trading path from analyzing fabricated or non-broker candles. The guard is
  covered by a fail-closed regression test.
- `AgentOperations` now closes durable runs on normal/error shutdown, emits a
  pulse heartbeat even when no LLM candle is eligible, and marks abandoned
  heartbeat records `STALE_RECOVERED` before starting a replacement worker.
  This is covered by run-finish/recovery tests.
- `journal_audit.py` now validates `market_ticks` quality semantics and reports
  its count, so missing/stale quote evidence is included in integrity reports.
- `mt5_calendar_wrapper.py` now handles real ctypes structures and test/mocked
  providers through one allocation/call path, maps signed MT5 error codes
  correctly, and preserves fail-closed calendar errors. The full calendar
  wrapper suite passes.
- Interactive MT5/GUI diagnostic scripts now skip safely during headless
  pytest collection instead of initializing a terminal or display at import.
- `promotion_package.py` and `journal promotion-package` now combine integrity,
  sample, research, stress, forward-test, and tiny-live approval evidence into
  a versioned review artifact with a deterministic package hash. It remains explicitly `NOT_READY` and
  `execution_authority=false` with the current empty closed-trade sample.
- `journal promotion-verify` recomputes the package schema/hash and rejects a
  modified artifact; verification is evidence-only and cannot authorize orders.
- L2 scenario memory is now review-gated: candidate scenarios cannot enter
  proposal context, `memory-review scenario approve/retire` records reviewer
  provenance, and weekly rebuilds cannot demote approved scenarios.
- L1 atomic-fact retrieval now excludes proposed, superseded, expired, and
  future-dated facts from proposal context while retaining them for audit.
- L2 scenario rebuilding now retains rejected/missed/invalidated setup examples
  and unresolved order intents with separate counts, so pattern retrieval can
  be reviewed against both successful and failed decisions.
- `setup_labels` now persists deterministic, source-state-derived AUTO labels
  (`REJECTED_VALIDATOR`, `MISSED_UNFILLED`, `CANCELLED_UNFILLED`,
  `EXPIRED_UNFILLED`, `INVALIDATED_SETUP`, `DATA_QUALITY_BLOCKED`, and
  `UNRESOLVED_BROKER`) for non-trade setup/order-intent outcomes. The journal
  audit validates their attribution and `journal setup-labels` lists them;
  runtime setup transitions and intent resolution write them immediately, and
  stale/missing/invalid-data rejection reasons are classified as
  `DATA_QUALITY_BLOCKED`;
  these labels are evidence only and cannot grant execution permission.
- Setup-label review is now explicit and provenance-bearing: `memory-review
  setup-label list|approve|retire --subject-key ...` records reviewer and UTC
  review time without changing the deterministic label or execution rules.
- `local_llm_memory.py` now allow-lists distillation sources against durable
  closed-trade labels or recorded macro/event reactions. Unknown or mismatched
  outbox records are quarantined as `BLOCKED` before any LLM call, and blocked
  counts are exposed in memory/operations metrics; distilled records remain
  review-gated candidates.
- Completed macro event reactions are now queued as verified `event_reaction`
  sources for the same configured LLM distiller; the queue remains advisory and
  candidate/review-gated.
- The pair scanner no longer sends a static “check economic calendar” string to
  the LLM. `_pair_selection_context()` records timestamped calendar, news and
  FRED status/values and returns no pairs when the required calendar is missing.
- `_build_rl_state()` now reads completed-bar feature values, uses explicit
  unknown/neutral states for missing higher-timeframe data, and includes S/R,
  volatility, trend-strength, event-state and signed news fields; the RL
  state-key builder preserves missing numeric features as `unknown` rather
  than converting them to neutral zeroes; it never grants execution authority.
- Health alerts now distinguish blocked unverified-memory sources from provider
  failures, so source-governance violations remain visible to operators.
- `journal readiness-status` now combines authorization, broker-preflight,
  journal-integrity, legacy-reconciliation and labelled-sample gates into one
  fail-closed operator report; it never grants order permission.
- Continuous maintenance schedules are processed before the no-new-candle
  HOLD return. Health, event-window, position-reconciliation and session-review
  schedules have explicit durable/telemetry handlers instead of being silently
  acknowledged; candle-triggered LLM analysis remains separately gated.
- Local semantic retrieval now tokenizes JSON consistently and prefers approved
  scenarios with verified win/loss balance; unbalanced results carry an explicit
  incomplete-evidence warning.
- The desktop dashboard no longer drops multi-timeframe or metric updates: it
  renders alignment, RL/technical values and position refreshes from supplied
  durable data without inventing missing values.
- Macro causal edges now have append-only proposal/review/revision history;
  amendments are demoted to candidate status until explicitly reviewed again.
- Explicit `strategies=both` now always resolves to independent swing and scalp
  engines; an explicit lower or higher timeframe no longer silently disables
  the other engine.
- AUTO/scanner-selected symbols now undergo one-time broker-specification
  preflight before their first cycle, instead of bypassing startup verification.
- Qualified journal metrics now include explicitly assumption-labelled,
  sample-gated Calmar/Omega and risk-of-ruin estimates in addition to the
  existing R-based Sharpe/Sortino/CVaR/Kelly fields.
- Holiday detection now stops trading while keeping continuous maintenance
  alive; the outer loop no longer exits before reconciliation and health tasks.
- Quiet-period event monitoring now writes deduplicated, source-linked calendar
  observations to the macro journal, rather than waiting for a candle-triggered
  analysis cycle.
- `journal_audit.qualified_metrics()` now reports execution status counts/rates,
  p50/p95 broker execution latency, spread at submission/fill, implementation
  shortfall, requote totals, and broker-reported commission/swap alongside
  requested-versus-accepted slippage.
- Memory retrieval now carries an explicit point-in-time `as_of` cutoff through
  structured and semantic layers; proposal telemetry stores the cutoff and the
  runtime proposal path uses the captured market timestamp. Future-approved
  narratives/scenarios/lessons are therefore excluded from historical context.
- Macro reaction similarity, recent reaction lists, and approved causal-edge
  context now apply the same snapshot cutoff and expose `retrieval_as_of`, so
  future event outcomes cannot leak into a historical proposal.
- Local semantic retrieval now applies exact structured state filters before
  hashed-vector ranking and returns parsed tag metadata; a high textual match
  from another symbol or setup is excluded before it can reach the prompt.
- OpenRouter and Cerebras SDK usage metadata is now normalized to numeric
  prompt/completion/total tokens and cost, then persisted in `llm_requests`;
  prompts, responses, and credentials are not stored as usage telemetry.
- The same qualified report now emits sample-gated descriptive confidence
  calibration bins, Brier score, log loss and 0.5-threshold accuracy. Missing
  confidence remains `INSUFFICIENT_DATA`; this does not promote a model.
- The qualified report now separates descriptive closed-row diagnostics from a
  strict `research_quality` view. The latter is sourced only through
  `research_validation.labelled_dataset()`, exposes every exclusion reason,
  requires deterministic labels and hashed point-in-time features, and reports
  a strict sample/date/cost/OOS status. Top-level `sample_size` now means the
  research-qualified sample; `descriptive_sample_size` is retained for audit
  visibility. Raw legacy rows therefore cannot inflate Kelly, risk, or
  promotion evidence.
- Memory quality telemetry now reports per-layer p50/p95 latency, zero-result
  rate, outcome-association coverage and counts, prompt-character/token
  estimates, plus malformed/future `as_of` cutoff errors. Predictive
  `hit_rate` remains unset because retrieval association is not causal
  evidence; only reconciled labelled outcomes are counted as associations.
- `journal audit-export` now carries the same memory-quality report beside
  trading, operational, and immutable research-run evidence, so an exported
  handoff cannot omit retrieval provenance or prompt-size telemetry.
- Research promotion and setup-governance gates now reject caller-supplied
  thresholds below the mandatory 100 correctly labelled closed-trade minimum.
  Invalid thresholds return a blocked configuration result instead of silently
  weakening the evidence requirement; execution authority remains false.
- `qualified_metrics()` now exposes `proposal_quality`: total/approved/rejected
  proposals, approval rate, strict closed-trade linkage, confidence-based false
  positives/negatives, and explicit raw-versus-validated/baseline-lift status.
  Rejected proposals are never scored as losses without a valid point-in-time
  counterfactual replay.
- The same report now exposes strict monthly `setup_decay` comparisons with a
  30-trade minimum per period, expectancy change, degradation fraction, and an
  operator-review flag. It never retires or changes a setup automatically.
- `APIManager.get_status()` now handles partially initialized managers safely:
  missing provider attributes report offline, and optional OpenBB status is
  included only when that provider was initialized. Health reporting therefore
  cannot crash or imply source availability during degraded startup.
- `CorrelationRisk` now validates sample/threshold/lookback/staleness policy
  inputs and treats unparseable bar timestamps as stale, forcing the
  conservative driver-bucket fallback. It cannot use malformed correlation
  data to influence portfolio permission.
- `research_stress.py` now rejects non-finite R-values, unsafe risk fractions,
  malformed path/seed inputs, invalid cost buffers, and sample thresholds below
  100. Invalid stress requests return an explicit non-authoritative error
  instead of producing misleading Monte Carlo output.
- Forward-test callers can no longer weaken the evidence floor by passing a
  smaller demo threshold; invalid thresholds, missing cost metadata, malformed
  backtest values, and undersized samples return `INSUFFICIENT_DATA` or
  `INVALID_INPUT` with `execution_authority=false`. Focused forward-test,
  promotion-package, rollout-gate, and Section-J tests pass (`13 passed`).
- Planned-entry construction now requires a non-empty normalized symbol, a
  positive integral quote timestamp, and an integral expiry; malformed identity
  or timing cannot become a pending order. Execution-planning, policy, preflight,
  and Section-A regression coverage passes (`33 passed`).
- `execution_policy.load()` now fails closed when broker preflight lacks an
  explicit `validation.valid=true` result; policy review timestamps must be
  timezone-aware and cannot be materially in the future. Policy/preflight
  regression coverage passes (`19 passed`). It also requires the exact policy
  symbol to be present in the captured and validated broker contract.
- Tiny-live approval now requires at least one existing hashed file artifact;
  reference-only strings cannot create an active approval. Evidence-drift and
  rollout regression coverage passes (`11 passed`).
- `readiness_report.build()` now converts authorization, journal, sample,
  legacy, rollout, security, and malformed-preflight exceptions into blocked
  evidence records rather than crashing or implying readiness. Readiness,
  audit-export, and journal-CLI regression coverage passes (`8 passed`).
- `broker_preflight.validate_envelope()` now independently returns an invalid,
  non-authoritative result for malformed report/account/symbol shapes or
  requested-symbol inputs instead of raising. Broker-preflight, Section-A, and
  readiness regression coverage passes (`16 passed`).
- Persisted rollout manifests and broker-policy state are now shape-checked;
  malformed records remain inactive/rejected rather than crashing verification
  or loading. Rollout, broker-preflight, and Section-A regression coverage
  passes (`17 passed`).
- Research split and promotion-floor helpers now reject malformed parameters,
  horizons, sample counts, stress scenarios, and stress metrics with empty
  folds or explicit floor reasons. Research validation, run-ledger, Section-I,
  and scorecard coverage passes (`16 passed`).
- Offline baseline, contextual-bandit, and supervised-training entry points now
  enforce the 100-row research floor and reject malformed timing/context
  settings before reading or writing artifacts. Model-input safety plus
  contextual-bandit, supervised, scorecard, and Section-I coverage passes
  (`13 passed`).
- Model-family planning and setup-governance now reject weak/malformed sample,
  fold, boolean, and numeric thresholds. Online HMM/GARCH/Kelly reject
  non-finite or malformed state/observations and always expose
  `execution_authority=false`; adaptive/model-family and Section-C/I coverage
  passes (`16 passed`).
- `AdaptiveResearchService` now rejects empty identities, non-iterable/string
  close inputs, and malformed persisted HMM/GARCH sections without terminating
  the loop; adaptive service and model-safety coverage passes (`5 passed`).
- A broader regression slice spanning forward-test, reconciliation, execution,
  broker preflight/policy, rollout/readiness, promotion, research, and adaptive
  model modules completed with all collected tests passing; no execution
  authority was enabled.
- The security scanner now recognizes explicit redaction markers and type
  annotations as non-secret code. The current audit has no production
  credential findings; non-production test fixtures remain separately listed,
  while historical `.env`/account/config paths still require operator rotation
  and history review (`rotation_completed=false`).
- Production daily MT5 reconciliation now invokes strict timestamped mode:
  deals are filtered to the requested UTC interval, malformed/missing owned
  timestamps and close-entry markers become durable attention discrepancies,
  and out-of-window deals are counted separately. Reconciliation and CLI
  regression coverage passes (`10 passed`).
- `llm_router.py` no longer substitutes an unavailable selected provider or
  silently traverses fallback providers; failover requires explicit configuration
  and is covered by router-policy tests.
- `journal_scope.py` now fails closed for DEMO/LIVE scopes without a non-empty
  account fingerprint, rejects mismatched account fingerprints, and reports
  incomplete existing DEMO/LIVE rows as `INVALID`; PAPER/RESEARCH behavior is
  unchanged and OS/database access-role provisioning remains operator work.
- The LLM runtime now propagates the selected PAPER/DEMO/LIVE mode into the
  authoritative scope before creating strategy state, derives the account
  fingerprint from authenticated login/server identity, and rejects combined
  mode flags at argument parsing. Runtime output reports the real mode.
- `agent_contracts.py` now covers every runtime scheduled agent action and
  artifact (health, macro review, reconciliation, and research), with tests
  preventing proposer agents from acquiring execution permissions. DEMO/LIVE
  identity derivation now rejects terminals that do not return authenticated
  login and server values.
- Order-intent creation now requires the contract’s explicit `order_authority`
  bit in addition to action and artifact permissions; a proposer cannot gain
  execution capability from a future metadata-only contract change.
- `TradingStore` validates the requested journal scope before creating
  operational tables, and regression coverage confirms an invalid DEMO/LIVE
  startup leaves no newly initialized execution database.
- `DataQualityGate` now rejects live snapshots with incomplete candle
  timestamps or any candle timestamp later than the capture time, preventing
  point-in-time ordering from being assumed. Focused data-quality tests pass.
- `setup_registry.classify()` now returns an auditable ineligible
  `unclassified_v1` result for malformed, non-finite, or negative ADX inputs
  instead of raising during proposal validation. Valid registered setup
  classification remains covered.
- `event_policy.evaluate()` now handles malformed calendar records and
  importance values deterministically: malformed relevant events create a
  blackout reason, while malformed records for unrelated currencies do not
  contaminate the symbol’s event policy.
- `cost_estimator.assess_costs()` now rejects non-finite/negative broker
  prices, tick values, and calibrated commission/slippage/swap assumptions
  before calculating net-RR. Focused tests cover NaN, Infinity, and negative
  inputs.
- `execution_planning.py` now rejects non-finite pending-entry limits,
  timestamps, quote values, spread limits, and tick-age limits before planned
  orders or pre-submit quote validation. Focused planning tests pass.
- `broker_preflight.validate_envelope()` now rejects non-finite/malformed
  account flags, leverage, tick/contract values, volume ranges, stop/freeze
  levels, and order modes. Broker-preflight regression coverage passes.
- `execution_policy.validate()` now rejects non-finite calibration values,
  empty broker fingerprints, and fractional/non-finite expiry or tick-age
  limits before approval. Focused policy-validation tests pass.
- `RiskManager` now validates finite risk configuration/equity/position inputs,
  caps direct per-trade risk at 1%, and refuses to round a position up to the
  broker minimum when that would exceed dollar risk. Risk-safety regressions
  pass.

## Verification commands

```text
python -m py_compile macro_extraction.py model_family_gate.py execution_policy.py forward_test_report.py journal_audit.py journal_migration.py risk_manager.py setup_labeling.py trading_memory.py trading_safety.py agent_operations.py mt5_trader.py
python mt5_trader.py journal integrity
python mt5_trader.py journal policy-status
python mt5_trader.py journal model-family-status
python mt5_trader.py journal dataset-status
python mt5_trader.py journal research-status
python mt5_trader.py journal restart-status
python mt5_trader.py journal security-status
python mt5_trader.py journal supervised-status
python mt5_trader.py journal live-approval --approval-action status
python mt5_trader.py journal broker-preflight-status
python mt5_trader.py journal sample-gate-status
python mt5_trader.py journal auth-status
python mt5_trader.py journal policy-candidate --policy-symbol XAUUSD
python mt5_trader.py journal promotion-package
python mt5_trader.py memory-review scenario list
python mt5_trader.py journal setup-labels
python mt5_trader.py journal legacy-status --journal-path trading_state.db
python mt5_trader.py journal reconciliation --journal-path trading_state.db
python mt5_trader.py journal incident-drills
```

Targeted tests passed across safety, operations, migration,
security, supervised-gate, candle-claim ordering, paper-quote safety,
live-rollout gate, broker-preflight, sample-gate, auth-diagnostics, position-
attention, risk-state persistence, manager-state persistence, router-policy,
and RL integration tests. The entire repository test directory still contains
legacy calendar/paper/ownership tests that are not a valid live-readiness gate;
the ownership failure is intentional because unmanaged/manual positions must
be refused.
Additional trade-management regression tests pass, including
broker stop/freeze-distance rejection for protective SL changes.

Grouped repository verification (latest run, 2026-08-10): `453 passed, 10 skipped`
across all 463 collected tests; skips are interactive diagnostic/TUI modules
that require a logged-in MT5 terminal or desktop display.

Journal reporting commands now consistently honor `--journal-path`; a CLI
regression test verifies reconciliation reads the selected authoritative
database rather than silently falling back to the default path. Durable
kill-switch, provider-circuit, and memory review commands now accept the same
path override, as do model status/drift/register/review and the core journal
integrity/metrics/report commands.

Research promotion metrics now consume only the leakage-safe labelled dataset;
unlabelled/legacy closed rows cannot appear setup-eligible, and a purged
walk-forward fold is required before eligibility is reported.

Model-registry approval additionally requires explicit walk-forward,
purged-validation, point-in-time, sample-gate, cost-validation, and artifact
reference evidence; registry approval never grants execution authority.

Encrypted journal backup verification now passes. `journal_backup.py` creates
AES-256-GCM backups using PBKDF2-HMAC-SHA256, stores a plaintext hash and
SQLite integrity metadata, rejects wrong passphrases, and restores only after
authenticated verification. The CLI exposes `encrypted-backup-create`,
`encrypted-backup-status`, and `encrypted-backup-restore`; the passphrase is
read only from an operator-selected environment variable. Windows SQLite
verification uses a uniquely-created temporary file that is deleted before
return, so no plaintext backup copy is retained.

Backup retention controls now pass targeted tests. `backup-prune` only considers
recognized backup suffixes, preserves the newest configured minimum, reports a
dry-run plan by default, and deletes only after explicit `--apply`; companion
manifests are removed together with their backup.

`journal audit-export` was smoke-tested against the authoritative journal. It
writes a complete audit/baseline/qualified-metrics JSON artifact and explicitly
reports that the artifact has no execution authority.

LLM request telemetry now persists prompt/response hashes, retry count, parse
status, response size, latency, and provider/model identity. Estimates remain
separate from realized token/cost fields so budget accounting cannot silently
turn an estimate into actual usage.

LLM failure-accounting hardening: a valid model response with `action=HOLD` is
recorded as a successful call, while `NO_RESPONSE`, `PARSE_ERROR`, `ERROR`, or
an unknown trace outcome is recorded as an unsuccessful request and provider
circuit event. The selected model still fails closed to HOLD; failure telemetry
now contributes to persistent circuit escalation instead of falsely resetting
health. Pure trace-outcome and unexpected-provider-error regressions pass.

Scanner-lane parity: pair-selection success/failure outcomes now also update
the same persistent provider circuit and emit durable circuit-health events.
Scanner parse/provider failures therefore cannot bypass health escalation while
the scanner still fails closed to an empty symbol universe. Scanner regression
coverage passes.

Scanner usage accounting: provider-reported `total_tokens` and `cost` metadata
are now persisted on successful and failed pair-selection requests (with
validated zero defaults when a provider exposes no usage). This keeps scanner
cost reporting consistent with the main decision lane without retaining prompt
or response content.

Usage-integrity hardening: fractional, negative, non-finite, boolean, or
non-object provider usage metadata is treated as an invalid request outcome,
recorded with zero safe numeric usage and a failure status. Completion cannot
raise while leaving a request unresolved, and invalid usage cannot reset the
provider circuit. Regression coverage passes.

LLM reservation recovery hardening: abandoned `RESERVED` provider/model leases
are durably marked `EXPIRED` after a bounded timeout, freeing concurrency after
a process crash while preserving the request in hourly/daily attempt counts.
Recovery is fail-closed for malformed lease-age inputs and is covered by
restart/concurrency regression tests.

Provider-circuit state hardening: malformed persisted tiers, failure lists, or
naive/invalid timestamps now force `MANUAL_RESET_REQUIRED` and block provider
calls until an explicit reset. Circuit state is no longer allowed to throw on
corrupt journal data or accidentally reopen the LLM path; targeted malformed-
state tests pass.

Schedule-cadence hardening: durable schedule claims now retain their original
cadence across restarts, report missed windows, reject malformed claim limits,
and recover malformed timestamps to a known future point without crashing the
pulse. Agent-operation and scheduler-pulse regressions pass.

Security cleanup: a plaintext `.env` was found recreated in the synced workspace
but could not be moved because the file was locked. Its contents were replaced
in place with a non-secret placeholder. The current security scan reports zero
production credential findings; historical rotation/Git-history review is still
an operator-controlled gate.

Validator telemetry now persists a machine-readable per-rule trace containing
pass/fail status, observed values, limits, and failure reasons for proposal,
setup-instance, and lifecycle records. Early schema/setup/data-quality/policy
rejections carry an explicit trace as well; portfolio-arbiter and authenticated
broker replay evidence are still required for full release readiness.

Position-management telemetry now records owned SL modifications and partial or
full close success/error outcomes in both lifecycle and broker-execution tables;
partial-close requests carry the position magic number and unmanaged positions
remain observation/refusal-only.

Unresolved execution reviews now escalate from warning to critical using durable
age and detection-count thresholds during reconciliation. Escalation never
resolves an intent or grants order permission.

SQLite append-only triggers now reject edits and deletes for lifecycle,
broker-execution, position-observation, and market-tick evidence tables.

L0 trace redaction now scans free-form strings as well as sensitive keys, so
inline passwords, API keys, bearer tokens, and account/login identifiers are
replaced before raw traces are stored.

Account registration now rejects plaintext `--password` values and stores only
an uppercase environment-variable reference. Regression coverage confirms a
new account record contains no password field.

The broker replay harness now records direction-correct MFE/MAE in price,
currency, and R units from the same completed-bar sequence used for exits;
buy/sell regression cases pass while replay remains non-authoritative research
evidence.

Daily reconciliation now maps MT5 position tickets to journal order/deal
tickets through the execution ledger and reports unresolved or ambiguous
relationships instead of silently treating identifiers as interchangeable.
Both mapped-clean and unresolved-mapping regression cases pass.
Malformed/non-finite owned broker P&L and cost fields are also rejected rather
than coerced to zero.

The leakage-safe research dataset now rejects non-finite or malformed
point-in-time feature scalars and trade outcomes before setup metrics are
computed; a NaN regression case is covered and the sample gate remains
fail-closed.

Dataset extraction also rejects reversed/malformed trade horizons and any
feature timestamp later than entry; future-timestamp regression coverage
passes.

Macro reaction measurement now validates every requested observation window and
returns a fail-closed reason for malformed, non-finite, zero, fractional, or
unnamed windows; regression coverage passes.

Memory scheduling now emits deterministic weekly summaries, creates only
reviewable weekly core-memory candidates when closed data exists, and reports
approved/contradicted memories for monthly review without auto-retiring them.
Empty weeks produce no candidate; schedule regression coverage passes.
The `journal weekly-memory` and `journal monthly-memory` commands were also
smoke-tested against an isolated SQLite journal and preserve execution
authority as false. Daily and weekly period inputs are validated as explicit
UTC dates, with weekly summaries anchored to Mondays.

Macro reaction measurement now validates every requested observation window and
returns a fail-closed reason for malformed, non-finite, zero, fractional, or
unnamed windows; regression coverage passes.

## Current evidence and blockers (2026-08-09 continuation)

- Journal integrity: passed; active legacy count is zero and 20 original rows
  remain preserved only through retirement markers with broker no-match evidence.
- Journal scope: fresh `DEMO` scope bound to the authenticated account/server
  fingerprint; the prior `LEGACY_UNSCOPED` database is archived and backed up.
- MT5 authorization: the latest read-only probe is `TERMINAL_UNAVAILABLE`.
  Earlier authorization evidence is retained for audit only; no current
  authorization or execution permission is inferred from it.
- Broker preflight: stored contract evidence remains `READY` for `XAUUSD`,
  `EURUSD`, and `GBPJPY`, but the latest read-only MT5 probe is
  `TERMINAL_UNAVAILABLE`; no current authorization is inferred from the stored
  evidence.
- Dataset/model/bandit/research promotion: insufficient labelled, point-in-time
  closed outcomes; execution authority remains false.
- Security preflight: `REVIEW_REQUIRED`; no production credential findings are
  present in the workspace. A locked synced `.env` was sanitized in place and
  legacy risky artifacts remain historical/review markers; Git-history
  rotation/review and secure-store migration remain operator actions. Values
  were not printed.
- Readiness remains blocked by terminal unavailability, insufficient labelled
  sample, missing tiny-live approval/evidence, and the outstanding security
  rotation/history gate.

## Remaining operator gates

1. Rotate all previously exposed broker/provider values, review Git history, and
   migrate runtime secrets to an approved secret store; rerun the redacted audit.
2. Review and approve broker-specific execution-policy calibrations for each
   preflighted symbol.
3. Collect at least 100 current-schema, point-in-time labelled closed trades per
   setup across multiple regimes before supervised/bandit/RL promotion.
4. Complete demo forward-test comparison and obtain explicit operator approval
   plus kill-switch/reconnect/manual-recovery evidence before any tiny live run.

Latest continuation smoke evidence: `journal weekly-memory --week-start
2026-08-03` returned `NO_DATA` with no candidate and
`execution_authority=false`; `journal monthly-memory --older-than-days 30`
archived zero records and reported only operator-review counts, also with
`execution_authority=false`.

Latest status checks: `journal security-status` reports no production
credential findings, no tracked risky files, and `REVIEW_REQUIRED` for
uncompleted rotation/history review; `journal readiness-status` reports
`TERMINAL_UNAVAILABLE` and `INSUFFICIENT_DATA` (0 eligible setup samples,
minimum 100 samples and 2 regimes). Both checks preserve
`execution_authority=false`.

Latest promotion evidence: `journal promotion-package --promotion-output
promotion_package_current.json` produced a content-hashed `NOT_READY` artifact.
Integrity and journal-scope checks pass, while sample, purged walk-forward,
cost-stress, forward-demo, tiny-live approval, and security-rotation gates
remain explicitly listed as blockers. The artifact grants no order authority.

Latest local fault evidence: `python incident_drills.py` returned `PASS` for
kill-switch persistence, duplicate-intent blocking, provider outage
degradation, malformed market/calendar data, weekend closure, and restart
recovery. The drill is broker-free and does not replace authenticated MT5
network/fill fault evidence.

Short-term memory hardening: `AgentOperations` now persists redacted,
revisioned working-memory entries with source IDs, explicit `as_of` filtering,
expiry handling, bounded prompt size, and deterministic compaction that keeps
risk/position/error/kill-switch state while summarizing ordinary observations.
The summary is operational context only; source journal evidence is never
deleted. Dedicated regression coverage passes (`17 passed` in
`tests/test_agent_operations.py` before namespace integration; the latest
combined operations/scheduler run passes (`20 passed`). The journal CLI now
exposes `working-memory-status`, `working-memory-context`, and
`working-memory-compact`, all explicitly reporting `execution_authority=false`.

Dual-velocity hardening: `dual_velocity.py` provides bounded fast/slow worker
lanes with keyed deduplication, explicit backpressure, non-propagating worker
errors, latency telemetry, and shutdown handling. The orchestrator dispatches
verified local-memory distillation through the slow lane and polls results on
the market pulse; no worker has order authority. Dedicated executor regression
coverage passes as part of the latest full suite.

Operational scope hardening: the orchestrator now constructs
`AgentOperations` against the same scoped `TradingStore` path, preventing
heartbeats, leases, LLM telemetry, and working memory from diverging into a
second default database after restart.

Qualified journal metrics now expose sample-gated adaptive measures required
by the design: month-over-month regime growth quotient, MAE-based drawdown
compression, and explicit macro-bias/trade-side regime alignment efficiency.
Unknown macro bias is excluded rather than inferred, and insufficient samples
return `INSUFFICIENT_DATA` instead of a fabricated score.

Entry-path hardening: the generic live `place_market_order` helper now fails
closed. New positions must use the deterministic pending limit/stop plan;
market execution is available only through the separately validated,
ticket/magic/reason-bound emergency-exit path. Paper fixtures remain available
for simulation and the planned-entry regression suite passes. The direct
`market-order` CLI is also rejected before any broker initialization.

Calendar-source hardening: market analysis no longer turns FRED observations
into synthetic release events, and an empty configured calendar no longer
prevents the authenticated MT5 calendar fallback from being queried. FRED is
retained as macro context only; the snapshot records the calendar source and
event count. Calendar/source regression coverage passes (`40 passed, 1
skipped` across the focused calendar/data suite).

Market-data integrity hardening: `fetch_ohlc()` now fails closed whether MT5 is
unavailable or returns no bars; it does not fall back to Yahoo or generated
candles. `MarketDataProvider.fetch_data()` likewise rejects paper-mode random
OHLC, DOM, and mock-news generation. Explicit broker-replay/test fixtures are
the only permitted non-broker data source. The focused market-data/paper-order
regression set passes (`8 passed`). The standalone `data --paper` path was
hardened to the same fail-closed rule and now requires an explicit
replay/fixture source instead of generating random candles.
Mock LLM decisions are also rejected before any broker-cycle side effect, and
the source-health report no longer labels absent MT5 data as simulated.
The offline AUTO scanner now returns an empty opportunity set instead of
selecting Yahoo or random candidates.
Authenticated wildcard selection is deterministic across repeated scans, with
regression coverage for stable candidate ordering.
Paper quote mechanics no longer force missing event/data evidence to “safe” in
the deterministic validator; a focused safety regression covers this rule.

Legacy news guard hardening: `APIManager.is_news_blackout()` now requires a
fresh structured calendar cache, validates timestamps/currency/importance, and
fails closed on missing or malformed records. Headline-only NewsAPI cache data
cannot certify market safety, and the continuous execution cycle leaves
permission to the source-bound calendar/EventPolicy path. Regression coverage
passes in `tests/test_api_manager_news_guard.py`.
The same source-health regression verifies that the uninstantiated SEC
integration is reported offline rather than falsely online.

Pair-selection hardening: malformed or failed LLM pair-selection responses no
longer default to major pairs. Successful responses are normalized,
deduplicated, and allow-listed against the registered universe; invalid output
becomes an empty opportunity set. Regression coverage is in
`tests/test_pair_selection_context.py`.
The same context gate now blocks scans when the calendar is healthy but news or
fundamental data sources are unavailable.
Pair-selection calls also reserve and finish a scoped LLM request record with
hashes, latency, parse status, and provider/model budget metadata.

Dashboard metric hardening: runtime cycle output now computes rolling
Sharpe/Sortino from closed-trade history, exposes them in `system_metrics`, and
formats max drawdown as currency. Performance regression coverage includes
downside-only Sortino behavior.

Pending-entry boundary hardening: the low-level pending order function now
revalidates ownership, finite protective geometry, timezone-aware future expiry,
and MT5 availability instead of trusting only the upstream proposal validator.
Paper and invalid-plan regression coverage is included in
`tests/test_paper_quote_safety.py`.

Pending-cancellation hardening: expiry reconciliation now passes the persisted
intent magic to the cancellation boundary, which refuses to remove a broker
or paper order owned by another strategy/manual actor. Ownership regression is
covered alongside the pending-entry tests.

Pending quote-geometry hardening: `validate_pending_entry_geometry()` and the
live submission path now reject crossed or immediately marketable limit/stop
plans after a quote move. The focused execution suite passes (`35 passed`).

Broker-volume and ownership hardening: position-manager partial-close and
pyramiding paths now require a broker symbol contract, normalize requested
volume to the broker's minimum/maximum/step, reject missing or malformed
specifications, require a fresh quote, preserve the owned magic number, and
refuse unowned/manual positions. Dedicated tests cover non-default lot steps
and ownership refusal.

Broker filling-mode hardening: owned position-management and emergency-exit
requests now translate the broker's filling-mode flags into MT5 request values,
prefer IOC when advertised, fall back to FOK, and fail closed when no supported
market mode is available. No hard-coded management IOC request remains.

Microstructure hardening: DOM observations now reject missing capture times and
non-finite or negative spread/depth/imbalance values. Existing spread-spike,
quote-gap, liquidity-withdrawal, and imbalance-rate tests remain green.

Calendar permission hardening: live proposal validation now requires the
current snapshot to identify a successful `configured_calendar` or
`mt5_calendar` source. A calendar wrapper merely existing in memory cannot
turn a failed or missing current feed into permission to trade.

Market-source integrity hardening: live snapshots now carry a non-secret MT5
source identity containing provider, server, and an account/server hash;
`DataQualityGate` fails closed when that identity is missing, malformed, or
contains sensitive fields. The gate also retains bounded in-run candle
fingerprints and rejects a changed OHLC/revision payload for an already-seen
candle timestamp. Focused data-quality tests pass.

Session-liquidity feature hardening: completed-candle feature snapshots now
persist deterministic London-open, New-York-open, and London/New-York-overlap
boolean flags derived from the candle timestamp. They are replay-stable and do
not use the process wall clock; setup permission remains deterministic and
separate. Feature-builder/schema regression coverage passes.

Volatility/lineage feature hardening: completed-candle snapshots now persist a
point-in-time SMA(20), bounded ATR percentile over completed ATR observations,
and explicit source/timeframe/capture/completed-bar lineage. The schema validator
checks lineage consistency when present, while values remain hash-bound and
advisory for risk/research. Feature and schema tests pass.

Provider-time hardening: the live proposal path now records the MT5 tick time
as `provider_time` before data-quality validation. The gate compares provider
time with the local snapshot capture and rejects missing, malformed, or
out-of-threshold observations. This closes the deterministic portion of the
provider-time requirement; authenticated broker replay remains external.

Macro-event feature hardening: feature snapshots now retain the nearest future
high-impact calendar event in minutes and a within-60-minute flag, using only
events timestamped after point-in-time capture. Past or low-impact rows are
excluded, preventing event hindsight from entering setup features. Focused
feature/schema coverage passes.

Cross-market/correlation feature hardening: snapshots now accept only
timestamped cross-market observations at or before capture, summarize their
count/mean/age, count future observations rejected, and retain a hashed
driver-bucket identity in lineage. Bare or untimestamped values are treated as
unavailable; the original observations remain authoritative-journal data and
cannot grant order permission. The live cycle records the current completed
return first, then retrieves only other-symbol, non-stale ledger observations at
that same cutoff. Mixed ISO/epoch timestamp parsing now selects the newest
valid observation deterministically. Regression coverage verifies future-data,
stale-data, mixed-timestamp, and missing-data behavior.

Adaptive-research safety hardening: completed-close HMM/GARCH state is now
included in the bounded LLM context and proposal evidence as explicitly
advisory metadata. Malformed, non-finite, or non-positive closes are skipped
before updating the persistent estimator, while fewer than two valid closes
produce an unavailable state. These outputs remain unable to bypass the
deterministic validator, portfolio arbiter, or kill switch.

Lifecycle-state hardening: setup instances now persist the full managed path
from `FILLED` through `PARTIAL`/`MANAGED` to `CLOSED`. Broker-position writes
attach the ticket to the setup, partial/SL management updates the setup state,
and close reconciliation transitions the linked setup before recording the
terminal lifecycle event. Terminal states reject later management transitions.

Rollout-authority metadata hardening: tiny-live approval status and approval
responses now report `execution_authority=false`; approval remains an evidence
prerequisite only. The runtime still requires the independent validator,
portfolio limits, kill switch, broker policy, and reconciliation gates.

Research-run ledger hardening: every invocation of `journal baseline-status`,
`journal supervised-status`, and `journal bandit-status` now appends the exact
report to the authoritative SQLite journal. The `research_validation_runs`
ledger stores the run kind, status, dataset/report hashes, configuration, and
full report, while `journal validation-runs` provides an auditable readback
with `execution_authority=false`. A research report therefore cannot be
silently replaced by a later mutable code version: SQLite triggers reject
updates and deletes. The ledger remains strictly evidence-only.

Broker-timestamp hardening: `TradingStore.record_close()` now accepts and
normalizes the broker's close timestamp, uses it for the terminal position
observation, and MT5 history reconciliation forwards `time_msc`/`time` when
available. Weekly/monthly memory windows and label horizons therefore remain
tied to the execution event rather than the reconciliation wall clock.

Promotion-evidence hardening: `promotion_package.build()` now embeds up to 50
immutable `research_validation_runs` records (including run kind, hashes,
configuration, and `execution_authority=false`) in the signed review package.
Reviewers can therefore verify which baseline/supervised/bandit reports were
available without treating the package as order permission.

Audit-export hardening: `journal audit-export` now includes the same immutable
research-run records alongside journal and operational evidence, retaining the
evidence-only `execution_authority=false` marker.

MT5-diagnostics hardening: authorization probes now pass a bounded 5-second
native `initialize` timeout per terminal path (clamped to 1–30 seconds), retain
the timeout in each attempt, and classify timeout failures as
`TERMINAL_UNAVAILABLE`. The read-only `journal auth-status` path therefore
fails closed without an unbounded connection wait.

Connection-path hardening: `MT5Client.login()` now applies the same bounded
1–30 second initialization policy (default 5 seconds), including the real
credentialed initialization path. The timeout is configurable through
`MT5_INIT_TIMEOUT_MS` and remains independent of execution authority.

Order-intent hardening: `AgentOperations.resolve_order_intent()` now enforces
an explicit transition table, rejects unknown intents/statuses and backward
terminal mutations, treats repeated states idempotently, and records each
accepted transition as redacted operational telemetry. `UNRESOLVED` remains
recoverable only through documented broker-confirmed outcomes.

Journal-schema hardening: `TradingStore` now initializes the
`research_validation_runs` table and its append-only triggers on every fresh
or migrated journal, before any research command runs. Research evidence is
therefore part of the authoritative schema rather than an optional late-
created table.

Restart-recovery hardening: `AgentOperations.summary()` now reclaims agent
runs whose durable heartbeat is older than the recovery threshold before
reporting status, exposes `stale_runs_recovered`, and includes the explicit
`execution_authority=false` marker. An interrupted process therefore cannot
remain falsely reported as active until a new worker happens to start.

Final safety verification for this continuation: `python -m py_compile`
completed for the modified research-ledger, trading-safety, journal-audit,
memory, and MT5 reconciliation modules; the focused research-ledger suite
passed (`4 passed`),
the memory/lifecycle regression suite passed (`14 passed`), the bounded
authorization/readiness suite passed (`7 passed`), the operations/scheduler
suite passed (`25 passed`), the bounded MT5 client/data suite passed (`10
passed`), and the order-intent lifecycle suite passed (`34 passed`).
The monolithic run exceeded the command runner’s timeout without reporting a
failure; file-group runs covered the complete collected set and passed.
The current
read-only readiness report is `BLOCKED` with `execution_authority=false`:
there are no reconciled closed trades for the 100-trade research gate, tiny-
live approval is absent, and credential/history rotation plus secure-store
review remains an operator-controlled requirement. The latest probe also reports
`TERMINAL_UNAVAILABLE`; no execution permission is inferred from prior
authorization evidence.

Durable alert-outbox hardening: `alert_outbox.py` now provides an idempotent,
SQLite-backed operator-alert handoff with bounded leases, retry/backoff,
expired-claim recovery, terminal `BLOCKED` state, and delivery acknowledgements.
It is transport-neutral (`transport=NONE`), stores only redacted alert fields,
opens no network connection, and cannot change trading permission. The journal
CLI exposes read-only `alerts`, `alerts-enqueue`, `alerts-status`,
`alerts-claim`, `alerts-ack`, and `alerts-recover` operations. Focused outbox,
health-alert, and memory-governance tests pass; an external notifier and
operator delivery evidence remain intentionally external. The continuous
market-health schedule now enqueues evaluated alerts idempotently after each
health pulse, while the outbox remains advisory and cannot alter the
validator, arbiter, kill switch, or execution path.

Research-influence hardening: `research_influence_gate.py` is now the single
fail-closed permission check for adaptive HMM/GARCH, Kelly/growth, and RL
outputs on broker-backed paths. Paper mode may expose advisory research; demo
and live paths require a current 100+ labelled sample, qualifying purged/OOS
promotion evidence, a SHA-256 evidence digest, and an expiring operator
approval. The environment switch alone cannot bypass this gate. The journal
CLI exposes `research-influence-status`, `research-influence-approve`, and
`research-influence-revoke`; focused gate/adaptive/RL/growth suites pass.
The final gate/outbox/RL regression slice passed (`26 passed`); compilation of
the changed runtime modules passed. A broader mixed regression command was
terminated by the command-runner timeout after partial progress, so it is not
treated as evidence of a full-suite pass.

Pre-submit timing hardening: `execution_planning.validate_quote()` now rejects
missing, non-integral, or non-positive broker tick timestamps before calculating
age, so malformed provider timing cannot be treated as a fresh quote. Planned
entry/policy/validator regression tests passed (`30 passed`).

Readiness integration: `journal readiness-status` now includes the centralized
research-influence gate and an explicit blocker when adaptive/learned output is
not approved. Readiness therefore cannot appear complete while the model
promotion evidence is missing; readiness tests pass (`12 passed` in the focused
slice).
### Read-only qualified dashboard aggregation

- Added `dashboard_report.py` and `journal dashboard-status` as a single
  operator view over the authoritative SQLite journal. It combines qualified
  research/trade/execution/portfolio/period metrics, memory-quality evidence,
  health alerts, and the independent readiness gates.
- Every dashboard response carries sample size, date range, cost model,
  point-in-time status, out-of-sample status, promotion status, and
  `execution_authority: false`. A database that was missing before the report
  was requested remains visibly `DATABASE_MISSING` even if readiness created a
  diagnostic schema while collecting evidence.
- Added `tests/test_dashboard_report.py`; the dashboard slice passes 2 tests.
- The combined dashboard/research-influence/alert/execution-planning/readiness
  regression slice passes 33 tests; `py_compile` also passes for the new
  dashboard module and CLI integration.

### External alert delivery (fail-closed notifier)

- Added `operations/alert_delivery.py`: the durable, transport-neutral
  `alert_outbox` now has an explicit delivery half. `deliver_pending()`
  validates transport configuration before claiming anything (fail closed),
  reclaims leases from a crashed delivery worker via `recover_expired`,
  delivers each claimed alert through a pluggable sender, and acknowledges
  through the existing retry/backoff/BLOCK protocol with per-alert isolation.
- Built-in transports: bounded redacted JSONL append (`file`, explicit
  directory required) and HTTP(S) POST webhook with a 10-second timeout
  (explicit URL or `TRADING_ALERT_WEBHOOK_URL`; non-2xx maps to a
  retryable failure; response bodies are never persisted).
- New journal CLI action `alerts-deliver` with `--alert-transport` and
  `--alert-file`; every response keeps `execution_authority: false` and
  delivery never touches trading permission or order state.
- Added `tests/test_alert_delivery.py` (10 tests): idempotent once-only file
  delivery, retry-then-BLOCK without silent loss, crash-between-claim-and-
  acknowledge lease recovery delivering exactly once, mixed-batch failure
  isolation, configuration fail-closed checks before any claim, a live local
  HTTP webhook success test, webhook error mapping, message bounds/shape,
  and env/CLI URL resolution. Focused slice: 14 passed including the
  pre-existing outbox tests. CLI smoke test verified enqueue -> deliver ->
  SENT plus fail-closed webhook-without-URL behavior end to end.

### Synthetic-data quarantine + research contamination filter

- Production journal audit confirmed all 360 closed trades/labels were
  `demo_sample_generator` fixtures (`synthetic_close`, bare WIN/LOSS vocab).
  Backed up to `backups/trading_state_pre_synthetic_quarantine_*.db`, then
  removed; the active DEMO journal is now honestly empty (0 trades).
- `research_validation.labelled_dataset()` now excludes synthetic fixture rows
  via defense-in-depth detection (payload flag, generator source markers at
  payload or feature level, reserved `synthetic_close` exit reason) with an
  auditable `synthetic_row_excluded` counter; random data can no longer reach
  sample gates, setup metrics, or promotion evidence.
- `demo_sample_generator.inject_samples()` now REFUSES journals containing any
  non-synthetic broker-reconciled trade unless `allow_mixed_journal=True`;
  its fixture schema also now matches production `trade_labels` columns
  (label_rule/review_status), fixing a latent labelled_dataset crash on
  generator-created DBs; duplicate dead `_proposal_features` removed.
- Version payloads are now content-bound: `strategy_spec` carries a SHA-256 of
  the serialized setup registry and `prompt_template` a SHA-256 of the hoisted
  `SIGNAL_SYSTEM_PROMPT_TEMPLATE` constant, so rule/prompt edits rotate
  journaled version ids instead of silently reusing "setup_registry_v1"/
  "trade_decision_v2" labels. Regression tests in
  `tests/test_lifecycle_versioning.py`.
- Updated stale harness integration test for the concurrently hardened
  fail-closed agent loop (stub live-data plugin supplies price/ATR; new
  regression test asserts CYCLE_REJECTED `insufficient_live_market_data`
  without live data). Focused slices pass; full suite green.

### Strategy lifecycle engine (Phase 2)

- `operations/strategy_lifecycle.py`: durable per-setup state machine
  (CANDIDATE -> VALIDATING -> PAPER_PROVEN -> DEMO_PROVEN -> DEPLOYED ->
  DECAYING -> FROZEN -> RETIRED) with append-only SQLite history protected by
  UPDATE/DELETE triggers. Automated actors may only demote
  (DEPLOYED->DECAYING->FROZEN) via explicit `allow_auto=True`; every
  promotion/unfreeze/retirement is an operator-actor CLI transition.
  Registered code setups are seeded DEPLOYED bound to the content-hash setup
  registry fingerprint; registry edits surface as REGISTRY_DRIFT until an
  operator acknowledges.
- `operations/strategy_evaluation.py`: scheduled evaluation consuming the
  strict labelled dataset plus `setup_governance.retirement_gate`. Setups
  below the mandatory sample floor are never judged; decayed/negative-
  expectancy setups are demoted automatically with WARNING
  (STRATEGY_DECAY_DETECTED) and CRITICAL (STRATEGY_FROZEN_OPERATOR_REVIEW)
  alerts enqueued to the durable outbox.
- Deterministic validator now carries an optional fail-closed lifecycle gate:
  FROZEN/RETIRED setups refuse new proposals (rule-traced as
  `lifecycle_clear`); a gate error also blocks rather than silently trading a
  possibly-frozen strategy. Unknown setup ids remain unblocked for backward
  compatibility.
- New hourly `lifecycle_evaluation` schedule (QuantitativeCoach) executes the
  evaluation inside the continuous pulse; journal CLI gains `strategy-status`
  (full board incl. drift), `strategy-transition`, `strategy-evaluate`, and
  `strategy-acknowledge` (all operator-gated).
- `model_registry.activation_status()` closes the self-attestation gap: an
  APPROVED row is only ACTIVATION_ELIGIBLE when its evidence flags hold, the
  artifact exists on disk, and at least one append-only validation run with a
  matching dataset hash recorded passing out-of-sample status; exposed via
  `journal model-activation-status --model-id`.
- Math repairs feeding live decisions: ADX now implements Wilder's directional
  movement selection rule with consistent adjust=False smoothing (previous
  version biased the confluence gate); `calculate_information_coefficient`
  drops forecast/return pairs jointly instead of filtering sides
  independently (length mismatch is now an explicit error).
- Tests: `tests/test_strategy_lifecycle_engine.py` (13 tests) covers seeding,
  transition legality, append-only history, drift acknowledgement,
  evidence-driven demotion/freeze/alerts, validator blocking (incl. fail-
  closed gate errors), activation corroboration, ADX bounds/trend behavior,
  and IC pairing. Focused slice passes; full suite green.

### Repo-wide docs-vs-code reconciliation sweep

- Import matrix over every module in core/intelligence/operations/research/
  data/ui plus root scripts: 205/205 import cleanly. Two landmines removed:
  (1) `data/marketdata_fix.py` was an unparsable stale patch fragment that
  still contained the fabricated paper-mode random candles the fail-closed
  doctrine had removed; archived to archive/legacy_root/. (2) both
  `launch_trading.py` copies spawned a trader subprocess at import time with
  a wrong cwd; now guarded behind main() and rooted at the repo root.
- CLI surface reconciliation: all 29 top-level commands now dispatch. The 11
  parse-but-silent commands are resolved: pending-order / modify-order /
  cancel-order refuse BEFORE any broker contact with the order-intent
  doctrine message; benchmark-llm refuses toward evidence-recorded
  evaluation surfaces; account-info / positions / orders / history /
  diagnose / test-live / benchmark are real read-only implementations that
  prefer authenticated login but fall back to an already-signed-in terminal
  session and never place orders. Module docstring no longer advertises
  nonexistent order workflows; history_overview datetime bug fixed.
- Live execution battery on this machine: journal sweep shows 50 actions
  producing real output on a scratch DB (5 more correctly require explicit
  arguments); account-info, positions, orders, history, test-live, diagnose
  verified live against the running MetaQuotes-Demo terminal;
  pending-order refusal verified before login.
- Full pytest suite green after all changes.
