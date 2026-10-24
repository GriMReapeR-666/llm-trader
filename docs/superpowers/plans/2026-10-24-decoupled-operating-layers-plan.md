# Part 13: 8-Layer Decoupled Operating Structure — Implementation Plan

**Goal:** Implement the institutional 8-layer decoupled operating structure (`core/operating_layers.py`), orchestrating Data, Signal, Strategy, Risk, Execution, Portfolio, Monitoring, and Governance layers with strict type safety, independent testability, and rich CLI reporting.

---

## Task 1: Layer 1–4 Implementation (Data, Signal, Strategy, Risk) (TDD)
- **Files to create/modify:**
  - `core/operating_layers.py`
  - `tests/test_operating_layers.py`
- **Steps:**
  1. Write failing tests in `tests/test_operating_layers.py` for `DataLayer`, `SignalLayer`, `StrategyLayer`, and `RiskLayer`.
  2. Implement `DataLayer` (integrating Polars data loading & `DataIntegrityChecker`).
  3. Implement `SignalLayer` (generating indicator dictionaries using Numba kernels).
  4. Implement `StrategyLayer` (parameterizing candidate universe specs).
  5. Implement `RiskLayer` (evaluating tiered drawdown defense: 10% halve, 15% freeze, 20% halt).
  6. Verify all tests pass (GREEN).

## Task 2: Layer 5–8 Implementation (Execution, Portfolio, Monitoring, Governance) (TDD)
- **Files to create/modify:**
  - `core/operating_layers.py`
  - `tests/test_operating_layers.py`
- **Steps:**
  1. Write failing tests for `ExecutionLayer`, `PortfolioLayer`, `MonitoringLayer`, and `GovernanceLayer`.
  2. Implement `ExecutionLayer` (order router: TWAP, VWAP, Iceberg, and transaction cost estimation).
  3. Implement `PortfolioLayer` (position netting, cross-correlation check, and HHI concentration).
  4. Implement `MonitoringLayer` (telemetry emission, alpha decay alerting, and drift tracking).
  5. Implement `GovernanceLayer` (9-tier quant gates, kill-switch verification, and hardcoded `execution_authority = False`).
  6. Verify all tests pass (GREEN).

## Task 3: Decoupled Pipeline Orchestrator & CLI Visualizer
- **Files to create/modify:**
  - `core/operating_layers.py`
  - `mt5_trader.py`
  - `tests/test_operating_layers.py`
- **Steps:**
  1. Implement `DecoupledOperatingPipeline.tick(df)` coordinating the 8 layers end-to-end.
  2. Format comprehensive ASCII operating deck showing status and health of all 8 layers.
  3. Add CLI command in `mt5_trader.py`: `quant layered-pipeline --symbol <SYM> --timeframe <TF> --bars <N>`.
  4. Verify end-to-end via CLI and test suite.
