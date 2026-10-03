# Logging Configuration Guide

## Overview

The LLM Trader uses Python's standard `logging` module with a hierarchical logger structure. Every module now logs errors that were previously swallowed, making debugging possible without code changes.

**Log output goes to:**
- Console (stdout)
- `mt5_trader.log` (file, overwritten each run)

## Quick Start

### The `--debug` Flag (Recommended)

The `--debug` flag enables DEBUG logging for **trading-critical modules only** — the orchestrator, crash detector, walk-forward validator, execution scheduler, risk manager, and 15+ other core modules — without flooding the console with every module's internal noise.

```bash
# Best for debugging trade decisions, gates, and execution
python mt5_trader.py llm-trade --symbol EURUSD --paper --debug

# With continuous mode
python mt5_trader.py llm-trade --symbol EURUSD --paper --debug --continuous
```

**What `--debug` enables:**
- `intelligence.llm_orchestrator` — all decision gates, validation, event emission
- `core.crash_detector` — crash detection state and trend analysis
- `core.walk_forward_validator` — IC decay detection, half-life estimation
- `core.optimal_execution` — Almgren-Chriss scheduling, cost-aware entry
- `core.order_execution` — margin checks, tick refresh, order retry
- `core.trade_manager` — position management, scale-out, flatten
- `core.risk_manager` — drawdown checks, exposure limits
- `core.ewc_forgetting_prevention` — EWC snapshots, penalty computation
- `core.auto_quant_tools` — all 16 auto-trigger tools
- `core.causal_feature_filter` — CRL-ABSO causal analysis
- `core.alpha_ensemble` — IC weighting, composite signal
- `core.meta_adaptive_engine` — regime profiling, adaptation
- `core.trading_tools` — indicator computation, data fetch
- `data.market_data_provider` — OHLCV fetch, DOM, calendar
- `research.rl_trainer` — Q-table updates, EWC integration

**What stays at INFO** (to reduce noise): UI modules, data providers (Seeking Alpha, FRED, Yahoo), operations/analytics, knowledge agents.

### Enable DEBUG for Everything

```bash
# Via environment variable (nuclear option — very verbose)
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper

# Via config.ini
[general]
log_level = DEBUG
```

### Enable DEBUG for a Specific Module

Python's dot-separated logger hierarchy lets you target one subsystem without flooding the console:

```bash
# Debug only the orchestrator (most trading decisions flow through here)
LOG_LEVEL=WARNING python -c "
import logging
logging.getLogger('intelligence.llm_orchestrator').setLevel('DEBUG')
# ... then import and run the trader
"

# Or inline in a script:
import logging
logging.getLogger('intelligence.llm_orchestrator').setLevel('DEBUG')
logging.getLogger('core.crash_detector').setLevel('DEBUG')
logging.getLogger('core.walk_forward_validator').setLevel('DEBUG')
```

### Per-Module DEBUG via Environment Variable

Set `LOG_LEVEL=DEBUG` in `.env`, then use a one-line filter:

```python
# Add to the top of your script or __main__ block:
import logging

# Silence noisy modules, keep only what you need
logging.getLogger("core.trading_tools").setLevel(logging.DEBUG)
logging.getLogger("data.market_data_provider").setLevel(logging.DEBUG)
logging.getLogger("intelligence.llm_orchestrator").setLevel(logging.DEBUG)

# Keep everything else at WARNING to reduce noise
for name in logging.Logger.manager.loggerDict:
    if not name.startswith("intelligence.llm_orchestrator"):
        logging.getLogger(name).setLevel(logging.WARNING)
```

## Logger Hierarchy

```
mt5_trader (root)
├── core
│   ├── core.alpha_discovery
│   ├── core.alpha_ensemble
│   ├── core.alpha_rotation
│   ├── core.alpha_decay_monitor
│   ├── core.auto_quant_tools
│   ├── core.causal_feature_filter
│   ├── core.crash_detector
│   ├── core.cpcv_backtester
│   ├── core.ewc_forgetting_prevention
│   ├── core.evolutionary_optimizer
│   ├── core.cross_market_benchmark
│   ├── core.meta_adaptive_engine
│   ├── core.multimodal_analysis
│   ├── core.optimal_execution
│   ├── core.policy_library
│   ├── core.regime_duration_tracker
│   ├── core.self_improvement_loop
│   ├── core.secure_store
│   ├── core.trade_manager
│   ├── core.trading_tools
│   ├── core.trading_constraints
│   └── core.walk_forward_validator
├── data
│   ├── data.market_data_provider
│   ├── data.seeking_alpha_provider
│   ├── data.forexfactory_provider
│   ├── data.cftc_cot_provider
│   ├── data.eia_provider
│   ├── data.exness_provider
│   └── data.data_source_pipeline
├── intelligence
│   ├── intelligence.llm_orchestrator
│   ├── intelligence.trading_memory
│   ├── intelligence.graph_engine_facade
│   ├── intelligence.active_knowledge_agent
│   ├── intelligence.cycle_brief
│   └── intelligence.case_study_matcher
├── research
│   ├── research.rl_trainer
│   ├── research.alpha_backtest_harness
│   └── research.kronos_bridge
├── operations
│   ├── operations.agent_operations
│   ├── operations.executors
│   ├── operations.account_growth
│   └── operations.analytics
└── ui
    ├── ui.gui_api
    ├── ui.gui_dashboard
    └── ui.interactive_menu
```

## Log Levels Used

| Level | When to Use | What You'll See |
|-------|-------------|-----------------|
| `ERROR` | Something broke that affects trading | Immediate attention needed |
| `WARNING` | Recoverable issue, degraded operation | State persistence failures, risk check failures |
| `INFO` | Normal operational events | Cycle starts, trades placed, data fetched |
| `DEBUG` | Detailed diagnostic info | Every exception that was previously swallowed |

### What Each Module Logs at DEBUG

| Module | DEBUG Messages |
|--------|---------------|
| `core.crash_detector` | Crash detection state save failures |
| `core.walk_forward_validator` | Walk-forward state save failures |
| `core.ewc_forgetting_prevention` | EWC state load/save failures |
| `core.trading_tools` | Indicator computation, data fetch, DOM errors |
| `core.trade_manager` | Position management, scale-out, flatten errors |
| `core.order_execution` | Margin check, tick refresh, order retry errors |
| `data.market_data_provider` | OHLCV fetch, calendar, DOM errors |
| `intelligence.llm_orchestrator` | Event emission, memory, metrics, schedule errors |
| `research.rl_trainer` | RL model load/save, EWC snapshot errors |

## Common Debug Scenarios

### 1. "Why did the system HOLD instead of trading?"

```bash
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper 2>&1 | grep -i "gate\|hold\|reject\|block"
```

Key log lines to look for:
```
DEBUG: Walk-forward decay gate: status=DEAD → HOLD
DEBUG: Crash detector gate: trend=RAPID_DESCEND → HOLD
DEBUG: Execution cost gate: costs exceed edge → HOLD
WARNING: Margin TOCTOU re-check skipped (non-fatal): ...
```

### 2. "Why did data fetch fail?"

```bash
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper 2>&1 | grep -i "fetch\|data\|ohlc\|mt5"
```

### 3. "Why did the LLM call fail?"

```bash
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper 2>&1 | grep -i "llm\|router\|api\|provider\|circuit"
```

### 4. "Why did the trade close unexpectedly?"

```bash
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper 2>&1 | grep -i "flatten\|close\|exit\|drawdown\|risk"
```

### 5. "Walk-forward decay detection not working?"

```bash
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper 2>&1 | grep -i "walk_forward\|decay\|ic\|half_life"
```

### 6. "EWC forgetting prevention not activating?"

```bash
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper 2>&1 | grep -i "ewc\|snapshot\|forget\|regime"
```

## Filtered Logging in Production

For continuous live/paper runs, use a filter to keep only what matters:

```python
# Add to config.ini or environment setup
import logging

class TradingPathFilter(logging.Filter):
    """Only allow WARNING+ from trading-critical modules, DEBUG from others."""
    
    TRADING_CRITICAL = {
        "core.trade_manager",
        "core.order_execution", 
        "core.risk_manager",
        "intelligence.llm_orchestrator",
    }
    
    def filter(self, record):
        if record.name in self.TRADING_CRITICAL:
            return record.levelno >= logging.WARNING
        return record.levelno >= logging.INFO

# Apply to root logger
logging.getLogger().addFilter(TradingPathFilter())
```

## File Logging

All output also goes to `mt5_trader.log` (overwritten each run). To append instead:

```python
# In setup_logging(), change mode='w' to mode='a':
logging.FileHandler("mt5_trader.log", mode='a', encoding='utf-8')
```

To keep historical logs:

```python
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    "mt5_trader.log",
    mode='a',
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
)
```

## CLI Flags

| Flag | Description |
|------|-------------|
| `--debug` | Enable DEBUG for trading-critical modules only (recommended) |
| `--paper` | Run in paper/simulation mode |
| `--continuous` | Run in continuous 24/7 loop |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Global log level (DEBUG, INFO, WARNING, ERROR) |
| `MT5_INIT_TIMEOUT_MS` | `5000` | MT5 connection timeout in milliseconds |
| `PIPELINE_V2` | `1` | Enable Pipeline V2 stage-based telemetry |

## Troubleshooting

### "Too much output with DEBUG"

Use `--debug` instead of `LOG_LEVEL=DEBUG`. The flag only elevates trading-critical modules:

```bash
# This is noisy (every module at DEBUG):
LOG_LEVEL=DEBUG python mt5_trader.py llm-trade --symbol EURUSD --paper

# This is targeted (only trading-critical modules):
python mt5_trader.py llm-trade --symbol EURUSD --paper --debug
```

If you still need even less output, filter the log file:
```bash
tail -f mt5_trader.log | grep -E "DEBUG|WARNING|ERROR" | grep -v "data.seeking_alpha"
```

### "Log file not created"

Check working directory — `mt5_trader.log` is created in the CWD:

```bash
ls -la mt5_trader.log
# If missing, ensure the process has write permission
```

### "TUI conflicts with logging"

The TUI dashboard (`Rich` library) has its own output channel. When TUI is active, log messages go through `TUILogHandler` instead of direct stdout. To force console output:

```bash
# Disable TUI, use plain console logging
python mt5_trader.py llm-trade --symbol EURUSD --paper --no-tui
```
