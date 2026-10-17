"""Unit tests for BarSynchronizedOpportunityBus and Dual Velocity routing."""
import time
import pytest

from intelligence.opportunity_bus import BarSynchronizedOpportunityBus


class DummyMarketData:
    def get_latest_price(self, symbol: str) -> float:
        return 1.0850


def test_is_new_bar():
    bus = BarSynchronizedOpportunityBus()
    assert bus.is_new_bar("EURUSD", "M15", "2026-10-17T00:00:00Z") is True
    # Same bar time -> not new
    assert bus.is_new_bar("EURUSD", "M15", "2026-10-17T00:00:00Z") is False
    # Next bar time -> new
    assert bus.is_new_bar("EURUSD", "M15", "2026-10-17T00:15:00Z") is True


def test_run_synchronized_bar_auction_dual_routing():
    bus = BarSynchronizedOpportunityBus()
    market = DummyMarketData()

    ctx = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "price": 1.0850,
        "bid": 1.0850,
        "ask": 1.0852,
        "digits": 5,
        "point": 0.0001,
        "inventory_units": 0.0,
        "features": {
            "dom_imbalance": 0.3,
            "atr": 0.0012,
            "volatility": 0.005,
        },
    }

    res = bus.run_synchronized_bar_auction(
        symbol="EURUSD",
        timeframe="M15",
        bar_time="2026-10-17T00:15:00Z",
        market_data=market,
        context=ctx,
    )

    assert res["status"] == "AUCTION_COMPLETE"
    assert res["symbol"] == "EURUSD"
    assert "scalp_winners" in res
    assert "swing_winners" in res
    assert "fast_lane_plans" in res
    assert len(res["scalp_winners"]) >= 1
    # Check that Fast Lane plan was produced for the scalp winner
    fast_plans = res["fast_lane_plans"]
    assert any("planned_entry" in p for p in fast_plans)
