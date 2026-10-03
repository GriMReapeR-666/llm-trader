# Trading Strategies — Plugin Framework

Drop-in location for custom signal generation and execution strategy scripts.

## Quick Start

1. Copy `example_strategy_template.py` to a new file (e.g., `my_breakout.py`)
2. Modify the class — change `name`, `symbols`, `timeframe`, and `evaluate()`
3. The loader discovers it automatically — no registration needed

```python
from strategies.base import BaseStrategy, StrategySignal

class MyStrategy(BaseStrategy):
    name = "my_breakout"
    description = "Breakout strategy for London session"
    symbols = ["EURUSD", "GBPUSD"]
    timeframe = "M15"

    def evaluate(self, context) -> StrategySignal:
        # Your signal logic here
        price = context["price"]
        return StrategySignal(
            strategy=self.name,
            symbol=context["symbol"],
            side="BUY",
            confidence=0.7,
            entry=price,
            stop_loss=price - 20 * context["point"],
            take_profit=price + 40 * context["point"],
        )
```

## Architecture

```
strategies/
├── __init__.py                    # Package exports
├── base.py                        # BaseStrategy ABC + StrategySignal dataclass
├── loader.py                      # Dynamic module discovery (importlib)
├── registry.py                    # Lifecycle state machine (8 states)
├── example_strategy_template.py   # Copy this to create new strategies
├── registry.json                  # Persisted lifecycle state (auto-generated)
└── *.py                           # Your custom strategies go here
```

## Components

### BaseStrategy (`base.py`)

Abstract base class.  All strategies must implement `evaluate()`.

**Lifecycle hooks:**
- `on_load()` — one-time initialization
- `on_activate()` — start receiving data
- `evaluate(context) → StrategySignal` — produce a signal
- `on_deactivate()` — stop receiving data
- `on_unload()` — cleanup before removal

**Safety:**
- Strategies never have execution authority
- All signals pass through deterministic validation and risk arbitration
- Cannot modify risk limits or override hard rules

### StrategySignal (`base.py`)

The output of `evaluate()`.  Contains:
- `side`: BUY / SELL / HOLD / REJECT
- `confidence`: 0.0 - 1.0
- `entry`, `stop_loss`, `take_profit`: price levels
- `rationale`: human-readable explanation
- `confluence_score`, `regime_alignment`, `mtf_alignment`: quality metrics

### StrategyLoader (`loader.py`)

Discovers `.py` files in this directory and instantiates `BaseStrategy`
subclasses via `importlib`.  Skips `__init__.py`, `base.py`, `loader.py`,
`registry.py`, and template files.

```python
from strategies import discover_strategies
strategies = discover_strategies()
# → {"my_breakout": <MyStrategy>, "example_momentum": <ExampleMomentum>}
```

### StrategyRegistry (`registry.py`)

Tracks lifecycle state for each strategy.  States mirror
`operations/strategy_lifecycle.py`:

```
CANDIDATE → VALIDATING → PAPER_PROVEN → DEMO_PROVEN → DEPLOYED → DECAYING → FROZEN → RETIRED
```

**Key rules:**
- Automated evaluation can only DEMOTE (DEPLOYED → DECAYING → FROZEN)
- All promotions require an explicit operator actor
- FROZEN/RETIRED strategies are blocked from execution
- Code-based setups start as DEPLOYED

```python
from strategies import StrategyRegistry
registry = StrategyRegistry()
registry.register(my_strategy)  # → RegistryEntry(state="DEPLOYED")
registry.can_execute("my_breakout")  # → True
registry.promote("my_breakout", actor="operator")  # → DEPLOYED → DECAYING (wait, that's a demotion)
```

## Integration with Existing Systems

### TUI StrategiesScreen

The registry feeds data to the TUI `StrategiesScreen` (screen 9).  Each
`RegistryEntry` maps to the `StrategyState` TypeScript interface:

| RegistryEntry field | TypeScript field |
|---|---|
| `name` | `id`, `name` |
| `state` | `stage` |
| `symbols` | `symbols` |
| `timeframe` | `timeframe` |
| `total_trades` | `trades` |
| `win_rate` | `winRate` |
| `expectancy_r` | `expectancyR` |

### Harness Workflows

Built-in institutional workflows live in `core/harness/workflows.py` (7
archetypes).  Drop-in strategies in this directory complement those with
user-authored, custom signal logic.

### LLM Orchestrator

The orchestrator (`intelligence/llm_orchestrator.py`) references strategies
by name for LLM rate limiting and lifecycle gating.  Drop-in strategies
can be referenced the same way once registered.

## Existing Built-in Strategies

For reference, these are the hardcoded strategy types in the system:

| Strategy | Source | Description |
|---|---|---|
| `swing` | `intelligence/llm_orchestrator.py` | Multi-timeframe swing trading |
| `scalp` | `intelligence/llm_orchestrator.py` | Short-horizon scalping |
| `statistical_arbitrage` | `core/harness/workflows.py` | Kalman Filter state-space pairs |
| `mean_reversion` | `core/harness/workflows.py` | Fractional differentiation + BOCPD |
| `macro_causal_momentum` | `core/harness/workflows.py` | 134-node DAG + 23 barometers |
| `microstructure_breakout` | `core/harness/workflows.py` | VPIN + micro-price + Hurst |
| `neural_kronos_foundation` | `core/harness/workflows.py` | Kronos neural + CPCV DSR |
| `macro_event_alpha` | `core/harness/workflows.py` | Leading indicators + surprise vector |
| `retail_quant_models` | `core/harness/workflows.py` | 11 document-derived models |
