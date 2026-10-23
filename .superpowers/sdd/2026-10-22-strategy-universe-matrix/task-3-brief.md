# Task 3 Brief: Polars Multi-Asset Data Ingestion & Universe Generation Engine

**Files:**
- Modify: `core/strategy_universe.py`
- Modify/Test: `tests/test_strategy_universe.py`

**Requirements:**
1. Implement `load_market_dataset_polars(data_dict: Dict[str, Any]) -> pl.DataFrame`:
   Converts data dictionaries to `pl.DataFrame` with `close` cast to `pl.Float64`.
2. Implement `StrategyUniverseGenerator`:
   - `__init__(self, seed: int = 42)`
   - `generate_matrix(symbols: Optional[List[str]] = None, timeframes: Optional[List[str]] = None, scales: Optional[List[CapitalScale]] = None) -> List[StrategySpec]`:
     Spans all 15 `StrategyStyle`s with compatible `TradingType`s, target symbols, timeframes, and regimes.
   - `generate_baseline_signals(spec: StrategySpec, df: pl.DataFrame) -> np.ndarray`:
     Generates deterministic entry/exit signals (-1, 0, 1) using Numba kernels on `df["close"]`.
     - TREND_FOLLOWING: Fast EMA vs Slow EMA crossover.
     - MEAN_REVERSION: Bollinger Bands mean reversion.
     - BREAKOUT / others: Rolling lookback breakout.
3. Add `test_polars_data_and_universe_generator()` to `tests/test_strategy_universe.py`.
4. Test failure, implementation, test pass.
5. Commit with `rtk git commit -m "feat(universe): add Polars market loader and StrategyUniverseGenerator"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-strategy-universe-matrix/task-3-report.md`.
