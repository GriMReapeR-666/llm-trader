# Design Spec: 15-Part Institutional Quant Operating Framework & Universe Matrix Engine

**Date:** 2026-10-22  
**Status:** Approved  
**Author:** Pair Programming Agent & User  
**Target Architecture:** Antigravity Quant Engine (`core/`, `strategies/`, `operations/`, `research/`)

---

## 1. Executive Summary & Vision

The objective of this architectural enhancement is to implement an institutional-grade, 15-part quant trading and deployment operating framework. The system operates as a fully autonomous research and execution pipeline that continuously discovers, tests, validates, stress-tests, and allocates capital across diverse trading strategies.

The operating framework enforces strict capital preservation, out-of-sample statistical rigor, realistic microstructure and transaction cost modeling (TCA), and multi-gate deployment verification.

---

## 2. Global 15-Part Operating Framework Architecture

The framework is organized into 4 execution phases:

```mermaid
flowchart TD
    subgraph Phase 1: Foundation & Universe
        P1["1. Strategy Universe Matrix\n(15 Styles × 12 Types)"] --> P13["13. Layered Operating Architecture\n(Data, Signal, Strategy, Risk, Execution, Portfolio, Monitor, Decision)"]
    end

    subgraph Phase 2: Diagnostics & Evaluation Engine
        P2["2. Parallel & Sequential Diagnostics\n(Quick Pass → Top Subset → Deep WFO)"] --> P3["3. 4-Tier Evaluation Engine\n(Data, Signal, Execution, Portfolio Checks)"]
        P3 --> P4["4. Regime-Aware Backtesting\n(IS, OOS, WFO, Stress Regimes)"]
    end

    subgraph Phase 3: Survivability, Risk & Small-Capital Growth
        P5["5. Small-Capital Compounding Engine\n(Kelly Bounds, Drawdown Stops, Drift)"] --> P7["7. Regime-Switching Monte Carlo\n(Fat-Tail Ruin Probabilities & Perturbation)"]
        P7 --> P8["8. Constrained RL Policy Baselines\n(Action Caps & Risk-Adjusted Reward)"]
        P8 --> P10["10. Account Flip & Capital Preservation\n(De-risking, Bucket Allocation)"]
    end

    subgraph Phase 4: Gates, Execution & Autonomous Telemetry
        P6["6. 9-Tier Quant Gate Stack\n(Data → Signal → Risk → Exec → MC → Shadow)"] --> P9["9. Dynamic Order & Portfolio Sizing\n(Almgren-Chriss, SOR, Vol Targeting)"]
        P11["11. Telemetry, Tracking & Health Monitor\n(Event Bus, Drift, Overfit Alert)"] --> P12["12 & 14. Best-Practice Deployment Pipeline\n(Live-Shadow & Kill Switch Logic)"]
        P12 --> P15["15. 7-Factor Composite Ranking Engine\n(25% Return, 20% DD, 15% Regime, 15% Exec, 10% MC, 10% Div, 5% Cap)"]
    end

    Phase 1 --> Phase 2 --> Phase 3 --> Phase 4
```

---

## 3. Deep Dive: Part 1 — Strategy Universe Matrix Engine

Part 1 establishes the universe coordinate space and high-speed simulation backbone upon which all downstream diagnostics, gates, and portfolio optimizers operate.

### 3.1 Strategy Taxonomy Dimensions

The universe is parameterized across 6 discrete coordinates:
1. **15 Strategy Styles**:
   - `TREND_FOLLOWING`: Dual EMA crossover, Donchian channel breakout, Supertrend trend continuation.
   - `MEAN_REVERSION`: Bollinger band compression, RSI extreme exhaustion, Ornstein-Uhlenbeck mean-reverting drift.
   - `BREAKOUT`: Keltner channel volatility expansion, multi-day range breakouts.
   - `MOMENTUM`: Cross-sectional relative strength and time-series momentum filters.
   - `VOLATILITY`: Volatility targeting, ATR expansion/compression regimes, IV/RV dispersion.
   - `CARRY`: Overnight rollover, interest rate parity differentials.
   - `STATISTICAL_ARBITRAGE`: Cointegration spread trading, Kalman filter dynamic hedge ratios.
   - `MARKET_NEUTRAL`: Long/short beta-neutral portfolio balancing.
   - `EVENT_DRIVEN`: Macroeconomic announcement reactions (CPI, Non-Farm Payrolls, Rate decisions).
   - `CROSS_ASSET_MACRO`: Multi-asset transmission models (Yields, DXY, Equities, Commodities).
   - `INTRADAY_SCALPING`: Micro-orderbook spread capture, Avellaneda-Stoikov market-making principles.
   - `SWING`: Multi-day liquidity sweeps, structural market transitions (BOS, order blocks).
   - `POSITION_TRADING`: Macro fundamental valuations, CFTC COT positioning alignment.
   - `ML_SIGNAL`: Probabilistic classification models with meta-labeling.
   - `RL_POLICY`: Deep reinforcement learning agents with strictly bounded action spaces.

2. **12 Trading Types**:
   - `LONG_ONLY`, `SHORT_ONLY`, `LONG_SHORT`, `DIRECTIONAL`, `PAIRS_SPREAD`, `FUTURES_PERP`, `OPTIONS_BASED`, `SPOT`, `CROSS_EXCHANGE_ARB`, `EXECUTION_ALGO`, `HEDGED_PORTFOLIO`, `MULTI_ASSET_ALLOCATION`.

3. **6 Market Regimes**:
   - `BULL_TREND`, `BEAR_TREND`, `SIDEWAYS_CONSOLIDATION`, `HIGH_VOL_EXPANSION`, `LOW_VOL_COMPRESSION`, `STRESS_LIQUIDITY_SHOCK`.

4. **4 Capital Scales**:
   - `MICRO` ($500 – $2,000)
   - `SMALL` ($10,000)
   - `MEDIUM` ($100,000)
   - `INSTITUTIONAL` ($1,000,000+)

5. **Instruments & Timeframes**:
   - FX Pairs (`EURUSD`, `GBPUSD`, `USDJPY`), Commodities (`XAUUSD`, `USOIL`), Indices (`SPX500`, `NAS100`), Crypto (`BTCUSD`, `ETHUSD`).
   - Granularities: `M1`, `M5`, `M15`, `H1`, `H4`, `D1`.

6. **Execution & Friction Models**:
   - Ideal (Zero slippage benchmark) vs. Realistic (Endogenous spread + Almgren-Chriss impact via `core/transaction_cost_engine.py`).

---

## 4. High-Performance Technology Stack: Polars & Numba Architecture

To evaluate thousands of permutation paths efficiently:
- **Polars (`pl.DataFrame`, `pl.LazyFrame`)**: High-performance data ingestion and columnar manipulation, enabling sub-millisecond multi-asset bar slicing and cross-sectional matrix joins.
- **Numba (`@nb.njit(fastmath=True, parallel=True)`)**: C-speed compilation for:
  - Technical indicator computation (moving averages, bands, ATR, oscillators).
  - High-throughput simulation kernels modeling order fills, slip, and dynamic fee deduction.
  - Vectorized metrics calculation (Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor).
- **VectorBT Integration**: Native fallback support for `vectorbt` Portfolio API, while maintaining high-speed Numba kernels when standalone.

---

## 5. Telemetry & Autonomous Event Bus Integration

Every step emits structured audit events through `core/agent_event_bus.py` and persistent JSONL records:
- Event: `STRATEGY_UNIVERSE_INITIALIZED` (Taxonomy counts, grid dimensions)
- Event: `DIAGNOSTIC_BATCH_STARTED` (Batch ID, strategy family, regime)
- Event: `SIMULATION_COMPLETED` (Returns, Drawdown, Latency, Trade Counts)
- Event: `QUANT_GATE_EVALUATED` (Stage verdict, threshold checks, rejection details)

---

## 6. Implementation Plan & Deliverables for Part 1

1. **`core/strategy_universe.py`**:
   - Taxonomy enums and dataclasses (`StrategyStyle`, `TradingType`, `MarketRegime`, `CapitalScale`, `StrategySpec`).
   - Fast Numba `@njit` kernels for signal generation and order fill simulation.
   - Polars-based dataset loader and parallel evaluator.
   - Universe generator and catalog registry.
   - Telemetry emission integrated with `AgentEventBus`.
2. **`tests/test_strategy_universe.py`**:
   - Comprehensive test suite asserting matrix completeness ($15 \times 12$).
   - Verification of Numba kernels and Polars data ingestion.
   - Deterministic metric correctness test.
