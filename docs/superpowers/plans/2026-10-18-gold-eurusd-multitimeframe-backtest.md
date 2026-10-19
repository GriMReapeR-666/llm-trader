# Gold & EURUSD Multi-Timeframe (D1/H4/H1/M15/M5) Backtest & Continuous Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end, multi-year (2019–2026) multi-timeframe (D1, H4, H1, M15, M5) chronological backtesting, macroeconomic event replay, and continuous Meta-Learning (MAML) + RL training pipeline for Gold (`XAUUSD`) and `EURUSD`.

**Architecture:** A Hierarchical Multi-Timeframe State Machine synchronizes D1/H4 macro regimes and SVAR IRF targets with H1 SMC structure and liquidity pools, dynamically zooming into M15/M5 execution steps during news releases and liquidity pool sweeps to capture Judas swings and mitigation entries under realistic XM Ultra Low broker friction, feeding continuous MAML/RL updates and 3-tier memory archival.

**Tech Stack:** Python 3.10+, MetaTrader5, NumPy, Pandas, PyTorch, SciPy, SQLite3.

**Spec:** `docs/superpowers/specs/2026-10-18-gold-eurusd-multitimeframe-backtest-design.md`

## Global Constraints

- Zero writes to production `trading_state.db`; all research states write to `data/research_memory.db`.
- Deterministic reproducibility governed by seeds (`seed=42`, `seed=777`).
- Zero lookahead bias: strict causal point-in-time synchronization across all timeframes.
- Minimum Risk:Reward ratio $R:R \ge 2.0$.
- Realistic broker model: XM Ultra Low spreads (EURUSD: 0.6 pip base, XAUUSD: 1.5 pip base) with news volatility widening.

---

### Task 1: Multi-Timeframe Data Synchronizer (`MultiTimeframeDataSynchronizer`)

**Files:**
- Create: `research/multitimeframe_historical_replay_pipeline.py`
- Test: `tests/test_multitimeframe_historical_replay.py`

**Interfaces:**
- Produces: `MultiTimeframeDataSynchronizer` class with `load_symbol_data(symbol, start_date, end_date) -> Dict[str, pd.DataFrame]`, `get_point_in_time_slice(symbol, timestamp) -> Dict[str, pd.DataFrame]`, and `generate_event_micro_bars(h1_bar, num_m5=12) -> pd.DataFrame`.

- [ ] **Step 1: Write the failing test for MTF Data Synchronizer**

```python
# In tests/test_multitimeframe_historical_replay.py
import pytest
import pandas as pd
from datetime import datetime, timezone
from research.multitimeframe_historical_replay_pipeline import MultiTimeframeDataSynchronizer

def test_data_synchronizer_alignment():
    sync = MultiTimeframeDataSynchronizer(seed=42)
    # Test synthetic generation and alignment across D1, H4, H1, M15, M5
    t0 = datetime(2023, 1, 2, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2023, 1, 5, 0, 0, tzinfo=timezone.utc)
    data = sync.load_mock_or_real_data("XAUUSD", t0, t1)
    assert "D1" in data and "H4" in data and "H1" in data and "M15" in data and "M5" in data
    assert len(data["D1"]) > 0
    assert len(data["H1"]) > len(data["D1"])
    assert len(data["M5"]) >= len(data["H1"]) * 12
    
    # Test point-in-time slice prevents lookahead
    query_t = datetime(2023, 1, 3, 12, 0, tzinfo=timezone.utc)
    pit_slice = sync.get_point_in_time_slice("XAUUSD", query_t)
    for tf, df in pit_slice.items():
        assert df["time"].max() <= query_t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_data_synchronizer_alignment -v`  
Expected: FAIL with ModuleNotFoundError or ImportError

- [ ] **Step 3: Implement `MultiTimeframeDataSynchronizer`**

Implement `MultiTimeframeDataSynchronizer` in `research/multitimeframe_historical_replay_pipeline.py` with multi-timeframe ingestion, cache indexing, and point-in-time filtering.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_data_synchronizer_alignment -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/multitimeframe_historical_replay_pipeline.py tests/test_multitimeframe_historical_replay.py
git commit -m "feat(research): implement multi-timeframe data synchronizer"
```

---

### Task 2: Hierarchical Multi-Timeframe State Machine (`HierarchicalMTFEngine`)

**Files:**
- Modify: `research/multitimeframe_historical_replay_pipeline.py`
- Test: `tests/test_multitimeframe_historical_replay.py`

**Interfaces:**
- Consumes: `MultiTimeframeDataSynchronizer`, `MacroeconometricVAREngine`, `NewsLiquidityManipulationDetector`.
- Produces: `HierarchicalMTFEngine` with `step_macro_bar()`, `should_zoom_in(current_time, current_price) -> bool`, and `get_macro_context() -> Dict[str, Any]`.

- [ ] **Step 1: Write the failing test for Hierarchical MTF State Machine**

```python
def test_hierarchical_mtf_state_transitions():
    from research.multitimeframe_historical_replay_pipeline import HierarchicalMTFEngine, MultiTimeframeDataSynchronizer
    sync = MultiTimeframeDataSynchronizer(seed=42)
    engine = HierarchicalMTFEngine(sync, symbol="XAUUSD", seed=42)
    
    # Step through macro bars and verify state transitions
    ctx = engine.get_macro_context()
    assert "macro_bias" in ctx
    assert "h1_order_blocks" in ctx
    assert "h1_liquidity_pools" in ctx
    assert "svar_target_price" in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_hierarchical_mtf_state_transitions -v`  
Expected: FAIL with AttributeError or ImportError

- [ ] **Step 3: Implement `HierarchicalMTFEngine`**

Implement macro analysis (D1/H4 200 EMA + SVAR IRF), H1 SMC liquidity/order-block detection, and zoom-in trigger logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_hierarchical_mtf_state_transitions -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/multitimeframe_historical_replay_pipeline.py tests/test_multitimeframe_historical_replay.py
git commit -m "feat(research): implement hierarchical MTF state machine"
```

---

### Task 3: Post-Manipulation News Mitigation Execution under XM Ultra Low Pricing

**Files:**
- Modify: `research/multitimeframe_historical_replay_pipeline.py`
- Test: `tests/test_multitimeframe_historical_replay.py`

**Interfaces:**
- Consumes: `NewsLiquidityManipulationDetector`, XM Ultra Low broker spread model.
- Produces: `MTFExecutionSimulator` executing Judas swing sweep retests with entry, stop loss, take profit ($R:R \ge 2.0$), and dynamic spread friction.

- [ ] **Step 1: Write the failing test for Judas swing mitigation execution**

```python
def test_judas_swing_mitigation_execution():
    from research.multitimeframe_historical_replay_pipeline import MTFExecutionSimulator
    sim = MTFExecutionSimulator(symbol="XAUUSD", initial_balance=10000.0, seed=42)
    # Simulate trade execution on manipulation setup
    trade = sim.execute_manipulation_trade(
        direction="BUY",
        entry_price=2000.0,
        stop_loss=1995.0,
        target_price=2012.0,
        timestamp=datetime(2023, 1, 3, 14, 30, tzinfo=timezone.utc),
        is_news_event=True
    )
    assert trade is not None
    assert trade["risk_reward"] >= 2.0
    assert trade["spread_applied"] > 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_judas_swing_mitigation_execution -v`  
Expected: FAIL

- [ ] **Step 3: Implement `MTFExecutionSimulator`**

Implement execution simulation with realistic XM Ultra Low pricing, spread widening on news shocks, and position lifecycle management.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_judas_swing_mitigation_execution -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/multitimeframe_historical_replay_pipeline.py tests/test_multitimeframe_historical_replay.py
git commit -m "feat(research): implement MTF execution simulator with XM Ultra Low pricing"
```

---

### Task 4: Continuous Meta-Learning (MAML), Meta-RL (PPO), and EWC Catastrophic Forgetting Prevention

**Files:**
- Modify: `research/multitimeframe_historical_replay_pipeline.py`
- Test: `tests/test_multitimeframe_historical_replay.py`

**Interfaces:**
- Consumes: `MetaAdaptiveEngine`, `MetaRLTrainer`, `EWCForgettingPrevention`.
- Produces: `ContinuousLearningEngine` updating regime profiles, RL trade management policies, and Fisher information matrices across historical epochs.

- [ ] **Step 1: Write the failing test for Continuous Learning Engine**

```python
def test_continuous_meta_learning_and_ewc():
    from research.multitimeframe_historical_replay_pipeline import ContinuousLearningEngine
    learn = ContinuousLearningEngine(seed=42)
    mock_epoch_trades = [
        {"pnl": 150.0, "risk_reward": 2.5, "win": True, "regime": "HAWKISH_EXPANSION"},
        {"pnl": -50.0, "risk_reward": 0.0, "win": False, "regime": "HAWKISH_EXPANSION"},
        {"pnl": 200.0, "risk_reward": 3.0, "win": True, "regime": "HAWKISH_EXPANSION"},
    ]
    result = learn.adapt_epoch(epoch_name="2023_Q1", trades=mock_epoch_trades)
    assert result["maml_updated"] is True
    assert result["ewc_loss"] >= 0.0
    assert "fisher_trace" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_continuous_meta_learning_and_ewc -v`  
Expected: FAIL

- [ ] **Step 3: Implement `ContinuousLearningEngine`**

Implement MAML adaptation, Meta-RL policy fine-tuning, and EWC penalty computation.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_continuous_meta_learning_and_ewc -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/multitimeframe_historical_replay_pipeline.py tests/test_multitimeframe_historical_replay.py
git commit -m "feat(research): implement continuous MAML, Meta-RL, and EWC engine"
```

---

### Task 5: 3-Tier Long-Term Memory Journaling & Graph RAG Incubation Bundle Export

**Files:**
- Modify: `research/multitimeframe_historical_replay_pipeline.py`
- Test: `tests/test_multitimeframe_historical_replay.py`

**Interfaces:**
- Consumes: `TradingMemory`, `data/research_memory.db`, `data/incubation_knowledge_bundle.json`.
- Produces: `ResearchMemoryBridge` that logs L1/L2/L3 memory items and updates the Graph RAG incubation bundle without touching production `trading_state.db`.

- [ ] **Step 1: Write the failing test for Memory Bridge**

```python
def test_memory_journaling_and_bundle_export():
    from research.multitimeframe_historical_replay_pipeline import ResearchMemoryBridge
    bridge = ResearchMemoryBridge(db_path="data/research_memory.db")
    # Verify strict isolation
    assert "trading_state.db" not in bridge.db_path
    
    # Record trade fact (L1) and heuristic (L3)
    fact_id = bridge.record_l1_fact("XAUUSD", "Judas swing swept London high before US NFP", {"pnl": 220.0})
    heuristic_id = bridge.record_l3_heuristic("Gold Short Mitigation Rule", "Post-CPI Judas sweep yields high R:R", 0.85)
    assert fact_id > 0
    assert heuristic_id > 0
    
    # Export bundle
    bundle_path = bridge.export_incubation_knowledge_bundle("data/test_incubation_bundle.json")
    assert os.path.exists(bundle_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_memory_journaling_and_bundle_export -v`  
Expected: FAIL

- [ ] **Step 3: Implement `ResearchMemoryBridge`**

Implement SQLite isolation, L1/L2/L3 schema management, and JSON Graph RAG knowledge bundle export.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_memory_journaling_and_bundle_export -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/multitimeframe_historical_replay_pipeline.py tests/test_multitimeframe_historical_replay.py
git commit -m "feat(research): implement research memory bridge and Graph RAG export"
```

---

### Task 6: Multi-Year Backtest & Continuous Training Pipeline Runner (`run_multitimeframe_pipeline`)

**Files:**
- Modify: `research/multitimeframe_historical_replay_pipeline.py`
- Test: `tests/test_multitimeframe_historical_replay.py`

**Interfaces:**
- Produces: `run_multitimeframe_pipeline(symbols, start_year, end_year, output_report_path) -> Dict[str, Any]` and CLI execution entry point.

- [ ] **Step 1: Write the smoke test for full pipeline**

```python
def test_full_pipeline_smoke_run():
    from research.multitimeframe_historical_replay_pipeline import run_multitimeframe_pipeline
    report = run_multitimeframe_pipeline(
        symbols=["XAUUSD", "EURUSD"],
        start_date="2024-01-01",
        end_date="2024-01-15",
        fast_mode=True,
        seed=42
    )
    assert "symbols" in report
    assert "metrics" in report
    assert report["metrics"]["total_trades"] > 0
    assert report["metrics"]["win_rate"] >= 0.50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_full_pipeline_smoke_run -v`  
Expected: FAIL

- [ ] **Step 3: Implement pipeline coordinator and CLI execution**

Wire all components together in `run_multitimeframe_pipeline` and provide a clean `__main__` entry point.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_multitimeframe_historical_replay.py::test_full_pipeline_smoke_run -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/multitimeframe_historical_replay_pipeline.py tests/test_multitimeframe_historical_replay.py
git commit -m "feat(research): implement full multi-timeframe backtest and training pipeline"
```
