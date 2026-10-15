"""Precious-metal futures/spot tickers resolve to the TVC CFD feed.

A paying customer's AI analysed gold, read "COMEX GC1!" off market_snapshot,
then fed GC1! straight into coin_analysis / multi_timeframe_analysis — which
resolved it to KUCOIN:GC1! and 404'd, so the AI fell back to a GLD-ETF proxy.
The XAUUSD/GOLD forms already worked; these are the futures/spot forms an AI
reaches for. Pure symbol-resolution assertions, no network.

Only TVC feeds verified live are aliased (GOLD/SILVER/PLATINUM/PALLADIUM);
USOIL/COPPER/NATGAS TVC symbols don't resolve and are intentionally absent.
"""
import pytest

from tradingview_mcp.core.utils.validators import (
    normalize_tradingview_symbol,
    resolve_screener_for_symbol,
)


@pytest.mark.parametrize("ticker,expected", [
    ("GC1!", "TVC:GOLD"), ("GC2!", "TVC:GOLD"), ("MGC1!", "TVC:GOLD"), ("GCUSD", "TVC:GOLD"),
    ("SI1!", "TVC:SILVER"), ("SIUSD", "TVC:SILVER"),
    ("PL1!", "TVC:PLATINUM"), ("XPTUSD", "TVC:PLATINUM"),
    ("PA1!", "TVC:PALLADIUM"), ("XPDUSD", "TVC:PALLADIUM"),
    # regression: the forms that already worked must keep working
    ("XAUUSD", "TVC:GOLD"), ("GOLD", "TVC:GOLD"), ("XAGUSD", "TVC:SILVER"),
])
def test_futures_ticker_resolves_regardless_of_exchange_guess(ticker, expected):
    # The exchange the AI guessed (COMEX) is unknown and sanitizes to a crypto
    # default upstream, but the symbol alias must win either way.
    for exchange in ("COMEX", "KUCOIN", "OANDA"):
        assert normalize_tradingview_symbol(ticker, exchange) == expected


def test_resolved_metal_uses_the_cfd_screener():
    full = normalize_tradingview_symbol("GC1!", "COMEX")
    assert resolve_screener_for_symbol(full, "COMEX") == "cfd"


def test_metal_futures_alias_does_not_hijack_a_stock_ticker():
    # A real equity on a stock venue must NOT be rewritten to a metal feed.
    # (GC1!/PL1! are futures notation and never equities, but a bare 2-letter
    # equity like "PL" on NYSE must stay literal — we deliberately did NOT add
    # bare-2-letter metal aliases, so this stays a normal stock symbol.)
    assert normalize_tradingview_symbol("PL", "NYSE") == "NYSE:PL"
