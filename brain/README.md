# Brain — Agent Cognitive System

On-disk cognitive artifacts for the trading agent: conversational context
continuity, episodic traces, and distilled lessons.

## Architecture

The Brain directory complements the SQLite-backed L0-L3 memory hierarchy in
`intelligence/trading_memory.py`.  The SQLite journal remains authoritative;
these files are research aids, retrieval artifacts, and the on-disk layer
for the memory spec.

```
brain/
├── __init__.py          # Package exports
├── memory.py            # ContextBuffer — conversational context continuity
├── episodic.py          # EpisodicStore — L0-L3 trace artifacts on disk
├── distiller.py         # LessonDistiller — extract lessons from outcomes
├── artifacts/           # Runtime artifacts (gitignored)
│   ├── traces/          # Per-symbol episodic traces (JSON)
│   │   ├── EURUSD/
│   │   └── XAUUSD/
│   ├── scenarios/       # Per-symbol scenario documents (Markdown, L2)
│   │   └── EURUSD/
│   │       ├── london_breakout_high_adx.md
│   │       └── cpi_release_reaction.md
│   └── lessons/         # Monthly distilled lessons (JSON)
│       └── 2026-09.json
└── README.md
```

## Components

### ContextBuffer (`memory.py`)

Bounded conversational context buffer for the LLM orchestrator.  When the
orchestrator starts, it imports:

```python
from brain.memory import ContextBuffer
```

If the import fails, the system falls back to stateless mode.  The buffer:

- Holds the last N messages (configurable, default 10)
- Compresses older messages into summaries when full
- **Pinned messages** (risk decisions, errors) are never dropped
- Persists to `brain/context_buffer.json` across restarts

### EpisodicStore (`episodic.py`)

On-disk storage for episodic trading traces — the file-based complement to
the SQLite `atomic_facts` and `scenario_documents` tables.

- **Traces**: Individual trade episodes (setup → decision → outcome → lesson)
- **Scenarios**: L2 scenario documents grouped by symbol/setup/regime
- **Lessons**: Monthly aggregated distilled lessons

### LessonDistiller (`distiller.py`)

Extracts actionable lessons from completed trades:

- Large losses (≤ -2.0R) → risk management lessons
- Big winners (≥ 3.0R) → replication patterns
- Regime-specific patterns
- Aggregates into monthly lesson files

## Memory Hierarchy (TRADING_MEMORY_SPEC.md)

| Layer | Name | Brain Artifact | SQLite Store |
|-------|------|----------------|--------------|
| L0 | Raw trace | `artifacts/traces/` | `trading_state.db` (append-only) |
| L1 | Atomic facts | `artifacts/traces/` | `atomic_facts` table |
| L2 | Scenario docs | `artifacts/scenarios/` | `scenario_documents` table |
| L3 | Core memory | `artifacts/lessons/` | `core_memories` table |

## Usage

```python
# Context buffer for LLM continuity
from brain.memory import ContextBuffer
buf = ContextBuffer(max_messages=10)
buf.add("user", "Analyze EURUSD H1")
buf.add("assistant", "EURUSD shows bearish structure...")
messages = buf.get_messages()  # → list of dicts for LLM prompt
buf.save("brain/context_buffer.json")

# Episodic trace storage
from brain.episodic import EpisodicStore, EpisodicTrace
store = EpisodicStore()
trace = EpisodicTrace(
    trace_id="abc123", symbol="EURUSD", strategy="swing",
    timeframe="H1", regime="trending", outcome="WIN", r_multiple=2.1
)
store.save_trace(trace)
stats = store.get_stats("EURUSD")

# Lesson distillation
from brain.distiller import LessonDistiller
distiller = LessonDistiller()
lesson = distiller.distill_from_trace({
    "symbol": "EURUSD", "strategy": "swing", "outcome": "LOSS",
    "r_multiple": -1.5, "setup_description": "London breakout..."
})
distiller.save()
```
