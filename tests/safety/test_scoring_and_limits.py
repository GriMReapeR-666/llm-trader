"""Pin the scoring and limit helpers that mutation testing left unpinned.

Mutation scores before this file: DeterministicValidator.confluence 0 %,
exposure_buckets 0 %, TradingStore.open_bucket_risk 0 %,
check_active_trade_limit 13 %, check_max_drawdown 18 %, check_equity_floor
41 %, check_portfolio_heat 42 %.
"""
from types import SimpleNamespace

import pytest

from core.risk_manager import RiskManager
from tests.fakes.fake_mt5 import patch_everywhere
from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore, exposure_buckets

NAN = float("nan")


# ------------------------------------------------------------- confluence
ALL_BUY = {"ema_alignment": 1, "mtf_alignment": 1, "adx": 25, "near_key_level": True,
           "confirmed_candle": True, "volume_confirmed": True, "hawkes_cascade_active": True}


def test_confluence_counts_every_factor_once():
    assert DeterministicValidator.confluence(ALL_BUY, "BUY") == 7
    assert DeterministicValidator.confluence({}, "BUY") == 0


@pytest.mark.parametrize("factor", sorted(ALL_BUY))
def test_confluence_each_factor_is_worth_exactly_one(factor):
    features = dict(ALL_BUY)
    features[factor] = 0 if factor in ("ema_alignment", "mtf_alignment", "adx") else False
    assert DeterministicValidator.confluence(features, "BUY") == 6


def test_confluence_direction_follows_side():
    sell = dict(ALL_BUY, ema_alignment=-1, mtf_alignment=-1)
    assert DeterministicValidator.confluence(sell, "sell") == 7
    assert DeterministicValidator.confluence(ALL_BUY, "SELL") == 5


@pytest.mark.parametrize("features,points", [
    ({"adx": 20}, 1), ({"adx": 19.99}, 0), ({"adx_14": 20}, 1), ({"adx": None, "adx_14": 30}, 1),
    ({"adx": "bad"}, 0),
    ({"tick_volume_ratio_20": 1.1}, 1), ({"tick_volume_ratio_20": 1.09}, 0),
    ({"completed_bar_time": "2026-10-07T12:00"}, 1), ({"close": 1.1}, 1),
    ({"confirmed_candle": False, "close": 1.1}, 0), ({"volume_confirmed": False, "tick_volume_ratio_20": 5}, 0),
], ids=lambda v: str(v))
def test_confluence_thresholds_and_fallbacks(features, points):
    assert DeterministicValidator.confluence(features, "BUY") == points


# ------------------------------------------------------- exposure buckets
@pytest.mark.parametrize("symbol,side,buckets", [
    ("EURUSD", "BUY", {"EUR_LONG", "USD_SHORT"}),
    ("eurusd", "sell", {"EUR_SHORT", "USD_LONG"}),
    ("USDJPY", "BUY", {"USD_LONG", "JPY_SHORT"}),
    ("XAUUSD", "BUY", {"GOLD_LONG", "USD_SHORT"}),
    ("GOLD", "SELL", {"GOLD_SHORT", "USD_LONG"}),
    ("US30", "BUY", {"US30"}),
])
def test_exposure_buckets(symbol, side, buckets):
    assert exposure_buckets(symbol, side) == buckets


def test_open_bucket_risk_sums_only_matching_open_trades(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    for ticket, symbol, side, risk in [(1, "EURUSD", "BUY", 50.0), (2, "XAUUSD", "BUY", 30.0),
                                       (3, "USDJPY", "BUY", 20.0), (4, "EURUSD", "BUY", 7.0)]:
        proposal = TradeProposal(strategy="s", symbol=symbol, side=side, entry=1.0, stop_loss=.9,
                                 take_profit=1.2, confidence=.8, features={"setup_id": str(ticket)})
        store.record_open_trade(ticket, proposal, .01, risk, {})
    store.record_close(4, 1.0, 0.0, "test")
    assert store.open_bucket_risk("USD_SHORT") == pytest.approx(80.0)
    assert store.open_bucket_risk("USD_LONG") == pytest.approx(20.0)
    assert store.open_bucket_risk("CHF_LONG") == 0.0


# ------------------------------------------------------ risk-manager limits
@pytest.fixture
def rm(tmp_path, monkeypatch):
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", None)
    return RiskManager(balance=10_000, state_file=str(tmp_path / "risk_state.json"))


def _positions(*magics):
    return SimpleNamespace(positions_get=lambda *a, **k: [SimpleNamespace(magic=m) for m in magics])


@pytest.mark.parametrize("count,ok", [(0, True), (1, True), (2, False), (3, False)])
def test_active_trade_limit_uses_provider(rm, count, ok):
    rm.set_active_positions_provider(lambda: count)
    assert rm.check_active_trade_limit() is ok


@pytest.mark.parametrize("magics,ok", [((), True), ((460001,), True), ((460001, 460002), False),
                                       ((1, 2, 3, 460001), True)],
                         ids=["none", "one-owned", "two-owned", "manual-ignored"])
def test_active_trade_limit_counts_only_owned_mt5_positions(rm, monkeypatch, magics, ok):
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", _positions(*magics))
    assert rm.check_active_trade_limit() is ok


def test_active_trade_limit_fails_closed_when_mt5_unreadable(rm, monkeypatch):
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", SimpleNamespace(positions_get=lambda: None))
    assert rm.check_active_trade_limit() is False

    def boom():
        raise OSError("terminal gone")
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", SimpleNamespace(positions_get=boom))
    assert rm.check_active_trade_limit() is False


@pytest.mark.parametrize("equity,ok", [(9_001, True), (9_000, False), (8_000, False), (12_000, True)])
def test_max_drawdown_breaker_boundary(rm, equity, ok):
    rm.peak_equity = 10_000.0
    assert rm.check_max_drawdown(current_equity=equity) is ok


def test_max_drawdown_peak_ratchets_up(rm):
    rm.peak_equity = 10_000.0
    assert rm.check_max_drawdown(current_equity=12_000) is True
    assert rm.peak_equity == 12_000
    assert rm.check_max_drawdown(current_equity=10_800) is False     # 10 % off the new peak


@pytest.mark.parametrize("equity,ok", [(2_001, True), (2_000, True), (1_999, False)])
def test_equity_floor_boundary_and_latch(rm, equity, ok):
    rm.initial_balance = 10_000.0
    assert rm.check_equity_floor(current_equity=equity) is ok
    assert rm.permanent_halt is (not ok)
    if not ok:
        assert rm.check_equity_floor(current_equity=50_000) is False   # stays halted


@pytest.mark.parametrize("heat,units,ok", [(0.0, 1.0, True), (3.0, 1.0, True), (3.0, 1.01, False),
                                           (4.0, 0.0, True), (4.5, 0.0, False)])
def test_portfolio_heat_budget(rm, heat, units, ok):
    rm.set_portfolio_heat_provider(lambda: heat)
    assert rm.max_portfolio_heat_r == 4.0
    assert rm.check_portfolio_heat(units) is ok


def test_portfolio_heat_fails_closed_when_unmeasurable_with_positions(rm):
    rm.set_portfolio_heat_provider(lambda: None)
    rm.set_active_positions_provider(lambda: 1)
    assert rm.check_portfolio_heat(1.0) is False
    rm.set_active_positions_provider(lambda: 0)
    assert rm.check_portfolio_heat(1.0) is True
