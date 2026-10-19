"""European + Canadian exchange routing (#95).

Every EU/CA exchange name has to agree on three things: the screener market,
the TradingView symbol prefix, and the prefix used in its coinlist. Any
mismatch fails silently (the scanner returns 0 rows, not an error):
``EPA:MC`` instead of ``EURONEXT:MC``, ``TSX:AMY`` for a Venture listing,
or Xetra prices for someone who asked for Frankfurt.
"""
import pytest

from tradingview_mcp.core.services import screener_service
from tradingview_mcp.core.services.coinlist import load_symbols
from tradingview_mcp.core.utils.validators import (
    _EXCHANGE_TV_PREFIX,
    EXCHANGE_SCREENER,
    STOCK_EXCHANGES,
    get_market_type,
    get_tv_exchange_prefix,
    is_stock_exchange,
    resolve_screener_for_symbol,
)

# exchange name -> (screener market, TradingView prefix)
ROUTES = {
    "epa": ("france", "EURONEXT"), "paris": ("france", "EURONEXT"), "enx": ("france", "EURONEXT"),
    "ams": ("netherlands", "EURONEXT"), "aex": ("netherlands", "EURONEXT"),
    "bru": ("belgium", "EURONEXT"), "enb": ("belgium", "EURONEXT"),
    "lis": ("portugal", "EURONEXT"), "elp": ("portugal", "EURONEXT"),
    "mil": ("italy", "MIL"), "milan": ("italy", "MIL"), "bit": ("italy", "MIL"),
    "lse": ("uk", "LSE"), "lon": ("uk", "LSE"),
    "six": ("switzerland", "SIX"), "swx": ("switzerland", "SIX"),
    "bme": ("spain", "BME"),
    "tsx": ("canada", "TSX"),
    "tsxv": ("canada", "TSXV"), "xsx": ("canada", "TSXV"), "ventures": ("canada", "TSXV"),
    "xetra": ("germany", "XETR"), "xetr": ("germany", "XETR"),
    "fwb": ("germany", "FWB"), "fra": ("germany", "FWB"),
}


@pytest.mark.parametrize("name,route", sorted(ROUTES.items()))
def test_routes_to_the_expected_screener_and_prefix(name, route):
    market, prefix = route
    for spelling in (name, name.upper()):
        assert is_stock_exchange(spelling)
        assert get_market_type(spelling) == market
        assert get_tv_exchange_prefix(spelling) == prefix


@pytest.mark.parametrize("name,route", sorted(ROUTES.items()))
def test_coinlist_uses_the_routed_prefix(name, route):
    _, prefix = route
    symbols = load_symbols(name)
    assert len(symbols) >= 30, f"{name}: coinlist missing or truncated"
    assert {s.split(":", 1)[0] for s in symbols} == {prefix}


def test_aliases_share_their_venues_coinlist():
    assert load_symbols("paris") == load_symbols("epa")
    assert load_symbols("ventures") == load_symbols("tsxv")
    assert load_symbols("FRA") == load_symbols("fwb")
    assert load_symbols("fwb") != load_symbols("xetra")


@pytest.mark.parametrize("ambiguous", ["tse", "mcx"])
def test_ambiguous_names_are_not_routed(ambiguous):
    # "tse" usually means Tokyo and "mcx" India's Multi Commodity Exchange.
    assert ambiguous not in STOCK_EXCHANGES
    assert ambiguous not in EXCHANGE_SCREENER
    assert ambiguous not in _EXCHANGE_TV_PREFIX


def test_no_yahoo_suffixes_in_coinlists():
    # "EPA:MC.PA" / "TSX:CSU.TO" are Yahoo conventions and return 0 rows on TradingView.
    for name in ROUTES:
        assert [s for s in load_symbols(name) if s.endswith((".PA", ".TO", ".MI"))] == []


# --- EURONEXT is one prefix across four markets ------------------------------

@pytest.mark.parametrize("exchange,market", [
    ("epa", "france"), ("paris", "france"), ("ams", "netherlands"),
    ("bru", "belgium"), ("lis", "portugal"),
])
def test_euronext_symbol_uses_the_named_venues_screener(exchange, market):
    # Before: EURONEXT:* resolved to "crypto", found nothing, and venue
    # fallback answered EPA MC with NYSE:MC (Moelis, not LVMH).
    assert resolve_screener_for_symbol("EURONEXT:MC", exchange) == market


def test_euronext_symbol_without_a_euronext_venue_defaults_to_paris():
    assert resolve_screener_for_symbol("EURONEXT:MC", "kucoin") == "france"


# --- venue fallback across colliding tickers ---------------------------------

@pytest.fixture
def listed_on(monkeypatch):
    def _set(*venues):
        monkeypatch.setattr(screener_service, "exchanges_listing_symbol", lambda s: list(venues))
    return _set


def test_fallback_stays_in_the_requested_venues_market(listed_on):
    listed_on("EPA", "NYSE")  # MC: LVMH on Euronext Paris, Moelis on NYSE
    assert screener_service.pick_fallback_exchange("MC", "NASDAQ") == "NYSE"


def test_fallback_from_a_crypto_venue_prefers_the_us(listed_on):
    listed_on("EPA", "NYSE")
    assert screener_service.pick_fallback_exchange("MC", "KUCOIN") == "NYSE"


def test_fallback_from_a_european_venue_does_not_jump_to_the_us(listed_on):
    listed_on("EPA", "NYSE")
    assert screener_service.pick_fallback_exchange("MC", "AMS") == "EPA"


def test_crypto_fallback_order_is_unchanged(listed_on):
    listed_on("GATEIO", "KUCOIN")
    assert screener_service.pick_fallback_exchange("HYPEUSDT", "BINANCE") == "KUCOIN"
