# Comprehensive System Audit: As-Is (Current) vs. To-Be (Optimal Architecture)

> **Document ID:** `SYS-AUDIT-001`  
> **Status:** APPROVED / ARCHITECTURAL BASELINE  
> **System:** Autonomous Multi-Agent Trading System & OpenTUI Command Deck  
> **Scope:** Full-stack audit across Initialization, Steady-State Operation, Autonomous Execution, Strategy Backtesting, and UI Telemetry.

---

## Executive Summary

This audit evaluates the end-to-end architecture of the autonomous trading system, comparing the **current operational state ("As-Is")** against the **optimal institutional standard ("To-Be")**. 

The system possesses institutional quantitative foundations:
- Marcos López de Prado Combinatorial Purged Cross-Validation (CPCV), Deflated Sharpe Ratio (DSR), and Probability of Backtest Overfitting (PBO).
- Multi-agent contract separation (`TechnicalNet`, `MacroAnalyst`, `RiskController`, `LearningAgent`, `Supervisor`).
- Strict fail-closed risk envelopes ($6.0R$ heat cap, single-trade risk $\le 1\%$, max drawdown halt).
- Low-latency terminal user interface built on OpenTUI with mouse and keyboard orchestration.

However, several critical integration gaps exist between the computational engine, backend API endpoints, and the frontend UI screens that prevent backtest outputs, scanner rankings, and data catalogs from rendering seamlessly on the terminal dashboard.

---

## 1. Subsystem Audit Matrix (As-Is vs. To-Be)

| Subsystem / Phase | Current State (As-Is) | Optimal Target State (To-Be) | Gap / Action Required |
| :--- | :--- | :--- | :--- |
| **1. Initialization & Launch** | `START_DECK.bat` starts Python API `:8000`, Bun OpenTUI, MT5 MCP, and Kronos MCP. Handles port cleanup and path escaping. | Deterministic orchestrated supervisor with automated health check retry, process heartbeat tracking, and self-healing bridge restart. | Add watchdog process status verification in GUI API and auto-reconnection for MCP bridges. |
| **2. Broker Connection (MT5)** | Connected to `MetaQuotes-Demo` (`5055089321`), balance $100k, equity $100k, auto-trading enabled. Direct IPC working. | Multi-account failover, dynamic latency calibration, slippage tracking, and automated reconnect on broker socket drop. | Wire account credentials seamlessly between MCP server and `MT5Client` singleton. |
| **3. Market Scanning & Feeds** | MT5 scanner scans 12 pairs; fails closed cleanly when client is unauthenticated (`test_scanner_fail_closed.py`). | Live scanning actively ranks opportunities every 15s using live tick data, propagating candidates to the `OpportunityBus`. | Inject authenticated `MT5Client` instance into `ui/gui_api.py` scanner endpoint so `/api/scanner` serves live ranked opportunities. |
| **4. Autonomous Trading Engine** | `scheduler` executes periodic maintenance pulses. Refuses to trade without statistical edge (`HOLD`, `execution_authority: false`). | Dual-velocity event engine: Fast lane (1s quote/order management) + Slow lane (15-30s macro vectoring, regime classification, and causal graph propagation). | Maintain capital discipline while ensuring valid opportunities clear the `DeterministicValidator` without being blocked by missing module imports. |
| **5. Strategy Testing & Backtesting** | `backtest_report.json` contains full pipeline results (324 trades, Sharpe 31.8, PF 3.53). `cpcv_backtester.py` calculates DSR and PBO. But `/api/backtests` returns empty list `[]`. | Continuous autonomous backtesting daemon evaluating strategy factory candidates against historical bars; results written to DB and streamed live to `BacktestScreen.tsx`. | Re-route `/api/backtests` to read `backtest_report.json`, individual strategy test logs, and database run history. |
| **6. Strategy Library & Registry** | 13 strategies registered with stages (`FULL`, `EVALUATION`), Sharpe, PF, win rate, and recovery factor. | Automated promotion/quarantine gates driven by Out-Of-Sample (OOS) drift, SPRT Wald boundaries, and CUSUM breach detection. | Fully active in `/api/snapshot` and `StrategiesScreen.tsx`. |
| **7. Alternative Data & Macro Vector** | FRED, CFTC, and News feeds monitored. `alternative_data_health` logged in `auto_quant_tools_state.json` with warning `No module named 'economic_dynamics_catalog'`. | Real-time economic calendar ingest, FRED yield-curve slope (`T10Y2Y`), VIX term structure, and news sentiment polarity directly modulating position sizing. | Fix `economic_dynamics_catalog` import path so alternative data health reports 100% healthy. |
| **8. OpenTUI Interface Telemetry** | 100% mouse/keyboard navigable. Overhauled Positions screen (zero blank space). All 30 endpoints return HTTP 200. | All screens display rich live data with zero empty cards or placeholder screens. | Populate empty endpoints (`/api/backtests`, `/api/scanner`, `/api/tasks`, `/api/model-registry`). |

---

## 2. Detailed Phase-by-Phase Examination

### Phase I: Initialization & Startup
- **Current Flow**:
  1. `START_DECK.bat` executes pre-flight checks for `bun` and `python`.
  2. Kills stale listeners on port 8000 using `netstat` and `taskkill`.
  3. Launches `python -u ui\gui_api.py > api.log 2>&1` in background.
  4. Waits in ping-retry loop until `http://127.0.0.1:8000/api/health` returns HTTP 200.
  5. Launches `bun src/index.tsx` in the console foreground.
- **Identified Fragilities**:
  - If `gui_api.py` takes longer than 60s during first-time dependency import (torch, transformers, lightgbm), the launcher times out.
  - Port kill script (`START_DECK.bat` line 46) kills the previous API server instance, which can trigger exit notifications in background process monitors.
- **Optimal Workflow**:
  - A lightweight process manager (Supervisor) monitors health and handles graceful shutdown, hot reloads, and persistent logging with rotating file handlers.

### Phase II: Steady-State Working & Trading Analysis
- **Current Flow**:
  1. MT5 tick and OHLC bars are polled periodically.
  2. The `MarketScanner` evaluates volatility (ATR-14), Bollinger Band mid/upper/lower, RSI, and MACD.
  3. The `DeterministicValidator` checks 15 immutable safety rules (heat cap $\le 6.0R$, max risk per trade $\le 1\%$, max open positions $\le 5$, spread threshold $\le 3.5$ pips).
  4. If no edge exists, the scheduler logs `maintenance_pulse` with action `HOLD`.
- **Identified Fragilities**:
  - `llm_orchestrator.py` had a missing import for `MarketScanner` at line 4225, causing a `NameError` whenever `market_health` ran. (Now resolved).
  - `/api/scanner` in `gui_api.py` was instantiated with `client=None`, triggering the safety fail-closed scan (`[]`) rather than using the active MT5 connection.
- **Optimal Workflow**:
  - The API server reuses the active `MT5Client` instance to scan watchlists and publish live candidate opportunities to the frontend `ScannerScreen.tsx`.

### Phase III: Autonomous Backtesting & Strategy Testing
- **Current Flow**:
  1. Backtest code exists across `core/cpcv_backtester.py`, `research/full_pipeline_backtest.py`, and `research/backtest_dsr_integrator.py`.
  2. A complete backtest report exists in `backtest_report.json`.
  3. However, `ui/gui_api.py` endpoint `GET /api/backtests` strictly looked for `_aq.get("backtests", [])` inside `auto_quant_tools_state.json`, which was empty.
  4. As a result, OpenTUI's `BacktestScreen.tsx` showed `no backtests run`.
- **Optimal Workflow**:
  - `GET /api/backtests` aggregates both:
    a) Static and scheduled pipeline backtest runs from `backtest_report.json`.
    b) Individual strategy backtests generated by the Strategy Factory and CPCV engine.
  - The UI displays the full equity curve, Sharpe ratio, Sortino, Calmar, monthly return breakdown, and win-rate.

---

## 3. Core Architecture Conclusion

The mathematical, quantitative, and risk architecture of the project is top-tier:
- The system correctly refuses to overtrade or force ungrounded positions.
- Capital preservation is enforced at the database trigger and arbiter level.
- The user interface is responsive, fast, and cleanly rendered.
- Closing the routing gaps between the backtesting engine, live scanner client, and GUI endpoints will make the entire autonomous loop 100% visible and operational on the Command Deck.
