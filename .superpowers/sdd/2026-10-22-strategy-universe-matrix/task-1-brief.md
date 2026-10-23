# Task 1 Brief: Taxonomy & Strategy Specification Models

**Files:**
- Create: `core/strategy_universe.py`
- Create/Test: `tests/test_strategy_universe.py`

**Requirements:**
1. Implement `StrategyStyle(str, Enum)` with 15 styles:
   `TREND_FOLLOWING`, `MEAN_REVERSION`, `BREAKOUT`, `MOMENTUM`, `VOLATILITY`, `CARRY`, `STATISTICAL_ARBITRAGE`, `MARKET_NEUTRAL`, `EVENT_DRIVEN`, `CROSS_ASSET_MACRO`, `INTRADAY_SCALPING`, `SWING`, `POSITION_TRADING`, `ML_SIGNAL`, `RL_POLICY`.
2. Implement `TradingType(str, Enum)` with 12 types:
   `LONG_ONLY`, `SHORT_ONLY`, `LONG_SHORT`, `DIRECTIONAL`, `PAIRS_SPREAD`, `FUTURES_PERP`, `OPTIONS_BASED`, `SPOT`, `CROSS_EXCHANGE_ARB`, `EXECUTION_ALGO`, `HEDGED_PORTFOLIO`, `MULTI_ASSET_ALLOCATION`.
3. Implement `MarketRegime(str, Enum)` with 6 regimes:
   `BULL_TREND`, `BEAR_TREND`, `SIDEWAYS_CONSOLIDATION`, `HIGH_VOL_EXPANSION`, `LOW_VOL_COMPRESSION`, `STRESS_LIQUIDITY_SHOCK`.
4. Implement `CapitalScale(str, Enum)` with 4 scales:
   `MICRO`, `SMALL`, `MEDIUM`, `INSTITUTIONAL`.
5. Implement `StrategySpec` dataclass with fields:
   `strategy_id: str`, `name: str`, `style: StrategyStyle`, `trading_type: TradingType`, `regime_suitability: List[MarketRegime]`, `target_symbols: List[str]`, `timeframes: List[str]`, `capital_scale: CapitalScale = CapitalScale.SMALL`, `parameters: Dict[str, Any]`, `execution_authority: bool = False`, `created_at: float`.
   And method `to_dict() -> Dict[str, Any]`.
6. Write test in `tests/test_strategy_universe.py` for taxonomy dimensions and spec creation.
7. Run tests using `rtk pytest tests/test_strategy_universe.py -v`.
8. Commit the code with `rtk git commit -m "feat(universe): add strategy taxonomy enums and StrategySpec model"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-22-strategy-universe-matrix/task-1-report.md`.
