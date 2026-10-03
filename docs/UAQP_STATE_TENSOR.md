# UAQP State Tensor Schema — Source of Truth

> **Version:** 1.0 | **Date:** 2026-08-31
> **Purpose:** Map every field in the Universal Autonomous Quant Protocol (UAQP)
> state tensor **Γ = (A, E, S, Φ, Θ, T)** to its actual storage implementation
> in this codebase. This is the single source of truth for how state flows
> through the autonomous trading system.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [State Tensor Fields](#2-state-tensor-fields)
3. [Agent Registry](#3-agent-registry)
4. [Edge Algebra](#4-edge-algebra)
5. [Storage Map](#5-storage-map)
6. [Data Flow Diagrams](#6-data-flow-diagrams)
7. [Safety Architecture](#7-safety-architecture)
8. [Thresholds and Constants](#8-thresholds-and-constants)

---

## 1. System Overview

The deployed system is a pair **Γ_deployed = (Γ, Ψ)** where:

- **Γ** = the trading graph (agents, edges, state, transformations, orchestrator, triggers)
- **Ψ** = the External Guardian (reads broker API directly, independent of Γ)

### Core Principle

> Every agent is stateless. The graph is stateful.
> An agent wakes, reads S, transforms a subset, writes back, and terminates.

---

## 2. State Tensor Fields

### S.H — Hypothesis Space

| Property | Value |
|----------|-------|
| **Type** | `Set[AlphaCandidate]` |
| **Populated By** | α (Alpha Generator) |
| **Storage** | `core/alpha_discovery.py:AlphaCandidate` dataclass → in-memory list |
| **Persistence** | `alpha_discovery_state.json` (serialized candidates + IC history) |
| **Schema** | Each h ∈ S.H = (thesis, universe, expected_edge, holding_horizon, rationale, source_confidence) |
| **Lifecycle** | PENDING → VALIDATED → REJECTED |

**Fields:**

```python
@dataclass
class AlphaCandidate:
    expression: str           # thesis: e.g. "rank(ts_delta(close, 10))"
    universe: str             # e.g. "EURUSD,M15"
    expected_edge: str        # e.g. "momentum_reversion"
    holding_horizon: str      # e.g. "M15"
    rationale: str            # human-readable explanation
    source_confidence: float  # 0-1
    ic: float = 0.0          # information coefficient
    ic_ir: float = 0.0       # IC information ratio
    category: str = ""        # momentum, mean_reversion, volatility, etc.
    status: str = "PENDING"   # PENDING, VALIDATED, REJECTED
```

**Code locations:**
- `core/alpha_discovery.py:239` — AlphaCandidate dataclass
- `core/alpha_discovery.py:7` — run_discovery_cycle() generates candidates
- `alpha_discovery_state.json` — persistence

---

### S.D — Data Corpus

| Property | Value |
|----------|-------|
| **Type** | `Dict[str, DataFrame]` |
| **Populated By** | δ (Data Steward) |
| **Storage** | `data/market_data_provider.py:fetch_data()` → `toon` dict |
| **Persistence** | `trading_state.db` (market_ticks table) + API caches |
| **Schema** | toon["ohlc"] = List[Dict] with open/high/low/close/volume/timestamp |
| **Quality** | `core/trading_safety.py:DataQualityGate` validates freshness |

**The `toon` dictionary** is the primary data transport object:

```python
toon = {
    "symbol": "EURUSD",
    "timeframe": "M15",
    "ohlc": [{"open": 1.09, "high": 1.10, "low": 1.08, "close": 1.095, "volume": 1500, "time": "..."}],
    "technical_analysis": {  # computed indicators
        "rsi_14": 45.2,
        "macd_line": 0.001,
        "macd_signal": 0.0008,
        "macd_hist": 0.0002,
        "hma_21": 1.092,
        "adx_14": 28.5,
        "atr_14": 0.0035,
        "ema_alignment": "BULLISH",
    },
    "bocpd": {"changepoints": [45, 120], "hazard": 0.02},
    "fractional_diff": {"stationary": True, "adf_t_stat": -3.2},
    "kalman": {"beta_t": 0.85, "spread_z": 1.2, "ou_halflife": 25},
    "alpha_ensemble": {"composite_score": 1.2, "weights": {"alpha_001": 0.3, ...}},
    "regime_allocation": {"regime": {"primary_regime": "LOW_VOL_TREND", ...}},
    "copula_advisory": {"tail_risk_level": "NORMAL"},
    "zero_lag_macro": {"net_liquidity_bias": 0.1},
    "meta_loop": {"status": "COMPLETED", "patterns_found": 2},
    # ... 50+ fields
}
```

**Code locations:**
- `data/market_data_provider.py:839` — fetch_data() builds toon
- `data/market_data_provider.py:1232` — toon assembly
- `core/trading_safety.py:62` — JournalDB with market_ticks table
- `core/trading_safety.py:150` — market_ticks table schema

**Database tables:**

```sql
CREATE TABLE IF NOT EXISTS market_ticks (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    tick_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);
```

---

### S.F — Signal Manifest

| Property | Value |
|-----------------|-------|
| **Type** | `Dict[str, float]` (alpha scores per asset per time) |
| **Populated By** | σ (Signal Engineer) |
| **Storage** | `core/alpha_ensemble.py:combine_alphas()` → in-memory |
| **Persistence** | `ensemble_weights.json` (IC weights + composite) |
| **Schema** | Σ: (asset, time) → score ∈ ℝ |
| **Orthogonalization** | `core/signal_orthogonalizer.py` purifies signals against market factors |

**Signal pipeline:**

```
Raw OHLCV
    ↓
Alpha Factor Computation (core/alpha_factors.py: 23 alphas)
    ├── ts_rank, delta, rank, corr, volume primitives
    ↓
Signal Orthogonalization (core/signal_orthogonalizer.py)
    ├── OLS regression against market factors
    ├── PCA projection
    └── Gram-Schmidt sequential
    ↓
IC Weighting (core/alpha_ensemble.py:compute_ic_weights)
    ├── Rolling IC computation
    ├── Shrinkage estimation
    └── Regime adjustment
    ↓
Composite Score (combine_alphas → toon["alpha_ensemble"]["composite_score"])
```

**Code locations:**
- `core/alpha_ensemble.py:432` — combine_alphas() main entry
- `core/alpha_ensemble.py:387` — compute_ic_weights()
- `core/signal_orthogonalizer.py:404` — orthogonalize_signals()
- `core/alpha_factors.py:1` — 23 alpha factor implementations
- `ensemble_weights.json` — persistence

---

### S.B — Backtest Results

| Property | Value |
|----------|-------|
| **Type** | `Dict[str, BacktestMetrics]` |
| **Populated By** | β (Validator) |
| **Storage** | Multiple modules (CPCV, walk-forward, overfit) |
| **Persistence** | `walk_forward_state.json`, `backtest_report.json`, artifacts/ |
| **Schema** | Π = (CAGR, Sharpe, max_drawdown, win_rate, turnover, overfitting_index, walk_forward_pass) |

**Validation pipeline:**

```
Signal Σ + Execution Parameters
    ↓
CPCV Backtester (core/cpcv_backtester.py)
    ├── Combinatorially Purged Cross-Validation
    ├── Probability of Backtest Overfitting (PBO)
    └── Deflated Sharpe Ratio (DSR)
    ↓
Walk-Forward Validator (core/walk_forward_validator.py)
    ├── Rolling window optimization
    ├── Parameter stability analysis
    └── IC decay detection
    ↓
Overfit Detector (core/overfit_detector.py)
    ├── IS vs OOS divergence
    ├── Stepwise fitting detection
    └── Strategy overlap analysis
    ↓
Gate Function G_β:
    DSR < 0.95 → FAIL_NOT_SIGNIFICANT
    PBO > 0.25 → FAIL_OVERFIT
    Sharpe < 0.5 → FAIL_RESEARCH
    MaxDD > 0.15 → FAIL_RISK
    Walk-forward = FALSE → FAIL_ROBUSTNESS
```

**Code locations:**
- `core/cpcv_backtester.py:35` — CPCV results
- `core/walk_forward_validator.py:257` — walk-forward results
- `core/overfit_detector.py:33` — OverfitResult dataclass
- `core/metrics.py:161` — calculate_deflated_sharpe_ratio()
- `core/overfit_detector.py:82` — deflated_sharpe_test()
- `walk_forward_state.json` — persistence

---

### S.R — Risk State

| Property | Value |
|----------|-------|
| **Type** | `RiskState` control tensor |
| **Populated By** | ρ (Risk Guardian) |
| **Storage** | `core/risk_manager.py:RiskManager` + `core/trading_safety.py:JournalDB` |
| **Persistence** | `trading_state.db` (state table) |
| **Schema** | (approval_status, position_sizes, var_limit_status, kill_switch_state, sizing_model) |

**Risk components:**

```
RiskManager (core/risk_manager.py)
    ├── Position sizing (Kelly-derived, fixed fractional, vol targeting)
    ├── Heat budget management
    ├── Circuit breaker (weekly drawdown limit)
    ├── Correlation risk (copula-based)
    └── VaR limit enforcement

TradingSafety (core/trading_safety.py)
    ├── TradeProposal validation
    ├── Rate limiting (trades/hour)
    ├── Data quality gate
    ├── Duplicate prevention
    └── Emergency flatten capability
```

**Code locations:**
- `core/risk_manager.py:15` — RiskManager class
- `core/risk_manager.py:558` — set_heat_budget_r()
- `core/risk_manager.py:261` — check_weekly_circuit_breaker()
- `core/trading_safety.py:62` — JournalDB (SQLite)
- `trading_state.db:state` table — key-value persistence

**Database tables:**

```sql
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

### S.P — Portfolio State

| Property | Value |
|----------|-------|
| **Type** | `Dict[str, Any]` (positions, cash, margin, fills) |
| **Populated By** | ε (Execution Engine) |
| **Storage** | `core/trade_manager.py` + `core/order_execution.py` + MT5 API |
| **Persistence** | `trading_state.db` (trades, position_observations, trade_lifecycle_events) |
| **Schema** | (positions, cash, margin, fills, slippage_log, live_pnl_series) |

**Portfolio components:**

```
MT5 Broker API
    ├── mt5.positions_get() — live positions
    ├── mt5.account_info() — equity, margin, free margin
    └── mt5.orders_get() — pending orders

trade_manager.py
    ├── monitor_positions() — trailing stops, breakeven, partial closes
    ├── emergency_flatten() — risk-triggered liquidation
    └── journal tracking — trade lifecycle events

order_execution.py
    ├── place_market_order() — MARKET_IOC execution
    ├── place_pending_order() — LIMIT_MAKER execution
    └── emergency_market_exit() — flatten all positions
```

**Code locations:**
- `core/trade_manager.py:109` — monitor_positions()
- `core/order_execution.py:335` — place_market_order()
- `core/order_execution.py:444` — emergency_market_exit()
- `trading_state.db:trades` — trade records
- `trading_state.db:position_observations` — unrealized PnL snapshots

**Database tables:**

```sql
CREATE TABLE IF NOT EXISTS trades (
    ticket INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    volume REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    magic INTEGER,
    strategy TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    pnl REAL,
    risk_amount REAL
);

CREATE TABLE IF NOT EXISTS position_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER NOT NULL,
    unrealized_pnl REAL,
    current_price REAL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
```

---

### S.M — Market Regime

| Property | Value |
|----------|-------|
| **Type** | `RegimeState` classification |
| **Populated By** | μ (Monitor) |
| **Storage** | `core/hierarchical_portfolio.py:RegimeState` + `core/bocpd.py` |
| **Persistence** | In-memory (recomputed each cycle) |
| **Schema** | (regime_label, volatility_percentile, correlation_regime, liquidity_index) |

**Regime detection stack:**

```
BOCPD (core/bocpd.py)
    ├── Bayesian Online Changepoint Detection
    ├── Hazard function H(tau) = 1/lambda
    └── Produces changepoints + run-length posteriors

RegimeDetectionEngine (core/hierarchical_portfolio.py:60)
    ├── Hurst exponent (trend vs mean-reversion)
    ├── ADX (trend strength)
    ├── ATR% (volatility regime)
    └── Produces RegimeState with 4 regimes:
        ├── LOW_VOL_TREND
        ├── LOW_VOL_RANGE
        ├── HIGH_VOL_TREND
        └── HIGH_VOL_CRISIS

RegimeDurationTracker (core/regime_duration_tracker.py:61)
    ├── Tracks dwell time in current regime
    ├── Detects regime transitions
    └── Produces RegimeTransition records

MetaAdaptiveEngine (core/meta_adaptive_engine.py)
    ├── Zero-shot regime switching (FinPFN/ReCAP)
    ├── PolicyLibrary stores optimized params per regime
    └── RegimeGate blends policies based on regime probabilities
```

**Code locations:**
- `core/hierarchical_portfolio.py:31` — RegimeState dataclass
- `core/hierarchical_portfolio.py:60` — RegimeDetectionEngine
- `core/bocpd.py:1` — BOCPD algorithm
- `core/regime_duration_tracker.py:37` — RegimeTransition
- `core/regime_duration_tracker.py:61` — RegimeDurationTracker
- `core/meta_adaptive_engine.py` — zero-shot switching

---

### S.Ω — System Health

| Property | Value |
|----------|-------|
| **Type** | `Dict[str, Any]` diagnostic |
| **Populated By** | μ (Monitor) |
| **Storage** | `core/alpha_decay_monitor.py:AlphaHealth` + execution safety |
| **Persistence** | `alpha_decay_state.json`, `auto_quant_tools_state.json` |
| **Schema** | (alpha_decay_index, tracking_error, system_uptime, recommended_action) |

**Health components:**

```
AlphaDecayMonitor (core/alpha_decay_monitor.py:131)
    ├── IC tracking (raw + purified via orthogonalization)
    ├── Decay detection (HEALTHY/DEGRADED/DEAD/REVERSED)
    ├── Raw vs Purified IC comparison
    └── Alpha quality report

ExecutionSafetySupervisor (core/execution_safety_supervisor.py)
    ├── Circuit breaker monitoring
    ├── CPCV escalation tracking
    └── Position scale recommendations

OverfitDetector (core/overfit_detector.py)
    ├── IS/OOS divergence
    ├── Deflated Sharpe
    └── Overfit risk grade (A/B/C/D/F)

ExternalGuardian (core/external_guardian.py)
    ├── Independent broker-API monitoring
    ├── Drawdown thresholds (7%/12%/20%)
    └── Heartbeat monitoring
```

**Code locations:**
- `core/alpha_decay_monitor.py:52` — AlphaHealth dataclass
- `core/alpha_decay_monitor.py:131` — AlphaDecayMonitor class
- `core/execution_safety_supervisor.py` — CPCV escalation
- `core/external_guardian.py:57` — GuardianThresholds
- `alpha_decay_state.json` — persistence
- `auto_quant_tools_state.json` — tool run history

---

### S.Q — Orchestrator Queue

| Property | Value |
|----------|-------|
| **Type** | `TaskQueue` priority queue |
| **Populated By** | ω (Orchestrator) |
| **Storage** | `core/task_queue.py:TaskQueue` |
| **Persistence** | In-memory (reconstructed from DEFAULT_INTERVALS each cycle) |
| **Schema** | (pending_tasks, blocked_tasks, human_review_flags) |

**Priority levels:**

| Priority | Value | Tools | When |
|----------|-------|-------|------|
| CRITICAL | 0 | Kill switch, circuit breaker | Safety-critical |
| URGENT | 1 | BOCPD, crash_detector | Regime shift |
| HIGH | 2 | CPCV, Kalman, walk_forward | Validation |
| NORMAL | 3 | TCA, alpha_discovery, EWC | Scheduled |
| LOW | 4 | cross_market, evolutionary | Benchmarks |
| BACKGROUND | 5 | finrl_retrain, meta_loop | Slow retrain |

**Task lifecycle:**

```
PENDING → READY → RUNNING → COMPLETED/FAILED
                          ↘ RETRY (if retries remain)

Dependency resolution:
    BOCPD → meta_adaptive (regime must run first)
    Kalman → alpha_decay (trend must be known)
    CPCV → walk_forward (backtest must validate first)

Human-review gate:
    If ANY task needs review → ALL autonomous routing paused
```

**Code locations:**
- `core/task_queue.py:75` — Task dataclass
- `core/task_queue.py:225` — TaskQueue class
- `core/auto_quant_tools.py:40` — DEFAULT_INTERVALS
- `core/auto_quant_tools.py:426` — update() method

---

### S.Ξ — Trial Ledger

| Property | Value |
|----------|-------|
| **Type** | `(n_trials, trial_sharpe_distribution)` |
| **Populated By** | β (Validator) — append-only |
| **Storage** | `core/overfit_detector.py` + `core/metrics.py` |
| **Persistence** | Computed on-demand from CPCV results |
| **Schema** | Monotonic, append-only — no agent may decrement or reset n_trials |

**The trial counter is the load-bearing constraint.**

Every time a hypothesis is tested, n_trials increments. The Deflated Sharpe Ratio uses n_trials to correct for multiple testing:

```
DSR = DeflatedSharpe(
    observed = S.B.Sharpe,
    n_trials = S.Ξ.n_trials,     # ← this never decreases
    skew, kurtosis, n_obs,
    var = Var(Ξ.trial_sharpe_distribution)
)

If DSR < 0.95 → FAIL_NOT_SIGNIFICANT
```

**Code locations:**
- `core/metrics.py:161` — calculate_deflated_sharpe_ratio(num_trials=...)
- `core/overfit_detector.py:82` — deflated_sharpe_test(n_trials=...)
- `core/overfit_detector.py:57` — MinBacktestResult.n_trials

---

## 3. Agent Registry

| Agent | Symbol | Module | Reads | Writes | Trigger |
|-------|--------|--------|-------|--------|---------|
| **Alpha Generator** | α | `core/alpha_discovery.py` | S.Ω, S.M, I(t) | S.H | Nightly / decay > threshold |
| **Data Steward** | δ | `data/market_data_provider.py` | S.H, U, T | S.D | Per-cycle |
| **Signal Engineer** | σ | `core/alpha_ensemble.py` + `core/signal_orthogonalizer.py` | S.D[h], F | S.F | Per-cycle |
| **Validator** | β | `core/cpcv_backtester.py` + `core/walk_forward_validator.py` + `core/overfit_detector.py` | S.F[h], Π | S.B | When σ produces new signal |
| **Risk Guardian** | ρ | `core/risk_manager.py` + `core/trading_safety.py` | S.B[h], S.P, S.M | S.R | When β passes |
| **Execution Engine** | ε | `core/order_execution.py` + `core/trade_manager.py` | S.R, market | S.P | When ρ approves |
| **Monitor** | μ | `core/alpha_decay_monitor.py` | S.P, S.B, S.M | S.Ω | Per-cycle |
| **Orchestrator** | ω | `intelligence/llm_orchestrator.py` | S(t) full | S.Q | Priority-based |
| **Guardian** | Ψ | `core/external_guardian.py` | B(t) broker API | ARMED/TRIPPED | Independent Δt_guardian |
| **Code Safety** | ζ | `core/code_safety_agent.py` | generated code | APPROVED/REJECTED | When σ generates code |
| **Compliance Audit** | κ | `core/compliance_audit.py` | every transition | immutable log | Unconditional |
| **Meta-Loop** | — | `core/meta_loop.py` | S.B, S.Ω, outcomes | Δpriors | Monthly |

---

## 4. Edge Algebra

| Origin | Destination | Condition | Meaning |
|--------|-------------|-----------|---------|
| α | δ | h ∈ S.H ∧ h.data_required | New hypothesis needs data |
| δ | σ | S.D[h].quality = VALID | Clean data ready for signal |
| δ | α | S.D[h].coverage = FAIL | Insufficient data |
| σ | ζ | Signal code generated | Route through safety check |
| ζ | β | approved | Code cleared safety |
| ζ | human | code touches ε logic | Mandatory sign-off |
| σ | β | S.F[h].diagnostic ≠ NOISE | Signal has predictive structure |
| σ | α | S.F[h].diagnostic = NOISE | Signal is noise |
| β | ρ | G_β(S.B) = PASS | Strategy validated |
| β | α | G_β(S.B) = FAIL_RESEARCH | Thesis invalid |
| β | σ | G_β(S.B) ∈ {FAIL_OVERFIT, FAIL_ROBUSTNESS} | Modify and retest |
| ρ | ε | S.R.approval = APPROVED | Trade |
| ρ | σ | S.R.approval = MODIFIED | Resize/rebalance |
| ρ | ω | S.R.kill_switch = TRUE | Halt system |
| ε | μ | Fills confirmed | Begin monitoring |
| μ | ω | S.Ω.action = CONTINUE | Idle |
| μ | σ | S.Ω.action = TWEAK | Partial retrain |
| μ | α | S.Ω.action = RETRAIN | Full research cycle |
| ω | Any | Scheduled trigger T fires | Time-based activation |
| ψ → Ψ | Ψ → ε | TRIPPED | Override — flatten, halt, page human |

---

## 5. Storage Map

### SQLite Databases

| Database | Tables | Module | Purpose |
|----------|--------|--------|---------|
| `trading_state.db` | `state`, `proposals`, `trades`, `setup_instances`, `setup_labels`, `manual_activity`, `trade_lifecycle_events`, `position_observations`, `market_ticks` | `core/trading_safety.py` | Authoritative trade journal |
| `compliance_audit.db` | `corpus` (hash-chained) | `core/compliance_audit.py` | Immutable audit log (κ) |
| `meta_corpus.db` | `corpus`, `patterns`, `proposals` | `core/meta_loop.py` | Meta-learning corpus (C_meta) |

### JSON State Files

| File | Module | Contents |
|------|--------|----------|
| `alpha_discovery_state.json` | `core/alpha_discovery.py` | Candidate history, IC records |
| `alpha_decay_state.json` | `core/alpha_decay_monitor.py` | Alpha health status, IC history |
| `auto_quant_tools_state.json` | `core/auto_quant_tools.py` | Tool run history, alerts |
| `walk_forward_state.json` | `core/walk_forward_validator.py` | Walk-forward results, IC history |
| `meta_loop_state.json` | `core/meta_loop.py` | Analysis history, prior adjustments |
| `causal_filter_state.json` | `core/causal_feature_filter.py` | Feature importance rankings |
| `ensemble_weights.json` | `core/alpha_ensemble.py` | Current IC weights, composite |
| `artifacts/ensemble_cpcv.json` | `core/cpcv_backtester.py` | CPCV validation results |
| `artifacts/alpha_stability_report.json` | `research/alpha_ensemble_stability.py` | Parameter stability |
| `ζ_code_versions.json` | `core/code_safety_agent.py` | Code version tracking |

### In-Memory State (Recomputed Each Cycle)

| Object | Module | Recomputed By |
|--------|--------|---------------|
| `toon` dict | `data/market_data_provider.py` | fetch_data() per cycle |
| `RegimeState` | `core/hierarchical_portfolio.py` | RegimeDetectionEngine |
| `AlphaHealth` dict | `core/alpha_decay_monitor.py` | AlphaDecayMonitor.update() |
| `TaskQueue` | `core/task_queue.py` | Reconstructed from DEFAULT_INTERVALS |
| `CodeSafetyAgent._reviews` | `core/code_safety_agent.py` | CodeSafetyAgent.review() |

---

## 6. Data Flow Diagrams

### Per-Cycle Flow (Γ)

```
mt5_trader.py main loop
    │
    ├── 1. S.Q: get_ready_tasks(cycle_num) → scheduled tools
    │
    ├── 2. data/market_data_provider.py: fetch_data()
    │       → builds toon (S.D + S.F + S.M + S.Ω)
    │
    ├── 3. intelligence/llm_orchestrator.py: execute_cycle()
    │       │
    │       ├── κ: log LLM_CALL
    │       │
    │       ├── LLM reads toon, produces proposal
    │       │
    │       ├── trading_safety: validate proposal
    │       │   ├── DataQualityGate (freshness)
    │       │   ├── TradingConstraintEngine (14 rules)
    │       │   ├── OverfitDetector (gate)
    │       │   └── ζ: code safety (if generated code)
    │       │
    │       ├── κ: log LLM_DECISION
    │       │
    │       └── order_execution: place order
    │           └── κ: log ORDER_FILLED/REJECTED
    │
    ├── 4. trade_manager: monitor_positions()
    │       → trailing stops, breakeven, partial closes
    │
    ├── 5. auto_quant_tools: update()
    │       → BOCPD, alpha_decay, CPCV, meta_loop, etc.
    │
    ├── 6. alpha_decay_monitor: update()
    │       → S.Ω (system health)
    │
    └── 7. S.Q: update_cycle() → re-queue recurring tasks
```

### Safety Layer Flow

```
Γ (trading graph)                    Ψ (Guardian)
    │                                     │
    ├── reads own state S                 ├── reads B(t) from broker API
    ├── ρ judges risk                     ├── independent thresholds
    ├── μ monitors health                 ├── ARMED → normal
    │                                     ├── TRIPPED → flatten all
    └── If ρ fails...                     └── Unaffected by Γ failures
```

---

## 7. Safety Architecture

### Kill Switch Hierarchy

| Level | Trigger | Source | Action | Recovery |
|-------|---------|--------|--------|----------|
| **Yellow** | VaR limit breached | ρ (in-graph) | Reduce size 50% | Auto after 24h |
| **Orange** | Drawdown > 15% OR δ_α > 0.6 | ρ (in-graph) | Halt entries, allow exits | Human acknowledgment |
| **Red** | Drawdown > 20% OR data corruption | **Ψ (Guardian)** | Liquidate all, page human | Manual restart only |

**Critical design:** Red-tier authority lives in Ψ, NOT in ρ. Ψ reads the broker API directly — it never reads Γ's own state. This means even if Γ's state is corrupted, Ψ still works.

### Human-in-the-Loop Gates (Non-Negotiable)

1. Capital deployment > 50% into single strategy
2. First live trade of any new hypothesis class
3. Regime transition detected by μ
4. Any Orange or Red kill switch activation
5. Code touching ε domain (ζ gate)

---

## 8. Thresholds and Constants

### Guardian Thresholds (Ψ) — Hardcoded, Never LLM-Touched

| Threshold | Value | Meaning |
|-----------|-------|---------|
| `τ_red` | 20% | Daily drawdown → TRIPPED, flatten all |
| `τ_orange` | 12% | Daily drawdown → TRIPPED, halt entries |
| `τ_yellow` | 7% | Daily drawdown → reduce size 50% |
| `τ_leverage` | 5.0 | Gross leverage → TRIPPED |
| `τ_heartbeat` | 300s | Γ heartbeat timeout → TRIPPED |

### Validation Gate Thresholds (G_β)

| Threshold | Value | Meaning |
|-----------|-------|---------|
| `τ_dsr` | 0.95 | Deflated Sharpe minimum |
| `τ_pbo` | 0.25 | PBO warning |
| `τ_sharpe` | 0.5 | Minimum Sharpe |
| `τ_dd` | 0.15 | Maximum drawdown |
| `τ_overfit` | 0.3 | Overfit risk grade C |

### Decay Detection Thresholds (μ)

| Threshold | Value | Meaning |
|-----------|-------|---------|
| `τ_decay_mild` | 0.3 | IC correlation decline → TWEAK |
| `τ_decay_critical` | 0.6 | IC correlation decline → RETRAIN |
| `τ_tracking_error` | 0.05 | 5-day tracking error → REGIME_CHECK |

### CPCV Escalation Thresholds

| Threshold | Value | Meaning |
|-----------|-------|---------|
| `CPCV_ESCALATION_THRESHOLD` | 3 | 3+ consecutive degraded → CRITICAL |
| `CPCV_PBO_WARNING` | 0.25 | PBO above = degraded |
| `CPCV_DSR_WARNING` | 0.70 | DSR below = degraded |
| `CPCV_PBO_CRITICAL` | 0.35 | PBO above = critically degraded |
| `CPCV_DSR_CRITICAL` | 0.50 | DSR below = critically degraded |

### Position Scale from Escalation

| Streak | Scale | Meaning |
|--------|-------|---------|
| < 3 | 1.0 | No reduction |
| 3-4 | 0.5 | Half size |
| 5-6 | 0.25 | Quarter size |
| 7+ | 0.0 | Cash only |

---

## Appendix: Module Quick Reference

| Module | Lines | Purpose |
|--------|-------|---------|
| `core/alpha_discovery.py` | ~400 | α agent — hypothesis generation |
| `core/alpha_factors.py` | ~600 | 23 alpha factor implementations |
| `core/alpha_ensemble.py` | ~600 | σ agent — IC-weighted ensemble |
| `core/alpha_decay_monitor.py` | ~600 | μ agent — IC decay detection |
| `core/signal_orthogonalizer.py` | ~640 | ψ — signal purification |
| `core/cpcv_backtester.py` | ~200 | β agent — CPCV validation |
| `core/walk_forward_validator.py` | ~450 | β agent — walk-forward |
| `core/overfit_detector.py` | ~650 | β agent — overfit detection |
| `core/risk_manager.py` | ~700 | ρ agent — risk management |
| `core/trading_safety.py` | ~970 | Safety primitives + journal DB |
| `core/order_execution.py` | ~500 | ε agent — order execution |
| `core/trade_manager.py` | ~300 | ε agent — position management |
| `core/hierarchical_portfolio.py` | ~350 | Regime detection + allocation |
| `core/bocpd.py` | ~100 | Bayesian changepoint detection |
| `core/regime_duration_tracker.py` | ~200 | Regime dwell tracking |
| `core/meta_adaptive_engine.py` | ~300 | Zero-shot regime switching |
| `core/task_queue.py` | ~726 | S.Q priority queue |
| `core/compliance_audit.py` | ~832 | κ audit agent |
| `core/code_safety_agent.py` | ~852 | ζ code safety agent |
| `core/meta_loop.py` | ~949 | Loop 4 meta-learning |
| `core/external_guardian.py` | ~400 | Ψ guardian |
| `core/execution_safety_supervisor.py` | ~300 | CPCV escalation |
| `intelligence/llm_orchestrator.py` | ~2000 | ω orchestrator + LLM |
| `data/market_data_provider.py` | ~1300 | δ data steward + toon builder |
| `mt5_trader.py` | ~3400 | Main loop + UI |
| `ui/compliance_dashboard.py` | ~360 | Compliance dashboard |
