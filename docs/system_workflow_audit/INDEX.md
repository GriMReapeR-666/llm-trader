# System Workflow & Architecture Documentation Suite

> **Suite ID:** `DOC-SUITE-WORKFLOW-2026`  
> **Repository:** Autonomous Multi-Agent Trading System & Command Deck  
> **Location:** `docs/system_workflow_audit/`  
> **Status:** COMPLETE & INDEXED

---

## Suite Directory & Reading Order

This documentation suite provides a comprehensive, mathematically rigorous audit and optimal workflow blueprint for the entire autonomous trading project:

1. [01_COMPREHENSIVE_SYSTEM_AUDIT_AS_IS_VS_TO_BE.md](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/docs/system_workflow_audit/01_COMPREHENSIVE_SYSTEM_AUDIT_AS_IS_VS_TO_BE.md)
   - **Executive As-Is vs. To-Be Matrix**: Complete architectural comparison across Initialization, Market Scanning, Trading Engine, Backtesting, Risk Gating, and UI Telemetry.
   - **Diagnostic Findings**: Highlights resolved issues (such as the `MarketScanner` NameError fix) and pinpoints integration points between backend and OpenTUI screens.

2. [02_INITIALIZATION_AND_STARTUP_OPTIMAL_WORKFLOW.md](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/docs/system_workflow_audit/02_INITIALIZATION_AND_STARTUP_OPTIMAL_WORKFLOW.md)
   - **Deterministic Boot Sequence**: 5-stage initialization flow diagram covering pre-flight checks, path spaces escaping, port 8000 arbitration, SQLite schema verification, and broker bridge binding.
   - **Failure Recovery Runbook**: Automatic collision detection, MT5 IPC offline handling, and WebSocket backoff reconnect.

3. [03_STEADY_STATE_TRADING_ANALYSIS_WORKFLOW.md](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/docs/system_workflow_audit/03_STEADY_STATE_TRADING_ANALYSIS_WORKFLOW.md)
   - **Dual-Velocity Operating Engine**: Fast lane (1 Hz quote/order management) vs. Slow lane (15s-30s macro vectoring, regime classification, and opportunity fusion).
   - **Deterministic Risk Rules**: The 15 immutable safety rules (heat cap $\le 6.0R$, single-trade risk $\le 1\%$, consecutive loss breaker, drawdown floor).
   - **Capital Preservation Discipline**: Mathematical rationale for why the agent holds capital (`HOLD`, `execution_authority: false`) when high-conviction expectancy thresholds are not met.

4. [04_AUTONOMOUS_BACKTESTING_AND_STRATEGY_TESTING_WORKFLOW.md](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/docs/system_workflow_audit/04_AUTONOMOUS_BACKTESTING_AND_STRATEGY_TESTING_WORKFLOW.md)
   - **Quantitative Rigor**: Marcos López de Prado Combinatorial Purged Cross-Validation (CPCV), Deflated Sharpe Ratio (DSR), and Probability of Backtest Overfitting (PBO).
   - **Strategy Factory Lifecycle**: State transitions from `CANDIDATE` $\to$ `EVALUATION` $\to$ `FULL` $\to$ `QUARANTINE` $\to$ `RETIRED`.
   - **UI Bridge**: JSON schemas and routing required to render live equity curves, Sharpe/Sortino ratios, and monthly returns on `BacktestScreen.tsx` and `StrategiesScreen.tsx`.

5. [05_STEP_BY_STEP_EXECUTION_PLAN.md](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/docs/system_workflow_audit/05_STEP_BY_STEP_EXECUTION_PLAN.md)
   - **Actionable Engineering Tasks**: Bite-sized TDD plan to bridge `backtest_report.json` to `/api/backtests`, inject the authenticated MT5 client into the live scanner endpoint, resolve alternative data module paths, and verify 100% telemetry coverage across all 30 Command Deck screens.

---

## Graphify Knowledge Graph Persistence
This documentation suite is indexed directly into the persistent project knowledge graph (`graphify-out/`), allowing semantic graph traversal via `/graphify query` and instant cross-module relationship discovery across all autonomous trading subsystems.
