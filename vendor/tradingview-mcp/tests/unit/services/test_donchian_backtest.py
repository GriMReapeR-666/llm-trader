"""Regression test for the Donchian backtest strategy (PR #71).

The bug: `_run_donchian` compared bar i-1's high against the Donchian upper
band at i-1 — but that band's window *includes* highs[i-1], so a value can
never exceed the max that contains it. The `donchian` strategy therefore
returned **zero trades on any input**, silently.

The fix compares the current bar (i) against the PRIOR window's channel
(index i-1), which is the correct breakout semantics. These tests are
network-free: they call `_run_donchian` directly with synthetic candles, so
they never hit `_fetch_ohlcv` / any upstream.
"""
from tradingview_mcp.core.services.backtest_service import _run_donchian


def _candle(day: int, high: float, low: float, close: float | None = None) -> dict:
    return {
        "date": f"2026-01-{day:02d}",
        "open": high,
        "high": high,
        "low": low,
        "close": close if close is not None else high,
        "volume": 100,
    }


def test_donchian_breakout_produces_a_trade():
    # period=3. First four bars form a flat channel at 10. Bar idx 4 breaks the
    # prior 3-bar high (20 > 10) -> entry. Bar idx 6 breaks the prior 3-bar low
    # (5 < 10) -> exit. Pre-fix this returned [] on identical input.
    candles = [
        _candle(1, 10, 10),
        _candle(2, 10, 10),
        _candle(3, 10, 10),
        _candle(4, 10, 10),
        _candle(5, 20, 15, close=18),   # breakout up -> entry
        _candle(6, 20, 18, close=18),   # no exit yet
        _candle(7, 20, 5, close=8),     # breakout down -> exit
    ]
    trades = _run_donchian(candles, period=3)

    assert len(trades) == 1, f"expected exactly one completed trade, got {trades}"
    t = trades[0]
    assert t["strategy"] == "donchian"
    assert t["entry_date"] == "2026-01-05"
    assert t["exit_date"] == "2026-01-07"
    assert t["entry_date"] < t["exit_date"]


def test_donchian_flat_data_yields_no_false_signals():
    # A perfectly flat series never breaks its own channel -> no trades.
    candles = [_candle(d, 10, 10) for d in range(1, 11)]
    assert _run_donchian(candles, period=3) == []
