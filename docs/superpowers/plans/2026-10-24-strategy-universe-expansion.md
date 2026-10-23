# Strategy Universe Matrix Engine & Autonomous Logging Implementation Plan

**Goal:** Expand Part 1 of the 15-part institutional quantitative operating framework into a comprehensive Strategy Universe Matrix Engine with dedicated signal kernels for all 15 trading styles and 12 trading types, tested across multi-asset symbols, timeframes, regimes, capital scales, and execution conditions, emitting rich multi-stage autonomous telemetry and logging.

**Architecture Reference:** [`core/strategy_universe.py`](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/core/strategy_universe.py), [`operations/autonomous_pipeline.py`](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/operations/autonomous_pipeline.py), [`mt5_trader.py`](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/mt5_trader.py).

---

### File Structure & Responsibility

- `core/strategy_universe.py`:
  - Enhance `StrategyStyle` (15 styles) and `TradingType` (12 types) specifications.
  - Implement 15 specialized, high-performance `@nb.njit(fastmath=True)` signal generation kernels.
  - Implement trading-type filters and execution wrappers.
  - Implement multi-asset regime matrix evaluation (`evaluate_strategy_universe_matrix`).
  - Implement rich telemetry reporting with structured per-style diagnostics.
- `operations/autonomous_pipeline.py`:
  - Instrument the autonomous loop to run the expanded universe matrix.
  - Emit rich multi-line diagnostic logs, structured events (`STRATEGY_UNIVERSE_EVALUATION`), and leaderboard tables.
- `mt5_trader.py`:
  - Wire CLI subcommand `python mt5_trader.py quant universe-sweep` for manual and autonomous execution.
  - Enhance live engine telemetry formatting for cycle-based sweeps.
- `tests/test_strategy_universe_expanded.py`:
  - Test suite asserting 100% style/type coverage, signal differentiation, execution speed (<1.0s for full universe), and telemetry emission.

---

### Task Breakdown

#### Task 1: 15-Style Numba Signal Kernels & Trading Type Filters
- **File:** `core/strategy_universe.py`
- **Steps:**
  1. Write failing tests in `tests/test_strategy_universe_expanded.py` verifying all 15 styles generate unique, non-trivial deterministic signals on synthetic/archive prices.
  2. Implement `@nb.njit` kernels in `core/strategy_universe.py`:
     - Donchian breakout
     - RSI/ROC momentum
     - ATR volatility expansion squeeze
     - Rate differential carry proxy
     - Cointegration Kalman spread
     - Market-neutral dual asset hedge
     - Event post-announcement drift
     - Cross-asset macro impulse
     - Microstructure scalping
     - Swing pivot pullback
     - Position 50/200 MA filter
     - ML probabilistic classifier simulation
     - RL actor-critic policy simulation
  3. Implement trading type post-processing (Long-Only, Short-Only, Long/Short, Directional, Pairs, Perp, Options-vol, Spot, Cross-Arb, Exec-Algo, Hedged, Multi-Asset).
  4. Run tests with `rtk pytest tests/test_strategy_universe_expanded.py -v`.
  5. Commit task changes.

#### Task 2: Multi-Dimensional Matrix Generation & Regime Simulator
- **File:** `core/strategy_universe.py`
- **Steps:**
  1. Write failing tests verifying matrix generation across symbols (`EURUSD`, `GBPUSD`, `USDJPY`, `XAUUSD`), timeframes (`M15`, `H1`, `D1`), regimes (6 regimes), and capital scales.
  2. Implement `StrategyUniverseGenerator.generate_full_matrix()` generating structured candidate specifications with regime suitability tagging.
  3. Enhance `simulate_positions_numba` to incorporate trading-type friction (borrow costs for shorts, funding fees for perps, execution slippage).
  4. Run tests with `rtk pytest tests/test_strategy_universe_expanded.py -v`.
  5. Commit task changes.

#### Task 3: Rich Autonomous Operational Logger & EventBridge Integration
- **Files:** `core/strategy_universe.py`, `operations/autonomous_pipeline.py`
- **Steps:**
  1. Write failing tests verifying detailed logging output and event bus payloads (`UNIVERSE_SWEEP_STARTED`, `STYLE_EVALUATED`, `UNIVERSE_SWEEP_COMPLETED`).
  2. Add structured logger to `evaluate_strategy_universe` emitting table-formatted summaries:
     - Header block: Universe size, symbols, timeframes, active regime.
     - Per-style diagnostic line: Sharpe, Sortino, Win Rate, Expectancy, Max DD, Gate Verdict.
     - Leaderboard summary: Top 5 finalists by style, score, and ruin risk.
  3. Update `operations/autonomous_pipeline.py` to invoke the expanded matrix and format the console/log output.
  4. Run tests with `rtk pytest tests/test_autonomous_quant_pipeline.py tests/test_strategy_universe_expanded.py -v`.
  5. Commit task changes.

#### Task 4: CLI Subcommand & Integration Verification
- **Files:** `mt5_trader.py`, `tests/test_strategy_universe_expanded.py`
- **Steps:**
  1. Add `python mt5_trader.py quant universe-sweep` CLI handler.
  2. Benchmark execution speed: ensure the complete 15-style matrix evaluates in under 1.5 seconds on 1,000 bars using Numba fastmath.
  3. Run full quant test suite (`tests/test_strategy_universe*.py`, `tests/test_autonomous_quant_pipeline.py`).
  4. Run `rtk graphify update .` to update the AST knowledge graph.
  5. Final commit and verification.
