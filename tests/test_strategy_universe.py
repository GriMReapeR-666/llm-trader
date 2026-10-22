import pytest
from core.strategy_universe import (
    StrategyStyle,
    TradingType,
    MarketRegime,
    CapitalScale,
    StrategySpec,
)

def test_taxonomy_dimensions():
    assert len(StrategyStyle) == 15
    assert len(TradingType) == 12
    assert len(MarketRegime) == 6
    assert len(CapitalScale) == 4

def test_strategy_spec_creation():
    spec = StrategySpec(
        strategy_id="trend_ema_h1_eurusd",
        name="Dual EMA Trend Following",
        style=StrategyStyle.TREND_FOLLOWING,
        trading_type=TradingType.LONG_SHORT,
        regime_suitability=[MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND],
        target_symbols=["EURUSD"],
        timeframes=["H1"],
        capital_scale=CapitalScale.SMALL,
    )
    assert spec.strategy_id == "trend_ema_h1_eurusd"
    assert spec.execution_authority is False
    d = spec.to_dict()
    assert d["style"] == "TREND_FOLLOWING"
    assert d["trading_type"] == "LONG_SHORT"
