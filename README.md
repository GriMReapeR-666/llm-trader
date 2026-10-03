# Autonomous LLM Trader System

An institutional-grade, multi-agent autonomous trading system integrating Large Language Models (LLMs), Reinforcement Learning (Kronos & PPO), Autonomous Causal Inference, and Deterministic Fail-Closed Risk Management for MetaTrader 5.

---

## 🏛️ Project Directory Structure

```
.
├── mt5_trader.py                      # Main CLI entrypoint and unified facade
├── requirements.txt                   # Production Python dependencies
├── pytest.ini                         # Automated test suite configuration
├── accounts.json / config.ini         # Multi-account configuration & credentials
├── trading_state.db                   # Authoritative SQLite persistence store
│
├── core/                              # Foundational Trading, Risk & Order Engine
│   ├── trading_safety.py              # DeterministicValidator & TradingStore schema
│   ├── risk_manager.py                # Account drawdown breaker & 1% risk sizing
│   ├── trade_manager.py               # Trailing stops, break-even & partial exits
│   ├── order_execution.py             # PaperBroker & protective order routing
│   ├── technical_indicators.py        # High-performance EMA, RSI, MACD, ATR, ADX
│   ├── margin_safety.py               # Pre-trade margin exhaustion gates
│   ├── broker_preflight.py            # Broker symbol & contract specification checks
│   ├── execution_planning.py          # Immutable planned order intent structures
│   └── execution_policy.py            # Per-symbol approved execution policies
│
├── intelligence/                      # Causal Inference, Macro Graph & Memory
│   ├── llm_orchestrator.py            # Core orchestration engine & decision loop
│   ├── macro_graph.py                 # Macro dynamics & intermarket causal DAG
│   ├── autonomous_causal_engine.py    # Causal validation & setup chain analyzer
│   ├── economic_dynamics_catalog.py   # 134 economic dynamics definitions
│   ├── economic_dynamics_measure.py   # Real-time macroeconomic metric scoring
│   ├── opportunity_bus.py             # Hypothesis arbitration event bus
│   ├── opportunity_fusion.py          # Causal & technical agreement gate
│   ├── feature_builder.py             # Point-in-time completed-bar feature snapshots
│   ├── feature_schema.py              # Deterministic feature validation schema
│   ├── trading_memory.py              # Authoritative SQLite L0-L3 memory layers
│   ├── local_llm_memory.py            # Compact local memory distillation
│   └── semantic_memory.py             # High-dimensional scenario retrieval
│
├── data/                              # Data Pipelines, Feeds & LLM Gateways
│   ├── market_data_provider.py        # MT5 tick, OHLC & DOM collector
│   ├── data_quality.py                # Fail-closed data validation gates
│   ├── data_requirements.py           # Per-setup data completeness enforcement
│   ├── data_source_pipeline.py        # FRED & macroeconomic data ingestion
│   ├── realtime_news_ingest.py        # Live RSS & NewsAPI breaking news ingest
│   ├── mt5_calendar_enhanced.py       # High-impact economic calendar integration
│   ├── seeking_alpha_provider.py      # Fundamental analysis & transcript feeds
│   ├── openbb_data_bridge.py          # OpenBB institutional market data bridge
│   ├── portfolio123_provider.py       # Factor analysis & ranking feeds
│   ├── zero_lag_macro_provider.py     # Zero-lag physical proxies, spreads & liquidity nowcasts
│   ├── cerebras_client.py             # Ultra-fast Cerebras inference client
│   └── llm_router.py                  # Multi-provider LLM failover router
│
├── research/                          # ML / RL, Backtesting & Kronos Engine
│   ├── kronos_bridge.py               # Kronos foundation model interface
│   ├── kronos_mt5_adapter.py          # Time-series tokenization & forecasting
│   ├── ppo_trainer.py                 # Proximal Policy Optimization RL agent
│   ├── meta_rl_trainer.py             # Meta-RL regime adaptation engine
│   ├── supervised_training.py         # Walk-forward logistic baseline model
│   ├── contextual_bandit.py           # Thompson sampling exploration gate
│   ├── baseline_research.py           # Purged K-fold cross-validation
│   ├── research_validation.py         # Promotion gates & out-of-sample testing
│   ├── sample_gate.py                 # 100-sample / multi-regime qualification
│   ├── promotion_package.py           # Tamper-evident model deployment packages
│   ├── model_registry.py              # Versioned model weights & drift monitoring
│   └── broker_replay.py               # High-fidelity historical tick replay
│
├── ui/                                # User Interfaces & Dashboards
│   ├── gui_dashboard.py               # Tkinter desktop trading terminal
│   ├── gui_api.py                     # WebSocket / REST API server for UI
│   ├── web_dashboard_adapter.py       # Mirrors live state to the API server
│   └── interactive_menu.py            # Guided configuration menu
│
├── tui_opentui/                       # THE single canonical TUI (OpenTUI deck)
│
├── operations/                        # Runtime Operations, Audits & Security
│   ├── agent_operations.py            # Task leases, heartbeats & event logging
│   ├── agent_contracts.py             # Least-privilege write permission contracts
│   ├── agent_scheduler.py             # Periodic research & maintenance schedules
│   ├── dual_velocity.py               # Fast tick monitor vs slow research lanes
│   ├── journal_audit.py               # Journal integrity verification (14 rules)
│   ├── journal_backup.py              # AES-256-GCM encrypted database backups
│   ├── daily_reconciliation.py        # Broker deals vs journal reconciler
│   ├── legacy_reconciliation.py       # Legacy record quarantine & recovery
│   ├── live_rollout_gate.py           # Operator-signed live deployment manifests
│   ├── readiness_report.py            # Comprehensive readiness gate aggregator
│   └── security_audit.py              # Credential scanning & secret sanitizer
│
├── docs/                              # Master Specifications, Runbooks & Research
│   ├── analytical_frameworks_and_epistemology/ # Master Compendium: Geopolitics, Quant, Economic Dynamics & Epistemology
│   │   ├── INDEX.md                   # Master taxonomy & filter stacking guide
│   │   ├── 01_GEOPOLITICAL_AND_MACRO_COMMODITY_ANALYSIS.md
│   │   ├── 02_QUANTITATIVE_STATISTICAL_AND_CHAOS_MODELS.md
│   │   ├── 03_ECONOMIC_SYSTEMS_AND_INPUT_OUTPUT_MODELS.md
│   │   ├── 04_STRATEGIC_INTELLIGENCE_AND_ADVERSARIAL_FRAMEWORKS.md
│   │   ├── 05_BEHAVIORAL_COGNITIVE_AND_PSYCHOLOGICAL_ANALYSIS.md
│   │   ├── 06_EPISTEMOLOGY_ONTOLOGY_AND_PHILOSOPHICAL_FOUNDATIONS.md
│   │   ├── 07_SPECIALIZED_DOMAIN_AND_NICHE_ANALYTICAL_FRAMEWORKS.md
│   │   └── bma_causal_autonomous_system/ # Dedicated Deep-Dive: BMA, Causal Discovery & Meta-Agent
│   │       ├── INDEX.md
│   │       ├── 01_BMA_AND_CAUSAL_DISCOVERY_ARCHITECTURE.md
│   │       ├── 02_THE_ANALYSIS_PYRAMID_AND_MACRO_REGIMES.md
│   │       ├── 03_ADVANCED_QUANT_ECONOMETRICS_AND_MICROSTRUCTURE.md
│   │       ├── 04_MACHINE_LEARNING_NLP_AND_ALTERNATIVE_DATA.md
│   │       ├── 05_RIGOROUS_BACKTESTING_SIZING_AND_SURVIVAL.md
│   │       ├── 06_META_AGENT_ORCHESTRATION_AND_DECISION_FUSION.md
│   │       ├── 07_OMNI_GLOBAL_CASCADES_AND_CONTINUOUS_LEARNING.md
│   │       └── geopolitical_predictive_and_sanctions_intelligence/ # Predictive Foresight, COMPASS & Sanctions Forensics
│   │           ├── INDEX.md
│   │           ├── 01_PREDICTIVE_ANALYSIS_AND_EXTRAPOLATION_MATHEMATICS.md
│   │           ├── 02_GEOPOLITICAL_MACRO_FORESIGHT_AND_SCENARIO_PLANNING.md
│   │           ├── 03_MULTI_DISCIPLINARY_DOMAIN_KNOWLEDGE_COMPENDIUM.md
│   │           ├── 04_ECONOMIC_SANCTIONS_FINANCIAL_PLUMBING_AND_EVASION_FORENSICS.md
│   │           ├── 05_CASE_STUDY_MATCHING_GRAPH_COGNITION_AND_PATTERN_RECOGNITION.md
│   │           └── 06_GLOBAL_MACRO_RATIOS_SYNTHETIC_SPREADS_AND_AUTONOMOUS_LEARNING.md
│   ├── QUANT_RESEARCH_AND_SYSTEM_FOUNDATIONS.md # Quantitative foundations, risk, HMM, simulation & meta-learning
│   ├── TRADING_SYSTEM_MASTER_TODO.md  # Master roadmap & completion tracker
│   ├── TRADING_SYSTEM_ROADMAP.md      # Strategic system roadmap
│   ├── ARCHITECTURE_COMPLETENESS.md   # Architectural completeness audit
│   ├── INCIDENT_RUNBOOKS.md           # Emergency operations & troubleshooting
│   ├── TRADING_MEMORY_SPEC.md         # Multi-tier memory architecture spec
│   ├── INSTALLATION.md                # Environment setup guide
│   └── USAGE_EXAMPLES.md              # CLI & programmatic usage examples
│
├── artifacts/                         # Research data, reports & sample feeds
├── gui/                               # React + Vite Web Application
├── models/                            # Kronos model weights & tokenizers
└── tests/                             # 154 test suites (730+ unit & integration tests)
```

---

## 🚀 Quick Start

### 1. Verification & Readiness Checks
```powershell
# Run the complete test suite (148 test files, 700+ tests)
pytest -q

# Verify journal integrity across all 14 audit rules
python mt5_trader.py journal integrity

# Inspect comprehensive system readiness status
python mt5_trader.py journal readiness-status
```

### 2. Paper Trading Simulation
```powershell
# Run a single research cycle in paper mode
python mt5_trader.py trade --symbol EURUSD --timeframe M15 --paper

# Run interactive guided menu
python mt5_trader.py
```

### 3. Launching Dashboards
```powershell
# Launch the desktop GUI
python ui/gui_launcher.py

# Launch the single canonical TUI — the OpenTUI command deck (tui_opentui/)
python mt5_trader.py deck

# ...or attach the deck while a trading loop runs (mirrors state via gui_api :8000)
python mt5_trader.py llm-trade --symbol EURUSD --timeframe M15 --paper --tui --api-sync http://127.0.0.1:8000/api/internal/update
```

---

## 🛡️ Safety & Architecture Highlights

1. **Deterministic Risk Protection**: Every order must pass through the fail-closed [`DeterministicValidator`](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/core/trading_safety.py), enforcing mandatory stop-losses, maximum 1% risk-per-trade, and portfolio drawdown caps.
2. **Causal Agreement Gate**: Trade setups are cross-checked with the 134-rule macroeconomic dynamics catalog and causal DAG before approval.
3. **Multi-Tier SQLite Memory**: Local L0–L3 memory stores execution traces, validated scenario facts, and distilled operational principles without cloud lock-in.
4. **Broker Deal Reconciliation**: Automated end-of-day audit compares MT5 terminal deals against the local SQLite journal, detecting any slippage, out-of-band trades, or missing executions.