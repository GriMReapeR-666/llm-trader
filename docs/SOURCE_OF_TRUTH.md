# SOURCE OF TRUTH — Autonomous LLM Trader System

> **Last verified:** 2026-08-28 | **Test suite:** 212 files, 155+ passing | **Codebase:** ~83K LOC Python

This document is the **single authoritative reference** for the entire project. Every component, its purpose, its interfaces, its configuration, and its dependencies are documented here. If there is a conflict between this document and the code, the code wins — but this document tells you where to look.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Entry Points](#2-entry-points)
3. [Core Engine (`core/`)](#3-core-engine-core)
4. [Intelligence Layer (`intelligence/`)](#4-intelligence-layer-intelligence)
5. [Data Layer (`data/`)](#5-data-layer-data)
6. [Operations Layer (`operations/`)](#6-operations-layer-operations)
7. [Research Layer (`research/`)](#7-research-layer-research)
8. [UI Layer (`ui/`)](#8-ui-layer-ui)
9. [Alpha Factor System](#9-alpha-factor-system)
10. [Configuration & Environment](#10-configuration--environment)
11. [State Files & Persistence](#11-state-files--persistence)
12. [Test Suite](#12-test-suite)
13. [Module Dependency Map](#13-module-dependency-map)
14. [Key Design Principles](#14-key-design-principles)

---

## 1. System Overview

An **institutional-grade, multi-agent autonomous trading system** integrating:
- Large Language Models (LLMs) for trade decisions
- Reinforcement Learning (Kronos, PPO, Meta-RL)
- Autonomous Causal Inference for macro analysis
- Quant Platform-style alpha factor research
- Deterministic fail-closed risk management
- Running on **MetaTrader 5** (Forex: EURUSD primarily)

### Scale

| Metric | Value |
|--------|-------|
| Total Python files | 735 |
| Core source files (excl. tests) | ~520 |
| Test files | 212 |
| Lines of code (source) | ~83,000 |
| Lines of code (tests) | ~23,000 |
| Total LOC | ~106,000 |

---

## 2. Entry Points

### 2.1 `mt5_trader.py` — Main CLI (2,872 lines)

**The single unified facade** for all trading operations. This is the file users interact with directly.

```
python mt5_trader.py llm-trade --symbol EURUSD --paper --loops 1
python mt5_trader.py llm-trade --symbol EURUSD --demo --loops 5
python mt5_trader.py llm-trade --symbol EURUSD --live --loops 3 --yes
python mt5_trader.py account-info
python mt5_trader.py data --symbol EURUSD --timeframe M15 --count 500
python mt5_trader.py benchmark --symbol EURUSD --timeframe H1 --count 2000
python mt5_trader.py positions
python mt5_trader.py orders
python mt5_trader.py history --start 2026-01-01 --end 2026-02-01
```

**Key classes:**
- `AccountManager` — Multi-account management with password_env security
- `Config` — Configuration resolution (env vars → AccountManager → config.ini)
- `MT5Client` — MT5 terminal connection with reconnection and bounded timeouts

**CLI subcommands:**
- `llm-trade` — The main autonomous trading loop (paper/demo/live)
- `data` — Fetch OHLC + compute indicators
- `market-order` / `pending-order` / `modify-order` / `cancel-order` — Direct order management (legacy)
- `account-info` — Show current account info
- `positions` / `orders` / `history` — Read-only broker surfaces
- `benchmark` — Performance benchmarking
- `broker-preflight` — Capture broker symbol constraints
- `diagnose` — Terminal diagnostics

**Key CLI flags for `llm-trade`:**
- `--paper` / `--demo` / `--live` — Execution mode (mutually exclusive)
- `--symbol` — Trading symbol (e.g., EURUSD)
- `--timeframe` — AUTO, M1...D1
- `--risk-per-trade` — Fraction (0.01 = 1%)
- `--loops` — Number of cycles (default: 1)
- `--continuous` — Infinite loop mode
- `--model` — Specific LLM model
- `--strategies` — `swing`, `scalp`, or `both`
- `--show-metrics` / `--show-rl-metrics` — Post-run reporting
- `--no-dashboard` — Disable auto-generated alpha dashboard
- `--poll-interval` — Seconds between market pulses (default: 5)
- `--tui` — Attach the OpenTUI command deck (single canonical TUI) and mirror state to the API server via `--api-sync`

**Trading modes:**
1. **Paper** — Internal simulation, no MT5 connection needed
2. **Demo** — Real MT5 execution on demo account (no confirmation required)
3. **Live** — Real MT5 execution on live account (requires `LLM_TRADER_ALLOW_LIVE=true` + `--yes` or interactive confirmation)

### 2.2 Other Entry Points

| Entry Point | Purpose | How to Run |
|------------|---------|------------|
| `tui_opentui/` | Single canonical terminal UI — OpenTUI command deck | `python mt5_trader.py deck` (or `START_DECK.bat`) |
| `ui/gui_dashboard.py` | Tkinter desktop terminal | `python ui/gui_dashboard.py` |
| `ui/alpha_dashboard.py` | Alpha health HTML dashboard | `python ui/alpha_dashboard.py` |
| `ui/gui_api.py` | WebSocket/REST API server | `python ui/gui_api.py` |
| `research/alpha_backtest_harness.py` | Alpha ensemble backtesting | `python research/alpha_backtest_harness.py` |
| `operations/launch_trading.py` | Operational launch wrapper | `python operations/launch_trading.py` |

---

## 3. Core Engine (`core/`)

The foundational layer. **15,792 lines** across 39 files. Everything below is deterministic and LLM-free — it can be tested and replayed without any API dependency.

### 3.1 Trading Safety (`trading_safety.py` — 972 lines)

**The single most critical module.** Deterministic, restart-safe safety primitives. No LLM dependency.

**Key classes:**

| Class | Purpose |
|-------|---------|
| `TradeProposal` | Immutable trade proposal (strategy, symbol, side, entry, SL, TP, confidence, features) |
| `ValidationResult` | Approval/rejection with reasons, confluence score, rule trace |
| `DeterministicValidator` | Pre-trade validation engine — checks R:R, setup confluence, cooldowns, rate limits |
| `PortfolioRiskArbiter` | Portfolio-level risk gating — correlation, sector concentration, heat budget |
| `TradingStore` | SQLite persistence for proposals, trades, orders, journal entries |

**Magic numbers:**
- `460001` — Swing strategy
- `460002` — Scalp strategy

**Validation rules (deterministic, no randomness):**
1. R:R ratio ≥ 1.0
2. Maximum 5 open positions
3. Cooldown between trades (configurable, default 300s)
4. Max trades per hour (configurable, default 5)
5. Daily loss limit (3% hard stop)
6. Max consecutive losses (5)
7. Confluence score threshold
8. Session filters (trading hours, blackout windows)
9. Calendar event blackout
10. Margin safety pre-check

### 3.2 Risk Manager (`risk_manager.py` — 756 lines)

Account-level risk management with daily/weekly circuit breakers.

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `risk_per_trade` | 0.01 | Max 1% risk per trade |
| `max_drawdown` | 0.10 | 10% max drawdown circuit breaker |
| `max_daily_loss` | 0.03 | 3% daily loss hard stop |
| `cooldown_seconds` | 300 | Min seconds between trades |
| `max_trades_per_hour` | 5 | Rate limit |
| `max_consecutive_losses` | 5 | Loss streak breaker |
| `equity_floor_fraction` | 0.20 | Permanent halt at 20% of initial equity |
| `max_portfolio_heat_r` | 4.0 | Max portfolio heat in R units |

**Features:**
- Position sizing via Kelly criterion (capped at risk_per_trade)
- Daily/weekly equity tracking with automatic resets
- Permanent halt when equity falls below floor
- Portfolio heat budget (sum of |entry-SL| values)
- State persistence to `risk_state.json`

### 3.3 Trade Manager (`trade_manager.py` — 1,130 lines)

Post-entry trade management: trailing stops, breakeven, partial exits, pyramiding.

**R-Multiple Management Ladder:**
| R Level | Action |
|---------|--------|
| +1R | Move stop to breakeven |
| +2R | Close 50% of position |
| +3R | Close another 25% (half of remainder) |
| Trail | Remaining position trails at 1.5R distance |

**Features:**
- Trailing stop with configurable R multiplier
- Breakeven stop at +1R
- Two-stage partial exit (50% at +2R, 25% more at +3R)
- Time-based stop (4 hours max stagnation)
- Pyramiding (scale-in at favorable levels)
- Trade conditions file (`trade_conditions.json`)
- State persistence to `trade_manager_state.json`

### 3.4 Technical Indicators (`technical_indicators.py` — 151 lines)

High-performance indicator computation engine. All functions are O(n) vectorized.

**Indicators:**
| Indicator | Function | Parameters |
|-----------|----------|------------|
| EMA | `ema(series, period)` | Span-based exponential moving average |
| RSI | `rsi(series, period=14)` | Wilder's RSI |
| MACD | `macd(series, fast=12, slow=26, signal=9)` | Returns (macd_line, signal_line, histogram) |
| ATR | `atr(high, low, close, period=14)` | Average True Range |
| WMA | `wma(series, period)` | Weighted Moving Average (convolution-based, O(n)) |
| HMA | `hma(series, period)` | Hull Moving Average (reduced lag) |
| ADX | `adx(high, low, close, period=14)` | Wilder's Average Directional Index |

**`compute_indicators(df, requests)`** — Batch computation from string requests:
```python
compute_indicators(df, ["RSI", "MACD", "EMA(50)", "ATR(14)", "ADX(14)", "ALPHA012", "ALPHA053"])
```

### 3.5 Order Execution (`order_execution.py` — 476 lines)

Order routing for both paper and live modes.

**Key classes/functions:**
| Component | Purpose |
|-----------|---------|
| `PaperBroker` | Internal simulation broker for paper mode |
| `place_market_order()` | Market order routing (paper or live) |
| `emergency_market_exit()` | Emergency close all positions |
| `place_pending_order()` | Limit/stop order placement |
| `modify_order_sltp()` | Modify SL/TP of existing positions |
| `cancel_pending_order()` | Cancel pending orders |
| `validate_order_params()` | Pre-trade parameter validation |
| `validate_pending_entry_geometry()` | SL/TP geometry validation |

### 3.6 Execution Safety Supervisor (`execution_safety_supervisor.py` — 211 lines)

Real-time execution monitoring with zero-latency circuit breakers.

**Safety guards:**
| Guard | Threshold | Action |
|-------|-----------|--------|
| Max latency | 250ms round-trip | Reject execution |
| Max slippage | 2.5x historical average spread | Reject order |
| Daily drawdown | 5% of daily peak equity | Halt trading |
| Heartbeat watchdog | 15s silence | Emergency liquidation |
| Warning drawdown | 15% of peak equity | Warning alert |
| Critical drawdown | 20% of peak equity | Permanent halt |
| Equity floor | 20% of initial equity | Permanent halt |

### 3.7 Alpha Factor System (4 files, 1,876 lines)

See [Section 9](#9-alpha-factor-system) for full details.

### 3.8 Other Core Modules

| Module | Lines | Purpose |
|--------|-------|---------|
| `file_utils.py` | ~100 | Safe atomic file writes (JSON) |
| `shared_state.py` | ~150 | Global mutable state registry |
| `skill_registry.py` | ~100 | Skill registration and lookup |
| `rate_limiter.py` | ~80 | Generic rate limiter |
| `http_retry.py` | ~100 | HTTP request retry with exponential backoff |
| `broker_preflight.py` | ~150 | Broker symbol/contract specification checks |
| `execution_planning.py` | ~200 | Planned order intent structures |
| `execution_policy.py` | ~150 | Per-symbol approved execution policies |
| `margin_safety.py` | ~100 | Pre-trade margin exhaustion gates |
| `mathematical_pillars.py` | ~300 | Kelly, ruin theory, portfolio math |
| `bocpd.py` | ~200 | Bayesian Online Changepoint Detection |
| `copula_tail_risk.py` | ~200 | Copula-based tail risk modeling |
| `meta_labeling.py` | ~150 | Meta-labeling for trade filtering |
| `transaction_cost_engine.py` | ~150 | Commission, spread, swap modeling |
| `urgency_sor.py` | ~100 | Urgency-adjusted smart order routing |
| `cointegration_and_compounding_governance.py` | ~200 | Cointegration testing for pairs |
| `cointegration_robustness.py` | ~150 | Robustness checks for cointegration |
| `fractional_diff.py` | ~100 | Fractional differentiation for stationarity |
| `hierarchical_portfolio.py` | ~150 | Hierarchical risk parity |
| `kalman_statarb.py` | ~200 | Kalman filter for stat-arb |
| `cpcv_backtester.py` | ~200 | Combinatorial Purged Cross-Validation |
| `cpcv_validation.py` | ~150 | CPCV validation gates |
| `expectancy_ruin_and_hierarchical_risk.py` | ~200 | Expectancy/ruin calculations |
| `trading_tools.py` | ~200 | General trading utilities |
| `trading_workflows.py` | ~150 | Trading workflow orchestration |
| `macro_event_alpha_engine.py` | ~200 | Macro event alpha generation |
| ` robustness_playbook.py` | ~150 | Robustness Playbook methodology robustness |
| `skill_registry.py` | ~100 | Trading skill registration |

### 3.9 Pipeline V2 (`core/harness/` — 410 lines)

Stage-based pipeline runner with telemetry, retries, SLA tracking, and fallback policies.

**Architecture:**
```
execute_cycle_v2()
  ├─ STAGE blackout.gate        (EventAgent, abort_cycle on hit)
  ├─ STAGE portfolio.consult    (PortfolioAgent, skip-fallback, advisory)
  ├─ STAGE legacy.cycle_body    (orchestrator.execute_cycle verbatim)
  └─ span telemetry CYCLE_SPAN_START/DONE around all of it
```

**Stage contracts (`stage_contracts.py`):**
- `StageSpec` — Declares key, agent, function, timeout, SLA, retries, fallback policy
- `StageResult` — Status (done/skipped/degraded/failed/aborted), duration, value
- `RunnerContext` — Orchestrator, shared results, trace_id, flags
- `CycleAbort` — Exception for aborting the entire cycle

**Fallback policies:** `continue`, `skip`, `degrade`, `abort_cycle`

---

## 4. Intelligence Layer (`intelligence/`)

The AI decision-making layer. **26,867 lines** across 62 files.

### 4.1 LLM Orchestrator (`llm_orchestrator.py` — 5,165 lines)

**The brain of the system.** Generates trade proposals using LLM analysis of market context.

**Key classes:**
| Class | Purpose |
|-------|---------|
| `LLMOrchestrator` | Main orchestrator — builds prompts, calls LLM, parses responses |
| `MockLLMOrchestrator` | Deterministic mock for paper mode and testing |

**Decision flow:**
1. Fetch OHLCV data + indicators + alpha factors
2. Build LLM prompt with market context, indicators, alpha signals, ensemble composite
3. Call LLM (via LLMRouter) with structured output schema
4. Parse JSON response into `TradeProposal`
5. Validate via `DeterministicValidator` → `PortfolioRiskArbiter`
6. Route to execution

**Prompt sections:**
1. System identity and trading rules
2. Account state (balance, equity, positions, risk limits)
3. Market context (price, indicators, alpha factors, calendar events)
4. **Alpha signal interpretation rules** (ensemble composite, individual alpha signals)
5. Decision framework (technical, fundamental, risk rationale)
6. Output schema (JSON with rationale, ensemble_alignment)

**Key features:**
- Multi-provider LLM routing with failover
- Structured JSON output with validation
- Mock mode for zero-cost testing
- Provider-specific timeout handling
- Prompt deduplication (hash-based)

### 4.2 Causal Intelligence

| Module | Purpose |
|--------|---------|
| `macro_graph.py` | Macro dynamics & intermarket causal DAG |
| `autonomous_causal_engine.py` | Causal validation & setup chain analyzer |
| `causal_propagate.py` | Causal path propagation |
| `causal_setup_map.py` | Setup-to-cause mapping |
| `opportunity_bus.py` | Hypothesis arbitration event bus |
| `opportunity_fusion.py` | Causal & technical agreement gate |

### 4.3 Memory System

| Module | Purpose |
|--------|---------|
| `trading_memory.py` | Authoritative SQLite L0-L3 memory layers |
| `local_llm_memory.py` | Compact local memory distillation |
| `semantic_memory.py` | High-dimensional scenario retrieval |
| `local_agent_memory.py` | Cloud database memory mirror (optional) |
| `local_agent_memory.py` | Local Remote Mirror-style memory (SQLite) |

### 4.4 Economic Dynamics

| Module | Purpose |
|--------|---------|
| `economic_dynamics_catalog.py` | 134 economic dynamics definitions |
| `economic_dynamics_measure.py` | Real-time macroeconomic metric scoring |
| `economic_dynamics_seed.py` | Initial dynamics seeding |
| `macrofundamental_pipeline.py` | Macro fundamental analysis pipeline |
| `macro_extraction.py` | Economic data extraction |
| `macro_observations.py` | Market observation generation |
| `macro_state_vector.py` | Macro state vector construction |

### 4.5 Feature Engineering

| Module | Purpose |
|--------|---------|
| `feature_builder.py` | Point-in-time completed-bar feature snapshots |
| `feature_schema.py` | Deterministic feature validation schema |
| `market_filters.py` | Trading session/time filters |
| `market_microstructure.py` | Microstructure analysis |
| `regime_classifier.py` | Market regime classification |

### 4.6 Graph & Reasoning

| Module | Purpose |
|--------|---------|
| `graph_engine_facade.py` | Graph engine interface |
| `graph_state.py` | Graph state management |
| `graph_nodes.py` | Graph node definitions |
| `graph_checkpointer.py` | Graph state checkpointing |
| `deliberative_graph.py` | Multi-step reasoning graph |
| `reflection_graph.py` | Self-reflection reasoning |
| `meta_reflector.py` | Meta-level reflection |

### 4.7 Analytical Frameworks (`intelligence/analytical_frameworks/`)

Seven domain-specific analytical engines:

| Engine | File | Purpose |
|--------|------|---------|
| Geopolitical | `v01_geopolitical_commodity_engine.py` | Geopolitical risk analysis |
| Quantitative/Chaos | `v02_quantitative_chaos_engine.py` | Statistical chaos models |
| Economic I/O | `v03_economic_input_output_engine.py` | Input-output economic models |
| Strategic/Adversarial | `v04_strategic_adversarial_engine.py` | Adversarial frameworks |
| Behavioral/Cognitive | `v05_behavioral_cognitive_engine.py` | Behavioral finance |
| Epistemological | `v06_epistemological_filter_engine.py` | Epistemological filters |
| Specialized/Niche | `v07_specialized_niche_engine.py` | Niche domain frameworks |

### 4.8 Other Intelligence Modules

| Module | Purpose |
|--------|---------|
| `active_knowledge_agent.py` | Active knowledge retrieval |
| `behavioral_macro_catalog.py` | Behavioral macro event catalog |
| `brain_observability.py` | Brain layer monitoring |
| `canonical_literary_mastery_engine.py` | Literary analysis for macro |
| `case_study_learner.py` / `case_study_library.py` / `case_study_matcher.py` | Case-based reasoning |
| `correlation_risk.py` | Correlation risk management |
| `cost_estimator.py` | Trade cost estimation |
| `cycle_brief.py` | Cycle briefing generation |
| `extrapolation_and_state_trajectory.py` | State extrapolation |
| `fact_checker.py` | Claim verification |
| `geopolitical_macro_foresight.py` | Geopolitical forecasting |
| `global_macro_ratios_and_synthetic_spreads.py` | Synthetic spread computation |
| `intermarket_regime.py` | Intermarket regime detection |
| `kronos_signal_validator.py` / `kronos_synthetic_generator.py` | Kronos signal processing |
| `lag_delay_engine.py` | Timestamped event tracking, slippage calibration |
| `multi_disciplinary_domain_knowledge.py` | Cross-domain knowledge |
| `sanctions_financial_plumbing_and_evasion.py` | Sanctions analysis |
| `sovereign_macro_and_literary_forensics.py` | Sovereign risk analysis |
| `trading_memory.py` | Trading memory layers |
| `win_probability_model.py` | Win probability estimation |

---

## 5. Data Layer (`data/`)

Data ingestion, quality gating, and LLM routing. **10,292 lines** across 28 files.

### 5.1 Market Data Provider (`market_data_provider.py` — 1,032 lines)

Central hub for all market data. Feeds the entire system.

**Key classes:**
| Class | Purpose |
|-------|---------|
| `MarketDataProvider` | Main data provider (OHLC, indicators, alpha factors) |
| `DOMCollector` | Depth of Market collection |
| `PerformanceMonitor` | Data fetch performance tracking |

**`fetch_data()` pipeline:**
```
MT5 OHLCV Fetch
    ↓
compute_indicators() → RSI, MACD, EMA, ATR, ADX
    ↓
compute_alpha_factors() → 18 active alpha factors
    ↓
combine_alphas() → IC-weighted composite signal
    ↓
AlphaDecayMonitor.update() → health status per alpha
    ↓
AlphaRotationEngine.update() → rotate dead alphas
    ↓
toon = { price, indicators, alpha_factors, alpha_ensemble, alpha_decay_health, alpha_rotation }
```

**Key functions:**
| Function | Purpose |
|----------|---------|
| `fetch_ohlc()` | Fetch OHLC data from MT5 |
| `tf_const()` | Convert timeframe string to MT5 constant |
| `fetch_data()` | Full data pipeline (OHLC + indicators + alphas + ensemble) |

**Supported timeframes:** M1, M5, M15, M30, H1, H4, D1, W1, MN

### 5.2 LLM Router (`llm_router.py` — 623 lines)

Multi-provider LLM routing with load balancing, failover, and rate limiting.

**Supported providers:**
| Provider | Model Examples | Env Var |
|----------|---------------|---------|
| OpenRouter | GPT-4, Claude, Llama, Gemini | `OPENROUTER_API_KEY` |
| Cerebras | Llama 3.1/3.3 (free, fast) | `CEREBRAS_API_KEY` |
| NVIDIA NIM | Llama, DeepSeek | `NVIDIA_KEY_1`, `NVIDIA_KEY_2` |
| TokenRouter | OpenAI-compatible | `TOKENROUTER_API_KEY` |
| RoutesMe | OpenAI-compatible | `ROUTESME_API_KEY` |

**Key classes:**
| Class | Purpose |
|-------|---------|
| `LLMRouter` | Main router — provider selection, failover, rate limiting |
| `BaseLLMProvider` | Base provider class with circuit breaker |
| `RateLimiter` | Per-provider rate limiter with blocking cap |
| `OpenRouterProvider` | OpenRouter implementation |
| `CerebrasProvider` | Cerebras implementation |
| `NVIDIAProvider` | NVIDIA NIM implementation |

**Features:**
- Per-agent routing (TRADER, ANALYST, RISK agents can use different providers)
- Provider failover (configurable via `LLM_TRADER_ALLOW_PROVIDER_FAILOVER`)
- Rate limiting (RPM, RPD, concurrent, tokens/hour, cost/day)
- Circuit breaker (3 consecutive failures → 5-minute cooldown)
- Bounded wait times (prevents market pulse thread freeze)

### 5.3 Data Quality (`data_quality.py`)

Fail-closed data validation gates:
- Missing data detection
- Stale data detection
- Price spike detection
- Volume anomaly detection

### 5.4 Data Requirements (`data_requirements.py`)

Per-setup data completeness enforcement — ensures enough historical data exists for the requested setup.

### 5.5 Data Source Pipeline (`data_source_pipeline.py` — 430 lines)

FRED and macroeconomic data ingestion:
- Series cataloging
- Data freshness checks
- Automatic ingestion scheduling

### 5.6 External Data Providers

| Provider | Module | Purpose |
|----------|--------|---------|
| ForexFactory | `forexfactory_provider.py` | Economic calendar |
| Seeking Alpha | `seeking_alpha_provider.py` | Fundamental analysis, transcripts |
| SEC EDGAR | `sec_edgar_provider.py` | SEC filings |
| Yahoo Finance | `yahoo_finance_provider.py` | Market data backup |
| Portfolio123 | `portfolio123_provider.py` | Factor analysis & ranking |
| OpenBB | `openbb_data_bridge.py` | Institutional market data |
| EIA | `eia_provider.py` | Energy data |
| CFTC COT | `cftc_cot_provider.py` | Commitment of Traders |
| Zero-Lag Macro | `zero_lag_macro_provider.py` | Physical proxies, spreads, liquidity |
| Alternative Data | `alternative_data_provider.py` / `alternative_data_registry.py` | Non-traditional data |
| Exness | `exness_provider.py` | Broker-specific data |
| MT5 Calendar | `mt5_calendar_enhanced.py` / `mt5_calendar_wrapper.py` | Broker calendar |

### 5.7 Provider Circuit Breaker (`provider_circuit.py`)

Per-provider circuit breaker with configurable failure thresholds and cooldown periods.

### 5.8 Real-Time News (`realtime_news_ingest.py`)

Live RSS and NewsAPI breaking news ingestion with deduplication and priority scoring.

---

## 6. Operations Layer (`operations/`)

Runtime operations, auditing, and security. **9,619 lines** across 40 files.

### 6.1 Core Operations

| Module | Lines | Purpose |
|--------|-------|---------|
| `agent_operations.py` | ~400 | Task leases, heartbeats, event logging |
| `agent_contracts.py` | ~200 | Least-privilege write permission contracts |
| `agent_scheduler.py` | ~300 | Periodic research & maintenance schedules |
| `dual_velocity.py` | ~200 | Fast tick monitor vs slow research lanes |
| `supervisor.py` | ~400 | System supervision and health monitoring |

### 6.2 Journal & Audit

| Module | Lines | Purpose |
|--------|-------|---------|
| `journal_audit.py` | 872 | 14-rule journal integrity verification |
| `journal_backup.py` | ~200 | AES-256-GCM encrypted database backups |
| `journal_migration.py` | ~150 | Legacy record migration |
| `journal_scope.py` | ~100 | DEMO/LIVE scope isolation |
| `daily_reconciliation.py` | ~200 | Broker deals vs journal reconciler |
| `legacy_reconciliation.py` | ~150 | Legacy record quarantine & recovery |

### 6.3 Security

| Module | Lines | Purpose |
|--------|-------|---------|
| `security_audit.py` | 291 | Credential scanning & secret sanitizer |
| `security_scrub.py` | ~100 | Secret redaction utilities |
| `secrets_guard.py` | ~100 | Secret storage protection |

### 6.4 Alerts

| Module | Lines | Purpose |
|--------|-------|---------|
| `alert_outbox.py` | 217 | Durable operator-alert handoff (SQLite) |
| `alert_delivery.py` | ~100 | Webhook delivery for alerts |
| `health_alerts.py` | ~100 | Health-based alert generation |

### 6.5 Deployment & Lifecycle

| Module | Purpose |
|--------|---------|
| `live_rollout_gate.py` | Operator-signed live deployment manifests |
| `readiness_report.py` | Comprehensive readiness gate aggregator |
| `strategy_lifecycle.py` | Strategy lifecycle management |
| `strategy_evaluation.py` | Strategy performance evaluation |
| `strategy_factory.py` | Strategy instantiation |
| `restart_recovery.py` | Crash recovery and state restoration |
| `incident_drills.py` | Incident response drills |

### 6.6 Research Operations

| Module | Purpose |
|--------|---------|
| `research_influence_gate.py` | Controls research influence on live trading |
| `research_validation.py` (also in research/) | Promotion gates & out-of-sample testing |
| `reward_attribution.py` | Performance attribution |
| `reward_ledger.py` | Reward tracking ledger |
| `reward_sustainability.py` | Reward sustainability analysis |
| `analytics.py` | Performance analytics |

### 6.7 Operational Agents (`operations/agents/`)

| Agent | Purpose |
|-------|---------|
| `event_agent.py` | Event detection and blackout management |
| `incident_agent.py` | Incident detection and response |
| `learning_agent.py` | Continuous learning orchestration |
| `portfolio_agent.py` | Portfolio-level advisory |
| `research_agent.py` | Research pipeline orchestration |

---

## 7. Research Layer (`research/`)

ML/RL, backtesting, and model management. **11,484 lines** across 50 files.

### 7.1 Alpha Research

| Module | Lines | Purpose |
|--------|-------|---------|
| `alpha_backtest_harness.py` | 852 | IC-weighted vs equal-weight backtesting |
| `ensemble_backtest.py` | ~300 | Ensemble strategy backtesting |
| `edge_lifecycle.py` | ~200 | Edge discovery → validation → deployment |

### 7.2 Reinforcement Learning

| Module | Purpose |
|--------|---------|
| `ppo_trainer.py` | Proximal Policy Optimization RL agent |
| `meta_rl_trainer.py` | Meta-RL regime adaptation engine |
| `rl_trainer.py` | General RL training loop |
| `rl_action_bridge.py` | Bridge RL actions to trading decisions |
| `rl_analytics.py` | RL performance analytics |

### 7.3 Supervised Learning

| Module | Purpose |
|--------|---------|
| `supervised_training.py` | Walk-forward logistic baseline model |
| `model_registry.py` | Versioned model weights & drift monitoring |
| `model_selector.py` | Model selection interface |
| `model_family_gate.py` | Model family safety gates |

### 7.4 Kronos (Foundation Model)

| Module | Purpose |
|--------|---------|
| `kronos_bridge.py` | Kronos foundation model interface |
| `kronos_mt5_adapter.py` | Time-series tokenization & forecasting |
| `kronos_downloader.py` | Model download management |
| `kronos_trading_integrator.py` | Integration with trading system |

**Pretrained models (in `models/`):**
- `kronos-mini/`
- `kronos-small/`
- `kronos-tokenizer-2k/`

### 7.5 Backtesting

| Module | Purpose |
|--------|---------|
| `broker_replay.py` | High-fidelity historical tick replay |
| `historical_regimes.py` | Historical regime research |
| `historical_regime_replay.py` | Regime replay backtesting |
| `regime_monte_carlo.py` | Monte Carlo simulation under regimes |
| `synthetic_path_simulator.py` | Synthetic price path generation |
| `eval_harness.py` | Evaluation harness |
| `forward_test_report.py` | Forward test comparison |

### 7.6 Research Validation

| Module | Purpose |
|--------|---------|
| `research_validation.py` | Promotion gates & out-of-sample testing |
| `research_stress.py` | Stress testing |
| `research_runs.py` | Validation run recording |
| `sample_gate.py` | 100-sample / multi-regime qualification |
| `promotion_package.py` | Tamper-evident model deployment packages |
| `baseline_research.py` | Purged K-fold cross-validation |
| `adaptive_research_service.py` | Adaptive research orchestration |
| `research_sprint_planner.py` | Research sprint planning |

### 7.7 Advanced Quantitative

| Module | Purpose |
|--------|---------|
| `mathematical_pillars.py` | Kelly criterion, ruin theory, portfolio math |
| `bocpd.py` | Bayesian Online Changepoint Detection |
| `copula_tail_risk.py` | Copula-based tail risk modeling |
| `meta_labeling.py` | Meta-labeling for trade filtering |
| `cointegration_robustness.py` | Robustness checks for cointegration |
| `robustness_playbook.py` | Robustness Playbook methodology robustness |
| `hierarchical_portfolio.py` | Hierarchical risk parity |
| `kalman_statarb.py` | Kalman filter for stat-arb |
| `cpcv_backtester.py` | Combinatorial Purged Cross-Validation |
| `contextual_bandit.py` | Thompson sampling exploration gate |
| `risk_parity_engine.py` | Risk parity portfolio optimization |
| `grpo_proposer.py` | GRPO proposal generation |
| `profitability_foundation.py` | Profitability foundation analysis |
| `code_interpreter.py` | Code execution for research |
| `demo_sample_generator.py` | Demo data generation |

---

## 8. UI Layer (`ui/`)

User interfaces and dashboards. **5,633 lines** across 18 files.

### 8.1 Desktop UI

| Module | Purpose |
|--------|---------|
| `gui_dashboard.py` | Tkinter desktop trading terminal |
| `gui_api.py` | WebSocket/REST API server for GUI |
| `gui_launcher.py` | GUI launch helper |
| `main_window.py` | Main application window |

### 8.2 Terminal UI

**Single canonical TUI:** the OpenTUI command deck (`tui_opentui/`) — `python mt5_trader.py deck` / `START_DECK.bat` (requires bun). The former Rich-based TUIs (`ui/tui_dashboard.py`, `ui/unified_tui.py`, `ui/display.py`, `ui/log_monitor.py`, `ui/settings_tui.py`) are retired; their modules remain as import-safe deprecation stubs.

| Module | Purpose |
|--------|---------|
| `tui_opentui/` | OpenTUI command deck — the single terminal UI |
| `interactive_menu.py` | Guided configuration menu (questionary) |
| `scanner.py` | Market scanner |
| `setup_terminal.py` | Terminal setup |

### 8.3 Web UI

| Module | Purpose |
|--------|---------|
| `web_dashboard_adapter.py` | Web dashboard adapter |
| `economic_news_terminal.py` | Economic news terminal |

### 8.4 Alpha Dashboard

| Module | Lines | Purpose |
|--------|-------|---------|
| `alpha_dashboard.py` | 565 | Self-contained HTML dashboard (plotly.js) |
| `dashboard_report.py` | ~200 | Dashboard data generation |

**Alpha Dashboard features:**
- Status bar (HEALTHY/DEGRADED/REVERSED/DEAD counts)
- Alpha health cards with IC mean, trend, status
- Interactive IC trend charts (plotly.js)
- IC distribution bar chart
- Decay alert timeline
- Ensemble weight distribution
- Auto-generates after each trading cycle
- Dark theme, auto-refresh every 60 seconds

---

## 9. Alpha Factor System

The complete Quant Platform-style alpha factor pipeline. **4 files, 1,876 lines**.

### 9.1 Alpha Factors (`core/alpha_factors.py` — 525 lines)

**16 reusable primitives** (all O(n) vectorized):

| Primitive | Function | Complexity |
|-----------|----------|------------|
| `ts_rank(df, window)` | Rolling percentile rank | O(n·w) |
| `ts_argmax(ts_argmin)` | Which bar hit max/min in window | O(n·w) |
| `ts_min(ts_max)` | Rolling min/max | O(n) |
| `delta(df, period)` | Momentum: `close - close[n]` | O(n) |
| `delay(df, period)` | Lagged value | O(n) |
| `rank(df)` | Percentile rank of full series | O(n) |
| `scale(df, k)` | Rescale so `sum(abs) = k` | O(n) |
| `decay_linear(df, period)` | Linear-weighted MA | O(n) |
| `signed_power(df, exp)` | Sign-preserving power | O(n) |
| `log_safe(df)` | Guarded natural log | O(n) |

**23 alpha factors:**

| Alpha | Category | What It Captures |
|-------|----------|-----------------|
| **#1** | Volatility | Volatility regime flip detector |
| **#5** | VWAP | Open vs VWAP deviation × close vs VWAP distance |
| **#9** | Momentum | Momentum flip / trend continuation |
| **#12** | Volume-Price | Volume-price divergence (simplest, most robust) |
| **#14** | Returns | Returns acceleration × open-volume correlation |
| **#17** | Composite | Triple rank: price level × acceleration × volume |
| **#19** | Mean Reversion | Long-term mean reversion × short-term momentum |
| **#22** | Correlation | High-volume correlation shift × volatility |
| **#23** | Overbought | Overbought high reversal |
| **#25** | Bearish | Compound bearish signal |
| **#28** | Value | Value zone mean reversion |
| **#30** | Consensus | Directional consensus × volume ratio |
| **#34** | Volatility | Volatility compression + directional momentum |
| **#35** | Composite | Volume × range × return rank composite |
| **#36** | Composite | 5-component multi-factor composite |
| **#37** | Mean Reversion | Long-term open-close reversal × short-term gap |
| **#38** | Gap | Gap detection × close-to-open pattern |
| **#41** | VWAP | VWAP deviation from geometric midpoint |
| **#43** | Volume | Relative volume surge × reversal momentum |
| **#53** | Stochastic | Williams %R-style position in bar |
| **#55** | Stochastic | Stochastic oscillator × volume correlation |
| **#60** | Tick Rule | Tick rule proxy × close extremity |
| **#101** | Composite | Close-open directional strength × volume |

**Active pipeline (18 of 23 alphas):** #12, #53, #55, #34, #17, #9, #41, #101, #30, #35, #14, #22, #25, #19, #23, #36, #37, #43

**Registry:** `_ALPHA_REGISTRY` maps request strings (e.g., `"ALPHA012"`) to functions.

### 9.2 Alpha Ensemble (`core/alpha_ensemble.py` — 446 lines)

Quant Platform Brain-style alpha combination engine.

**Pipeline:**
```
Individual Alphas → Normalize → Compute IC → Weight by IC_IR → Composite Signal
       ↓               ↓            ↓              ↓                ↓
  alpha_012        z-score     Spearman ρ     w = IR/ΣIR      weighted sum
  alpha_053        expanding    rolling 60     high IR =        → z-scored
  alpha_055        window       bar window      more weight      composite
```

**Quality metrics per alpha:**
| Metric | Description |
|--------|-------------|
| `ic_mean` | Mean Spearman IC with forward returns |
| `ic_std` | Standard deviation of IC |
| `ic_ir` | IC Information Ratio (IC/stdev) — the key metric |
| `ic_positive_pct` | % of periods with positive IC |
| `turnover` | Average daily change in normalized signal |
| `half_life` | Bars until IC autocorrelation decays to 50% |
| `stability` | Correlation of IC in first half vs second half |

**Key functions:**
| Function | Purpose |
|----------|---------|
| `combine_alphas()` | Main ensemble engine — produces composite signal |
| `interpret_composite()` | Signal/strength/confidence at a bar |
| `format_quality_report()` | Human-readable quality table |
| `compute_ic_weights()` | IC_IR-based weighting |

**Output signal:**
- `composite`: z-scored composite signal
- `signal`: "LONG" / "SHORT" / "NEUTRAL" (|composite| > 1.0)
- `confidence`: "HIGH" (>2σ) / "MEDIUM" (>1σ) / "LOW"

### 9.3 Alpha Decay Monitor (`core/alpha_decay_monitor.py` — 470 lines)

Real-time alpha quality monitoring and decay detection.

**Health statuses:**
| Status | Condition | Alert |
|--------|-----------|-------|
| `HEALTHY` | IC > threshold, no sustained decay | None |
| `DEGRADED` | IC < 0.02 for 5+ consecutive bars | WARNING |
| `REVERSED` | IC < -0.01 for 5+ consecutive bars | CRITICAL |
| `DEAD` | Mean IC < reversal threshold overall | CRITICAL |

**Alert flow:**
```
AlphaDecayMonitor.update() → compute rolling IC
    ↓
check_decay() → generate alerts
    ↓
_emit_alert() → enqueue to operator outbox (trading_state.db)
    ↓
State persisted to alpha_decay_state.json
```

### 9.4 Alpha Rotation Engine (`core/alpha_rotation.py` — 435 lines)

Automatic dead alpha replacement and candidate promotion.

**Rotation lifecycle:**
```
Full Pool (22 alphas)
    ↓ AlphaRotationEngine manages
Active Set (5-20 alphas)
    ↓
compute_indicators() → only active alphas computed
    ↓
combine_alphas() → composite signal
    ↓
Decay Monitor → reports health
    ↓
Rotation Engine → removes DEAD, promotes best candidate
```

**Key features:**
- Dead alpha removal (DEAD/REVERSED status)
- Candidate promotion by IC_IR from full pool
- Cooldown: dead alphas can't be re-promoted for 50 bars
- Min/max bounds: never below 5, never above 20 alphas
- State persistence to `alpha_rotation_state.json`

### 9.5 Alpha Backtest Harness (`research/alpha_backtest_harness.py` — 852 lines)

Validates IC weighting vs equal-weight ensemble.

**Comparison metrics:**
- Sharpe ratio, Sortino ratio, Calmar ratio
- Max drawdown, win rate, profit factor
- Trade frequency / turnover
- Per-regime performance (trending vs ranging)

### 9.6 Alpha Dashboard (`ui/alpha_dashboard.py` — 565 lines)

Self-contained HTML dashboard with plotly.js. Auto-generates after each trading cycle.

---

## 10. Configuration & Environment

### 10.1 Environment Variables

**MT5 Credentials:**
| Variable | Purpose |
|----------|---------|
| `MT5_LOGIN` | Broker account login |
| `MT5_PASSWORD` | Broker password |
| `MT5_SERVER` | Broker server name |
| `LLM_TRADER_ALLOW_LIVE` | Must be `true` for live trading |
| `MT5_INIT_TIMEOUT_MS` | MT5 connection timeout (default: 5000ms) |

**LLM Providers:**
| Variable | Purpose |
|----------|---------|
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `CEREBRAS_API_KEY` | Cerebras API key |
| `NVIDIA_KEY_1`, `NVIDIA_KEY_2` | NVIDIA NIM keys |
| `TOKENROUTER_API_KEY` | TokenRouter key |
| `ROUTESME_API_KEY` | RoutesMe key |

**Per-Agent Routing:**
| Variable | Purpose |
|----------|---------|
| `TRADER_LLM_PROVIDER` / `TRADER_LLM_MODEL` | Trader agent LLM config |
| `ANALYST_LLM_PROVIDER` / `ANALYST_LLM_MODEL` | Analyst agent LLM config |
| `RISK_LLM_PROVIDER` / `RISK_LLM_MODEL` | Risk agent LLM config |

**LLM Router Tuning:**
| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_ROUTER_TIMEOUT` | 30s | Per-call LLM timeout |
| `LLM_ROUTER_MAX_RATE_WAIT` | 15s | Max wait when rate-limited |
| `LLM_TRADER_ALLOW_PROVIDER_FAILOVER` | false | Allow cross-provider failover |

**Data APIs:**
| Variable | Purpose |
|----------|---------|
| `NEWSAPI_KEY` | NewsAPI key |
| `FRED_API_KEY` | FRED economic data key |

**Optional Remote Mirror:**
| Variable | Purpose |
|----------|---------|
| `REMOTE_MIRROR_ENABLED` | Enable cloud memory mirror |
| `LOCAL_MEMORY_ENABLED` | Enable local SQLite memory |

**Pipeline:**
| Variable | Purpose |
|----------|---------|
| `PIPELINE_V2` | Enable V2 pipeline runner |
| `TRADING_JOURNAL_MODE` | DEMO/LIVE/PAPER scope |

### 10.2 Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (from `.env.example`) |
| `accounts.json` | Multi-account configuration |
| `config.ini` | Legacy configuration (risk, limits, trading) |
| `config/config.example.ini` | Example config |
| `pytest.ini` | Test configuration |

### 10.3 Account Configuration

Accounts are stored in `accounts.json` with password environment variable references (never plaintext):

```json
{
    "default": "antigravity",
    "accounts": {
        "antigravity": {
            "login": 5055089321,
            "password_env": "MT5_PASSWORD",
            "server": "MetaQuotes-Demo",
            "type": "DEMO"
        }
    }
}
```

---

## 11. State Files & Persistence

### 11.1 SQLite Database (`trading_state.db`)

**The authoritative persistence store.** Contains:

| Table | Purpose |
|-------|---------|
| `proposals` | Trade proposals (approved/rejected) |
| `trades` | Executed trades with full metadata |
| `orders` | Order lifecycle tracking |
| `alerts_outbox` | Operator alerts (idempotent, leased) |
| `research_validation_runs` | Research validation evidence |
| `journal_*` | Journal scope tables (DEMO/LIVE isolated) |

### 11.2 JSON State Files

| File | Purpose |
|------|---------|
| `risk_state.json` | Risk manager state (drawdown, cooldowns, trade history) |
| `trade_manager_state.json` | Trade manager state (partial exits, pyramid entries) |
| `alpha_decay_state.json` | Alpha decay monitor state (IC history, health, alerts) |
| `alpha_rotation_state.json` | Alpha rotation state (active set, dead timestamps, history) |
| `autonomy_state.json` | Autonomy configuration state |
| `supervisor_state.json` | Supervisor state |
| `trade_conditions.json` | Active trade conditions |
| `rl_model.json` | RL model metadata |
| `accounts.json` | Multi-account configuration |

### 11.3 Other State

| File | Purpose |
|------|---------|
| `alpha_dashboard.html` | Auto-generated alpha health dashboard |
| `mt5_trader.log` | Trading session log |
| `api_cache.json` | API response cache |
| `journal_audit.json` | Last audit results |
| `lessons.jsonl` | Accumulated trading lessons |
| `event_windows.json` | Event window tracking |

---

## 12. Test Suite

### 12.1 Scale

| Metric | Value |
|--------|-------|
| Test files | 212 |
| Lines of test code | ~23,000 |
| Test framework | pytest |
| Config | `pytest.ini` (testpaths=tests, --tb=short -q) |

### 12.2 Key Test Categories

**Alpha System Tests:**
| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_alpha_factors.py` | 22 | Primitives + all 22 alphas + pipeline integration |
| `test_alpha_ensemble.py` | 30 | Normalization, IC, weights, composite, quality |
| `test_alpha_decay_monitor.py` | 20 | IC tracking, health status, alerts, persistence |
| `test_alpha_rotation.py` | 24 | Dead removal, candidate promotion, cooldown, bounds |
| `test_alpha_dashboard.py` | 25 | Dashboard generation, auto-gen, CLI |
| `test_alpha_backtest_harness.py` | 29 | Synthetic data, signals, comparison, regime analysis |

**Core System Tests:**
| Test File | Coverage |
|-----------|----------|
| `test_indicators.py` | Technical indicator correctness |
| `test_trading_safety.py` | Validation rules, proposals, store |
| `test_risk_manager_safety.py` | Risk manager edge cases |
| `test_trade_management.py` | Trade manager features |
| `test_execution_metrics.py` | Execution metrics |
| `test_execution_planning.py` | Order planning |
| `test_broker_preflight.py` | Broker specification checks |
| `test_margin_safety.py` | Margin exhaustion gates |
| `test_hierarchical_portfolio.py` | Portfolio optimization |
| `test_mathematical_pillars.py` | Kelly, ruin theory |

**Intelligence Tests:**
| Test File | Coverage |
|-----------|----------|
| `test_graph_engine_facade.py` | Graph engine |
| `test_deliberative_graph.py` | Multi-step reasoning |
| `test_reflection_graph.py` | Self-reflection |
| `test_macro_observations.py` | Macro observations |
| `test_macro_event_alpha_engine.py` | Macro event alphas |
| `test_geopolitical_macro_foresight.py` | Geopolitical analysis |

**Operations Tests:**
| Test File | Coverage |
|-----------|----------|
| `test_alert_outbox.py` | Alert durability |
| `test_journal_audit.py` | Journal integrity |
| `test_journal_backup_encryption.py` | Backup encryption |
| `test_security_audit.py` | Security scanning |
| `test_readiness_report.py` | Readiness gates |
| `test_live_rollout_gate.py` | Deployment safety |
| `test_incident_drills.py` | Incident response |

**Research Tests:**
| Test File | Coverage |
|-----------|----------|
| `test_vbt_engine.py` | VectorBT engine |
| `test_broker_replay.py` | Tick replay |
| `test_research_validation.py` | Promotion gates |
| `test_supervised_training.py` | ML training |
| `test_ppo_trainer.py` | RL training |
| `test_meta_rl_trainer.py` | Meta-RL |

**Section Hardening Tests (A-K):**
- `test_section_a_hardening.py` through `test_section_k_hardening.py`
- Comprehensive security and hardening validation

---

## 13. Module Dependency Map

### Critical Path (Trade Execution)

```
mt5_trader.py
  → llm_orchestrator.py (decision making)
    → market_data_provider.py (data fetch)
      → technical_indicators.py (indicators)
      → alpha_factors.py (alpha computation)
      → alpha_ensemble.py (composite signal)
      → alpha_decay_monitor.py (health monitoring)
      → alpha_rotation.py (set management)
    → llm_router.py (LLM API calls)
    → trading_safety.py (validation)
      → risk_manager.py (risk checks)
    → order_execution.py (order routing)
    → trade_manager.py (post-entry management)
    → execution_safety_supervisor.py (live monitoring)
```

### Data Flow Summary

```
MT5 Terminal
    ↓ OHLCV + DOM
market_data_provider.py
    ↓ compute_indicators()
technical_indicators.py + alpha_factors.py
    ↓ combine_alphas()
alpha_ensemble.py
    ↓
toon = { price, indicators, alpha_factors, alpha_ensemble, alpha_decay_health, alpha_rotation }
    ↓
llm_orchestrator.py → LLM API
    ↓ TradeProposal
trading_safety.py (DeterministicValidator → PortfolioRiskArbiter)
    ↓ ValidationResult
order_execution.py → MT5 Terminal
    ↓
trade_manager.py (trailing stops, partial exits)
    ↓
execution_safety_supervisor.py (latency, slippage, drawdown monitoring)
```

---

## 14. Key Design Principles

### 14.1 Fail-Closed Safety

Every safety check defaults to **reject/block**. The system must explicitly approve every action:
- Live trading requires `LLM_TRADER_ALLOW_LIVE=true` + `--yes` + confirmation
- All proposals pass through `DeterministicValidator` → `PortfolioRiskArbiter`
- Emergency exit is always available and never blocked
- Circuit breakers halt trading on drawdown, latency, or slippage exceedance

### 14.2 Deterministic Validation

Trade proposals are validated **without randomness or LLM calls**. The validation layer:
- Can be unit-tested and replayed from journal data
- Uses only deterministic rules (R:R, cooldowns, rate limits, margin)
- Never trusts the LLM's confidence score without independent verification

### 14.3 Scope Isolation

DEMO and LIVE journals are isolated:
- Different database scope tables
- Fingerprinted by `login|server` from MT5's authenticated identity
- PAPER mode uses no MT5 connection at all

### 14.4 No Credential Leakage

- Passwords are stored as environment variable references (never plaintext)
- `AccountManager.password_for()` reads from env vars only
- Alert payloads are redacted (passwords, secrets, tokens, API keys)
- Journal audit scans for leaked credentials

### 14.5 Progressive Autonomy

The system can operate at multiple autonomy levels:
1. **Manual** — Human makes all decisions
2. **Advisory** — LLM suggests, human approves
3. **Supervised** — LLM decides, deterministic validation gates
4. **Autonomous** — Full autonomous loop with safety rails

### 14.6 Zero-Lookahead

No future data is used in any computation:
- Alpha factors use only past/current data
- IC is computed with forward returns that are shifted appropriately
- Feature snapshots are point-in-time completed bars only

### 14.7 Graceful Degradation

When components fail:
- LLM provider failure → deterministic HOLD (fail-closed)
- MT5 disconnection → reconnection attempts with bounded timeouts
- Data quality issues → rejected by fail-closed gates
- Alpha decay → automatic rotation to healthy alternatives
- Dashboard generation failure → never interrupts trading loop

---

## Appendix: File Inventory

### Root Level
| File | Lines | Purpose |
|------|-------|---------|
| `mt5_trader.py` | 2,872 | Main CLI entry point |
| `requirements.txt` | 20 | Python dependencies |
| `README.md` | ~500 | Project documentation |
| `.env.example` | ~120 | Environment variable template |
| `pytest.ini` | 6 | Test configuration |
| `accounts.json` | 10 | Multi-account configuration |
| `START_DECK.bat` | ~5 | Windows launcher |

### Directory Summary
| Directory | Files | LOC | Purpose |
|-----------|-------|-----|---------|
| `core/` | 39 | 15,792 | Foundational trading engine |
| `intelligence/` | 62 | 26,867 | AI decision-making |
| `data/` | 28 | 10,292 | Data pipelines & LLM routing |
| `operations/` | 40 | 9,619 | Runtime operations & auditing |
| `research/` | 50 | 11,484 | ML/RL & backtesting |
| `ui/` | 18 | 5,633 | User interfaces |
| `tests/` | 212 | 22,711 | Test suite |
| `docs/` | 38 | — | Documentation |
| `strategies/` | 1 | — | Strategy definitions (mostly empty) |
| `brain/` | 0 | — | Brain layer (empty, reserved) |
| `models/` | 3 dirs | — | Pretrained model weights |
| `ops/` | 1 dir | — | Operational tooling |
| `tools/` | 1 dir | — | Scrapers |
| `quant_knowledge_framework/` | — | — | Quantitative knowledge base |
| `educational_platform_knowledge_base/` | — | — | Educational Platform knowledge base |

---

*This document is the single source of truth. Update it when components change.*
