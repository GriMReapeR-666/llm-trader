# ARCHITECTURE — Autonomous LLM Trader System

> **Companion to:** [SOURCE_OF_TRUTH.md](./SOURCE_OF_TRUTH.md)
> **Last verified:** 2026-08-28

This document describes the system architecture, data flow, and component interactions with visual diagrams.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Trading Cycle Flow](#2-trading-cycle-flow)
3. [Alpha Factor Pipeline](#3-alpha-factor-pipeline)
4. [Decision Engine Flow](#4-decision-engine-flow)
5. [Safety & Risk Architecture](#5-safety--risk-architecture)
6. [Data Flow Diagrams](#6-data-flow-diagrams)
7. [State Management](#7-state-management)
8. [LLM Routing Architecture](#8-llm-routing-architecture)
9. [Operational Architecture](#9-operational-architecture)
10. [Deployment Modes](#10-deployment-modes)

---

## 1. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        MT5 TRADER (mt5_trader.py)                       │
│                        Main CLI + Unified Facade                        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
    ┌─────▼─────┐      ┌───────▼───────┐      ┌──────▼──────┐
    │   CLI     │      │  Interactive  │      │  Dashboard  │
    │  Commands │      │     Menu      │      │   Launch    │
    └─────┬─────┘      └───────┬───────┘      └──────┬──────┘
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   LLM ORCHESTRATOR    │
                    │  (llm_orchestrator.py)│
                    │   5,165 lines         │
                    └───────────┬───────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
  ┌─────▼─────┐          ┌─────▼─────┐          ┌──────▼──────┐
  │   DATA    │          │  INTELLI- │          │  EXECUTION  │
  │   LAYER   │          │  GENCE    │          │    LAYER    │
  │           │          │  LAYER    │          │             │
  │ market_   │          │           │          │ trading_    │
  │ data_     │          │ macro_    │          │ safety.py   │
  │ provider  │          │ graph     │          │             │
  │           │          │           │          │ risk_       │
  │ llm_      │          │ causal_   │          │ manager     │
  │ router    │          │ engine    │          │             │
  │           │          │           │          │ order_      │
  │ data_     │          │ memory    │          │ execution   │
  │ quality   │          │ system    │          │             │
  └─────┬─────┘          └───────────┘          │ trade_      │
        │                                        │ manager     │
  ┌─────▼─────┐                                  │             │
  │   ALPHA   │                                  │ execution_  │
  │  SYSTEM   │                                  │ safety_     │
  │           │                                  │ supervisor  │
  │ factors   │                                  └──────┬──────┘
  │ ensemble  │                                         │
  │ decay     │                                    ┌────▼────┐
  │ rotation  │                                    │   MT5   │
  └───────────┘                                    │ TERMINAL│
                                                   └─────────┘
```

---

## 2. Trading Cycle Flow

The main `llm-trade` command executes this cycle for each iteration:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TRADING CYCLE                                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ 1. CYCLE BRIEF                                                  │ │
│  │    - Check trading session (market hours?)                      │ │
│  │    - Check calendar events (blackout windows?)                  │ │
│  │    - Check account state (balance, equity, drawdown)            │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 2. DATA FETCH (market_data_provider.py)                         │ │
│  │    - Fetch OHLCV from MT5                                       │ │
│  │    - Compute technical indicators (RSI, MACD, EMA, ATR, ADX)   │ │
│  │    - Compute alpha factors (18 active alphas)                   │ │
│  │    - Compute alpha ensemble (IC-weighted composite)             │ │
│  │    - Run alpha decay monitor                                    │ │
│  │    - Run alpha rotation engine                                  │ │
│  │    - Fetch calendar events                                      │ │
│  │    - Fetch macro data (FRED, news)                              │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 3. LLM DECISION (llm_orchestrator.py)                           │ │
│  │    - Build prompt with market context                           │ │
│  │    - Include: price, indicators, alpha factors, ensemble        │ │
│  │    - Include: account state, risk limits, positions             │ │
│  │    - Call LLM via LLMRouter (OpenRouter/Cerebras/NVIDIA)        │ │
│  │    - Parse structured JSON response                             │ │
│  │    - Extract: side, entry, SL, TP, confidence, rationale        │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 4. VALIDATION (trading_safety.py)                               │ │
│  │    - DeterministicValidator: R:R, cooldowns, rate limits        │ │
│  │    - PortfolioRiskArbiter: correlation, heat, concentration     │ │
│  │    - MarginSafetyGate: margin sufficiency                       │ │
│  │    - Session filters: trading hours                             │ │
│  │    - If REJECTED → HOLD (no trade)                              │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 5. EXECUTION (order_execution.py)                               │ │
│  │    - PAPER: Internal simulation (PaperBroker)                   │ │
│  │    - DEMO: MT5 market/pending order (live execution)            │ │
│  │    - LIVE: MT5 order with confirmation gate                     │ │
│  │    - Record proposal + trade in trading_state.db                │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 6. POST-TRADE (trade_manager.py)                                │ │
│  │    - Trailing stop management                                   │ │
│  │    - Breakeven stop at +1R                                      │ │
│  │    - Partial exits (50% at +2R, 25% at +3R)                     │ │
│  │    - Time-based stop (4h stagnation)                            │ │
│  │    - Pyramiding management                                      │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 7. MONITORING (execution_safety_supervisor.py)                  │ │
│  │    - Latency guard (< 250ms)                                    │ │
│  │    - Slippage guard (< 2.5x spread)                             │ │
│  │    - Drawdown circuit breaker                                   │ │
│  │    - Heartbeat watchdog                                         │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 8. MAINTENANCE                                                  │ │
│  │    - Journal audit (14 integrity rules)                         │ │
│  │    - Daily reconciliation (broker vs journal)                   │ │
│  │    - Alert delivery (webhooks)                                  │ │
│  │    - Alpha dashboard auto-generation                            │ │
│  │    - Memory distillation                                        │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ 9. SLEEP (configurable, default 5s)                             │ │
│  │    - Wait for next cycle                                        │ │
│  │    - Check kill switch / loop count                             │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Alpha Factor Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ALPHA FACTOR PIPELINE                              │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ INPUT: OHLCV DataFrame (open, high, low, close, volume)        │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ STEP 1: INDIVIDUAL ALPHA COMPUTATION (alpha_factors.py)         │ │
│  │                                                                  │ │
│  │  Primitives (16):                                               │ │
│  │  ts_rank, ts_argmax, ts_argmin, ts_min, ts_max,                │ │
│  │  delta, delay, rank, scale, decay_linear,                       │ │
│  │  signed_power, log_safe                                         │ │
│  │                                                                  │ │
│  │  Alpha Functions (22):                                          │ │
│  │  alpha_001 → alpha_101 (Quant Platform-style factors)               │ │
│  │                                                                  │ │
│  │  Active Set (18, managed by rotation engine):                   │ │
│  │  #12 #53 #55 #34 #17 #9 #41 #101 #30 #35                      │ │
│  │  #14 #22 #25 #19 #23 #36 #37 #43                               │ │
│  │                                                                  │ │
│  │  Output: DataFrame with alpha_012, alpha_053, ... columns       │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ STEP 2: ENSEMBLE (alpha_ensemble.py)                             │ │
│  │                                                                  │ │
│  │  For each alpha:                                                │ │
│  │    1. Z-score normalize (expanding window)                      │ │
│  │    2. Compute rolling Spearman IC (60-bar window)               │ │
│  │    3. Assess quality: IC_IR, turnover, half-life, stability     │ │
│  │                                                                  │ │
│  │  Weighting (IC_IR-based):                                       │ │
│  │    weight_i = max(IC_IR_i, 0) / Σ max(IC_IR_j, 0)             │ │
│  │    Negative IC_IR → zero weight (bad alpha auto-excluded)       │ │
│  │                                                                  │ │
│  │  Composite:                                                     │ │
│  │    composite_raw = Σ (normalized_alpha_i × weight_i)            │ │
│  │    composite = zscore(composite_raw)                            │ │
│  │                                                                  │ │
│  │  Output: EnsembleResult {                                       │ │
│  │    composite (z-scored series),                                 │ │
│  │    signal: LONG/SHORT/NEUTRAL,                                  │ │
│  │    confidence: HIGH/MEDIUM/LOW,                                 │ │
│  │    weights: {alpha_055: 0.78, alpha_034: 0.22, ...}            │ │
│  │    alpha_qualities: {per-alpha IC, IR, turnover, ...}           │ │
│  │  }                                                              │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ STEP 3: DECAY MONITORING (alpha_decay_monitor.py)               │ │
│  │                                                                  │ │
│  │  For each alpha:                                                │ │
│  │    - Compute rolling IC (60-bar)                                │ │
│  │    - Check: IC < threshold for N consecutive bars?              │ │
│  │    - Status: HEALTHY → DEGRADED → REVERSED → DEAD               │ │
│  │    - Emit alerts to operator outbox                             │ │
│  │                                                                  │ │
│  │  Output: {alpha_012: {status: "HEALTHY", ic: 0.04, ...}, ...}  │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ STEP 4: ROTATION (alpha_rotation.py)                             │ │
│  │                                                                  │ │
│  │  For each DEAD/REVERSED alpha in active set:                    │ │
│  │    - Remove from active set                                     │ │
│  │    - Find best non-active alpha by IC_IR                        │ │
│  │    - Promote candidate (respecting cooldown)                    │ │
│  │                                                                  │ │
│  │  Constraints:                                                   │ │
│  │    - min_alphas = 5 (never drop below)                          │ │
│  │    - max_alphas = 20 (never exceed)                             │ │
│  │    - cooldown = 50 bars (no re-promotion too soon)              │ │
│  │                                                                  │ │
│  │  Output: updated active set for next cycle                      │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ FINAL OUTPUT (to LLM):                                          │ │
│  │                                                                  │ │
│  │  toon["indicators"]["alpha_factors"] = {                        │ │
│  │    "alpha_012": {"current": -0.0002, "mean_20": ..., "std": ..}│ │
│  │    "alpha_053": {"current": 0.277, "mean_20": ..., "std": ..}  │ │
│  │    ...                                                          │ │
│  │  }                                                              │ │
│  │                                                                  │ │
│  │  toon["indicators"]["alpha_ensemble"] = {                       │ │
│  │    "composite_signal": "NEUTRAL",                               │ │
│  │    "composite_strength": 0.92,                                  │ │
│  │    "composite_confidence": "LOW",                               │ │
│  │    "n_alphas_used": 12,                                         │ │
│  │    "overall_ic": 0.0385,                                        │ │
│  │    "weights": {"alpha_055": 0.78, ...},                         │ │
│  │    "top_contributors": [...]                                     │ │
│  │  }                                                              │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Decision Engine Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LLM DECISION ENGINE                               │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ CONTEXT ASSEMBLY                                                │ │
│  │                                                                  │ │
│  │  toon_lite = {                                                  │ │
│  │    "price":        { bid, ask, spread, tick_time },             │ │
│  │    "indicators":   { rsi_14, macd, ema_50, atr_14, adx_14 },   │ │
│  │    "alpha_factors": { alpha_012: ..., alpha_053: ..., ... },    │ │
│  │    "alpha_ensemble": { signal, strength, confidence, weights },  │ │
│  │    "alpha_decay_health": { summary, alerts },                    │ │
│  │    "alpha_rotation": { active_set, recent_events },             │ │
│  │    "account":      { balance, equity, margin_free },            │ │
│  │    "positions":    [{ ticket, symbol, side, profit, ... }],     │ │
│  │    "risk":         { drawdown, daily_loss, heat_r },            │ │
│  │    "calendar":     [{ event, importance, time }],               │ │
│  │    "session":      { name, is_trading_hours },                  │ │
│  │    "macro":        { regime, opportunities },                   │ │
│  │  }                                                              │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ PROMPT CONSTRUCTION                                             │ │
│  │                                                                  │ │
│  │  Section 1: System Identity & Trading Rules                     │ │
│  │    - Role: Professional forex trader                             │ │
│  │    - Risk rules: max 1% per trade, 3% daily stop                │ │
│  │    - R:R minimum: 1.5                                           │ │
│  │                                                                  │ │
│  │  Section 2: Account State                                       │ │
│  │    - Balance, equity, open positions, risk metrics              │ │
│  │                                                                  │ │
│  │  Section 3: Market Context                                      │ │
│  │    - Price data, technical indicators                           │ │
│  │    - Alpha factors (individual signals)                         │ │
│  │    - Alpha ensemble composite signal                            │ │
│  │    - Calendar events, macro context                             │ │
│  │                                                                  │ │
│  │  Section 4: Alpha Signal Interpretation Rules                   │ │
│  │    - When ensemble HIGH + aligns → INCREASE conviction           │ │
│  │    - When ensemble HIGH + contradicts → HALT                     │ │
│  │    - When ensemble LOW → Advisory only                          │ │
│  │    - 3+ alphas agree + RSI/MACD alignment → boost               │ │
│  │                                                                  │ │
│  │  Section 5: Decision Framework                                  │ │
│  │    - Technical rationale                                        │ │
│  │    - Fundamental rationale                                      │ │
│  │    - Risk assessment                                            │ │
│  │    - Ensemble alignment                                         │ │
│  │                                                                  │ │
│  │  Section 6: Output Schema (JSON)                                │ │
│  │    - decision: BUY/SELL/HOLD                                    │ │
│  │    - entry, stop_loss, take_profit                              │ │
│  │    - confidence (0-1)                                           │ │
│  │    - rationale: { technical, fundamental, risk,                 │ │
│  │                   ensemble_alignment }                          │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ LLM CALL (via LLMRouter)                                        │ │
│  │                                                                  │ │
│  │  Provider Selection:                                            │ │
│  │    1. Check per-agent routing (TRADER/ANALYST/RISK)             │ │
│  │    2. Check rate limits (RPM, RPD, concurrent)                  │ │
│  │    3. Check circuit breaker status                              │ │
│  │    4. Select available provider                                 │ │
│  │                                                                  │ │
│  │  Call:                                                           │ │
│  │    response = router.complete(                                  │ │
│  │      model="mistralai/mistral-7b-instruct",                    │ │
│  │      messages=[{role: "system", content: prompt}],              │ │
│  │      response_format={"type": "json_object"},                   │ │
│  │      temperature=0.1,                                           │ │
│  │    )                                                            │ │
│  │                                                                  │ │
│  │  Timeout: 30s (configurable)                                    │ │
│  │  Max wait: 15s when rate-limited                                │ │
│  └───────────────────────────┬─────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────────┐ │
│  │ RESPONSE PARSING                                                │ │
│  │                                                                  │ │
│  │  Parse JSON → Extract fields → Validate → TradeProposal         │ │
│  │                                                                  │ │
│  │  If invalid JSON or missing fields → HOLD (fail-closed)         │ │
│  │  If mock mode → deterministic response                          │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Safety & Risk Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SAFETY LAYERS (Defense in Depth)                   │
│                                                                      │
│  Layer 1: PRE-TRADE VALIDATION (trading_safety.py)                  │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ DeterministicValidator:                                         │ │
│  │   ✓ R:R ratio ≥ 1.0                                            │ │
│  │   ✓ Max 5 open positions                                       │ │
│  │   ✓ Cooldown between trades (300s)                             │ │
│  │   ✓ Rate limit (5 trades/hour)                                 │ │
│  │   ✓ Daily loss limit (3%)                                      │ │
│  │   ✓ Max consecutive losses (5)                                 │ │
│  │   ✓ Confluence score threshold                                 │ │
│  │   ✓ Session filter (trading hours)                             │ │
│  │   ✓ Calendar blackout check                                    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Layer 2: PORTFOLIO RISK (trading_safety.py + risk_manager.py)      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ PortfolioRiskArbiter:                                           │ │
│  │   ✓ Correlation check (new pos vs existing)                    │ │
│  │   ✓ Sector/asset concentration                                 │ │
│  │   ✓ Portfolio heat budget (max 4R)                              │ │
│  │   ✓ Margin safety pre-check                                    │ │
│  │                                                                  │ │
│  │ RiskManager:                                                    │ │
│  │   ✓ Position sizing (Kelly, capped at 1%)                      │ │
│  │   ✓ Max drawdown breaker (10%)                                 │ │
│  │   ✓ Daily loss hard stop (3%)                                  │ │
│  │   ✓ Weekly loss tracking                                       │ │
│  │   ✓ Equity floor (20% of initial → permanent halt)             │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Layer 3: EXECUTION SAFETY (execution_safety_supervisor.py)         │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ Live Execution Safety Supervisor:                               │ │
│  │   ✓ Latency guard (< 250ms round-trip)                         │ │
│  │   ✓ Slippage guard (< 2.5x historical spread)                  │ │
│  │   ✓ Daily drawdown circuit breaker (5%)                        │ │
│  │   ✓ Heartbeat watchdog (15s silence → emergency)               │ │
│  │   ✓ Warning escalation (15% peak drawdown)                     │ │
│  │   ✓ Critical escalation (20% peak drawdown)                    │ │
│  │   ✓ Equity floor (20% initial → permanent halt)                │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Layer 4: EMERGENCY CONTROLS                                        │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │   ✗ Emergency market exit (close all positions)                │ │
│  │   ✗ Kill switch (halt all trading)                             │ │
│  │   ✗ Permanent halt (equity floor breach)                       │ │
│  │   ✗ Manual intervention always possible                        │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Layer 5: POST-TRADE MONITORING                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │   ✓ Trailing stop management                                   │ │
│  │   ✓ Breakeven stop at +1R                                      │ │
│  │   ✓ Partial exits (50% at +2R, 25% at +3R)                     │ │
│  │   ✓ Time-based stop (4h stagnation)                            │ │
│  │   ✓ Daily reconciliation (broker vs journal)                   │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Layer 6: OPERATIONAL SAFETY                                        │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │   ✓ Live rollout gate (operator-signed manifests)              │ │
│  │   ✓ Research influence gate (controls research → live)         │ │
│  │   ✓ Journal integrity audit (14 rules)                         │ │
│  │   ✓ Security audit (credential scanning)                       │ │
│  │   ✓ Incident drills (response practice)                        │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Data Flow Diagrams

### 6.1 Market Data → LLM Context

```
MT5 Terminal
    │
    ▼
┌──────────────────┐
│ fetch_ohlc()     │ ← TIMEFRAME_MAP: M1/M5/M15/M30/H1/H4/D1
│ (500 bars)       │
└────────┬─────────┘
         │
    ┌────▼────────────────────────────────────┐
    │ compute_indicators(df, requests)         │
    │                                          │
    │ Standard: RSI(14), MACD, EMA(50),       │
    │           ATR(14), ADX(14)               │
    │                                          │
    │ Alpha: ALPHA012, ALPHA053, ALPHA055,    │
    │        ALPHA034, ALPHA017, ALPHA009,    │
    │        ALPHA041, ALPHA101, ALPHA030,    │
    │        ALPHA035, ALPHA014, ALPHA022,    │
    │        ALPHA025, ALPHA019, ALPHA023,    │
    │        ALPHA036, ALPHA037, ALPHA043     │
    └────────┬─────────────────────────────────┘
             │
    ┌────────▼─────────────────────────────────┐
    │ combine_alphas()                          │
    │  → Normalize (z-score expanding)          │
    │  → Compute IC (rolling Spearman, 60-bar)  │
    │  → Weight by IC_IR                        │
    │  → Composite = zscore(Σ αᵢ × wᵢ)        │
    └────────┬─────────────────────────────────┘
             │
    ┌────────▼─────────────────────────────────┐
    │ AlphaDecayMonitor.update()                │
    │  → Rolling IC per alpha                   │
    │  → Health: HEALTHY/DEGRADED/REVERSED/DEAD │
    │  → Alerts → operator outbox               │
    └────────┬─────────────────────────────────┘
             │
    ┌────────▼─────────────────────────────────┐
    │ AlphaRotationEngine.update()              │
    │  → Remove DEAD alphas                     │
    │  → Promote best candidate by IC_IR        │
    │  → Update active set                      │
    └────────┬─────────────────────────────────┘
             │
    ┌────────▼─────────────────────────────────┐
    │ toon = {                                  │
    │   price, indicators, alpha_factors,       │
    │   alpha_ensemble, alpha_decay_health,     │
    │   alpha_rotation                          │
    │ }                                         │
    └────────┬─────────────────────────────────┘
             │
             ▼
    ┌────────────────────┐
    │ LLM Orchestrator   │
    │ (builds prompt,    │
    │  calls LLM)        │
    └────────────────────┘
```

### 6.2 Trade Proposal → Execution

```
LLM Response (JSON)
    │
    ▼
┌─────────────────────┐
│ Parse & Validate    │
│ JSON structure      │
│ Required fields:    │
│ decision, entry,    │
│ sl, tp, confidence  │
└────────┬────────────┘
         │
    ┌────▼──────────────────────────┐
    │ TradeProposal construction    │
    │  strategy, symbol, side,      │
    │  entry, sl, tp, confidence,   │
    │  features, model              │
    └────────┬──────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │ DeterministicValidator        │
    │  approve/reject + reasons     │
    └────────┬──────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │ PortfolioRiskArbiter          │
    │  approve/reject + reasons     │
    └────────┬──────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │ MarginSafetyGate              │
    │  check margin sufficiency     │
    └────────┬──────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │ Journal Intent                │
    │  immutable order intent       │
    │  written to trading_state.db  │
    └────────┬──────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │ Execution                     │
    │  PAPER: PaperBroker.simulate()│
    │  DEMO:  mt5.order_send()      │
    │  LIVE:  mt5.order_send()      │
    │         + confirmation gate   │
    └────────┬──────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │ Post-Trade                    │
    │  Record in trades table       │
    │  Update risk state            │
    │  Trigger trade manager        │
    └───────────────────────────────┘
```

---

## 7. State Management

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STATE FILES & PERSISTENCE                         │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ trading_state.db (SQLite) — AUTHORITATIVE                       │ │
│  │                                                                  │ │
│  │  Tables:                                                        │ │
│  │  ├── proposals      (trade proposals: approved/rejected)        │ │
│  │  ├── trades         (executed trades with metadata)             │ │
│  │  ├── orders         (order lifecycle tracking)                  │ │
│  │  ├── alerts_outbox  (operator alerts, idempotent)               │ │
│  │  ├── research_validation_runs  (evidence)                       │ │
│  │  └── journal_*      (scope-isolated: DEMO/LIVE/PAPER)          │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ JSON State Files (companion persistence)                        │ │
│  │                                                                  │ │
│  │  risk_state.json          — RiskManager state                   │ │
│  │  trade_manager_state.json — Partial exits, pyramiding           │ │
│  │  alpha_decay_state.json   — IC history, health, alerts          │ │
│  │  alpha_rotation_state.json— Active set, dead timestamps         │ │
│  │  autonomy_state.json      — Autonomy configuration              │ │
│  │  supervisor_state.json    — Supervisor state                    │ │
│  │  trade_conditions.json    — Active trade conditions             │ │
│  │  rl_model.json            — RL model metadata                   │ │
│  │  accounts.json            — Multi-account configuration         │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ Generated Outputs                                               │ │
│  │                                                                  │ │
│  │  alpha_dashboard.html   — Auto-generated after each cycle       │ │
│  │  mt5_trader.log         — Trading session log                   │ │
│  │  journal_audit.json     — Last audit results                    │ │
│  │  lessons.jsonl          — Accumulated trading lessons           │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Scope Isolation:                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  PAPER  → No MT5 connection, no persistent journal scope       │ │
│  │  DEMO   → MT5 demo, isolated journal scope by login|server     │ │
│  │  LIVE   → MT5 live, isolated journal scope by login|server     │ │
│  │                                                                  │ │
│  │  Scope identity: fingerprinted by MT5's authenticated          │ │
│  │  account_info().login + account_info().server                   │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. LLM Routing Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LLM ROUTING ARCHITECTURE                          │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ LLMRouter (llm_router.py)                                       │ │
│  │                                                                  │ │
│  │  Per-Agent Routing:                                             │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │ │
│  │  │ TRADER   │  │ ANALYST  │  │  RISK    │                      │ │
│  │  │ Agent    │  │ Agent    │  │  Agent   │                      │ │
│  │  │          │  │          │  │          │                      │ │
│  │  │ Provider:│  │ Provider:│  │ Provider:│                      │ │
│  │  │ OpenRouter│ │ Cerebras │  │ NVIDIA   │                      │ │
│  │  │ Model:   │  │ Model:   │  │ Model:   │                      │ │
│  │  │ Mistral  │  │ Llama    │  │ Llama    │                      │ │
│  │  │ 7B       │  │ 3.1-70B  │  │ 3.1-70B  │                      │ │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘                      │ │
│  │       │              │              │                            │ │
│  │       └──────────────┼──────────────┘                            │ │
│  │                      │                                           │ │
│  │  ┌──────────────────▼──────────────────────────────────────────┐│ │
│  │  │ Provider Pool                                                ││ │
│  │  │                                                              ││ │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         ││ │
│  │  │  │ OpenRouter   │  │  Cerebras   │  │ NVIDIA NIM  │         ││ │
│  │  │  │             │  │             │  │             │         ││ │
│  │  │  │ Keys: env   │  │ Keys: env   │  │ Keys: env   │         ││ │
│  │  │  │ RPM: 60     │  │ RPM: 30     │  │ RPM: 60     │         ││ │
│  │  │  │ Timeout: 30s│  │ Timeout: 30s│  │ Timeout: 30s│         ││ │
│  │  │  │ Circuit: 3  │  │ Circuit: 3  │  │ Circuit: 3  │         ││ │
│  │  │  │ failures    │  │ failures    │  │ failures    │         ││ │
│  │  │  │ cooldown:   │  │ cooldown:   │  │ cooldown:   │         ││ │
│  │  │  │ 300s        │  │ 300s        │  │ 300s        │         ││ │
│  │  │  └─────────────┘  └─────────────┘  └─────────────┘         ││ │
│  │  │                                                              ││ │
│  │  │  Rate Limits (per provider):                                  ││ │
│  │  │  - RPM (requests per minute)                                 ││ │
│  │  │  - RPD (requests per day)                                    ││ │
│  │  │  - Max concurrent connections                                ││ │
│  │  │  - Max tokens/hour                                           ││ │
│  │  │  - Max cost/day                                              ││ │
│  │  │                                                              ││ │
│  │  │  Circuit Breaker:                                             ││ │
│  │  │  - 3 consecutive failures → open circuit                     ││ │
│  │  │  - 300s cooldown → half-open                                  ││ │
│  │  │  - Success → close circuit                                    ││ │
│  │  └──────────────────────────────────────────────────────────────┘│ │
│  │                                                                  │ │
│  │  Failover:                                                      │ │
│  │  - LLM_TRADER_ALLOW_PROVIDER_FAILOVER=false (default)           │ │
│  │  - When false: provider failure → deterministic HOLD             │ │
│  │  - When true: try fallback providers in order                   │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Safety Features:                                                    │
│  - Max wait when rate-limited: 15s (configurable)                   │
│  - Per-call timeout: 30s (configurable)                             │
│  - Bounded blocking (prevents market pulse thread freeze)           │
│  - Provider-specific model routing on failover                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. Operational Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OPERATIONAL ARCHITECTURE                          │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ AGENT SCHEDULER (agent_scheduler.py)                             │ │
│  │                                                                  │ │
│  │  Periodic Tasks:                                                │ │
│  │  ├── Journal audit (every N cycles)                             │ │
│  │  ├── Daily reconciliation                                       │ │
│  │  ├── Alert delivery (webhook)                                   │ │
│  │  ├── Memory distillation                                        │ │
│  │  ├── Research validation                                        │ │
│  │  └── Health alerts                                              │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ OPERATIONAL AGENTS (operations/agents/)                          │ │
│  │                                                                  │ │
│  │  Event Agent     → Event detection, blackout management         │ │
│  │  Incident Agent  → Incident detection & response                │ │
│  │  Learning Agent  → Continuous learning orchestration            │ │
│  │  Portfolio Agent → Portfolio-level advisory                     │ │
│  │  Research Agent  → Research pipeline orchestration              │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ PIPELINE V2 (core/harness/pipeline_v2.py)                       │ │
│  │                                                                  │ │
│  │  Stage-based execution with:                                    │ │
│  │  - Telemetry (STAGE_* spans)                                    │ │
│  │  - Retries (idempotent stages)                                  │ │
│  │  - SLA tracking                                                 │ │
│  │  - Fallback policies (continue/skip/degrade/abort_cycle)        │ │
│  │                                                                  │ │
│  │  Stages:                                                        │ │
│  │  1. blackout.gate     (EventAgent, abort on hit)                │ │
│  │  2. portfolio.consult (PortfolioAgent, advisory)                 │ │
│  │  3. legacy.cycle_body (orchestrator.execute_cycle)               │ │
│  │                                                                  │ │
│  │  Selected via PIPELINE_V2=1 env var                             │ │
│  │  Falls back to legacy path on any V2 failure                    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ ALERT SYSTEM                                                     │ │
│  │                                                                  │ │
│  │  Sources:                                                       │ │
│  │  ├── ExecutionSafetySupervisor (latency, drawdown, heartbeat)   │ │
│  │  ├── AlphaDecayMonitor (IC degradation)                         │ │
│  │  ├── RiskManager (drawdown breaker, daily loss)                 │ │
│  │  ├── HealthAlerts (system health)                               │ │
│  │  └── SecurityAudit (credential leaks)                           │ │
│  │                                                                  │ │
│  │  Delivery:                                                      │ │
│  │  1. enqueue() → alerts_outbox table (idempotent)                │ │
│  │  2. claim() → lease for delivery                                │ │
│  │  3. deliver() → webhook callback                                │ │
│  │  4. acknowledge() → mark delivered                              │ │
│  │  5. recover_expired() → re-lease expired alerts                 │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. Deployment Modes

### 10.1 Paper Mode (Internal Simulation)

```
┌──────────────┐     ┌──────────────┐
│   CLI/Menu   │────▶│  PaperBroker │────▶ Console Output
└──────────────┘     │  (Simulated) │
                     └──────────────┘
No MT5 connection needed.
```

### 10.2 Demo Mode (MT5 Demo Account)

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   CLI/Menu   │────▶│  MT5 Client  │────▶│  Demo MT5    │
└──────────────┘     │  (Real API)  │     │  Account     │
                     └──────────────┘     └──────────────┘
Real execution on demo account. No confirmation required.
Journal scope: DEMO (isolated by login|server).
```

### 10.3 Live Mode (MT5 Live Account)

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   CLI/Menu   │────▶│  MT5 Client  │────▶│  Live MT5    │
└──────────────┘     │  (Real API)  │     │  Account     │
                     └──────────────┘     └──────────────┘
Requires: LLM_TRADER_ALLOW_LIVE=true + --yes OR interactive confirm
All safety layers fully active. Permanent halt possible.
Journal scope: LIVE (isolated by login|server).
```

---

*This architecture document is a living reference. Update it when system topology changes.*
