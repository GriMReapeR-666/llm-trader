# tests/test_fx_portfolio_netting.py
"""Unit and integration tests for Portfolio-Level FX Netting & Cross-Pair Dollar Beta Engine."""

import pytest
from core.fx_portfolio_netting import (
    normalize_symbol,
    parse_instrument_legs,
    decompose_currency_exposures,
    calculate_portfolio_currency_exposures,
    calculate_dollar_beta_exposure,
    evaluate_fx_netting_permit,
)
from core.trading_safety import PortfolioRiskArbiter, TradingStore, exposure_buckets


def test_symbol_normalization_and_leg_parsing():
    """Verify that broker suffix stripping and base/quote decomposition work correctly."""
    assert normalize_symbol("EURUSD.pro") == "EURUSD"
    assert normalize_symbol("GBPUSD_raw") == "GBPUSD"
    assert normalize_symbol("USDJPYm") == "USDJPY"
    assert normalize_symbol("XAUUSD") == "XAUUSD"
    assert normalize_symbol("GOLD") == "GOLD"

    assert parse_instrument_legs("EURUSD.pro") == ("EUR", "USD")
    assert parse_instrument_legs("USDJPY") == ("USD", "JPY")
    assert parse_instrument_legs("EURGBP") == ("EUR", "GBP")
    assert parse_instrument_legs("XAUUSD") == ("XAU", "USD")
    assert parse_instrument_legs("GOLD") == ("XAU", "USD")
    assert parse_instrument_legs("BTCUSD") == ("BTC", "USD")


def test_currency_exposure_decomposition():
    """Verify signed currency leg allocations for Buy and Sell."""
    # Buy EURUSD: Long EUR, Short USD
    buy_eurusd = decompose_currency_exposures("EURUSD", "BUY", 100.0)
    assert buy_eurusd["EUR"] == 100.0
    assert buy_eurusd["USD"] == -100.0

    # Sell EURUSD: Short EUR, Long USD
    sell_eurusd = decompose_currency_exposures("EURUSD", "SELL", 100.0)
    assert sell_eurusd["EUR"] == -100.0
    assert sell_eurusd["USD"] == 100.0

    # Buy USDJPY: Long USD, Short JPY
    buy_usdjpy = decompose_currency_exposures("USDJPY", "BUY", 80.0)
    assert buy_usdjpy["USD"] == 80.0
    assert buy_usdjpy["JPY"] == -80.0

    # Edge cases
    assert decompose_currency_exposures("EURUSD", "INVALID", 100.0) == {}
    assert decompose_currency_exposures("EURUSD", "BUY", -10.0) == {}


def test_portfolio_currency_netting_synthetic_hedge():
    """Verify that Long EURUSD + Long USDJPY nets out the USD leg to form a synthetic EURJPY."""
    positions = [
        {"symbol": "EURUSD", "side": "BUY", "risk_amount": 100.0},
        {"symbol": "USDJPY", "side": "BUY", "risk_amount": 100.0},
    ]
    res = calculate_portfolio_currency_exposures(positions)

    # USD should be completely netted (+100 - 100 = 0)
    assert "USD" not in res["net_by_currency"] or abs(res["net_by_currency"].get("USD", 0.0)) < 1e-4
    assert res["net_by_currency"]["EUR"] == 100.0
    assert res["net_by_currency"]["JPY"] == -100.0

    # Gross risk is 200, but net currency risk is 100
    assert res["gross_risk"] == 200.0
    assert res["total_net_currency_risk"] == 100.0
    assert res["netting_benefit_ratio"] == 0.50


def test_dollar_beta_exposure_aggregation():
    """Verify directional dollar beta accumulation vs dollar beta hedging."""
    # 1. Directional short dollar avalanche: Long EURUSD + Long GBPUSD + Long AUDUSD
    short_usd_positions = [
        {"symbol": "EURUSD", "side": "BUY", "risk_amount": 100.0},  # beta 1.00 -> -100 USD Beta
        {"symbol": "GBPUSD", "side": "BUY", "risk_amount": 100.0},  # beta 0.85 -> -85 USD Beta
        {"symbol": "AUDUSD", "side": "BUY", "risk_amount": 100.0},  # beta 0.75 -> -75 USD Beta
    ]
    res_short = calculate_dollar_beta_exposure(short_usd_positions)
    # Total net beta = -100 - 85 - 75 = -260
    assert res_short["net_dollar_beta_risk"] == -260.0
    assert res_short["abs_net_dollar_beta_risk"] == 260.0

    # 2. Dollar beta hedged portfolio: Long EURUSD + Long USDCHF
    hedged_positions = [
        {"symbol": "EURUSD", "side": "BUY", "risk_amount": 100.0},  # -100 USD Beta
        {"symbol": "USDCHF", "side": "BUY", "risk_amount": 100.0},  # +80 USD Beta
    ]
    res_hedged = calculate_dollar_beta_exposure(hedged_positions)
    # Total net beta = -100 + 80 = -20
    assert res_hedged["net_dollar_beta_risk"] == -20.0
    assert res_hedged["abs_net_dollar_beta_risk"] == 20.0

    # 3. FX Cross pair (EURGBP): zero dollar beta
    cross_pos = [{"symbol": "EURGBP", "side": "BUY", "risk_amount": 150.0}]
    res_cross = calculate_dollar_beta_exposure(cross_pos)
    assert res_cross["net_dollar_beta_risk"] == 0.0


def test_evaluate_fx_netting_permit_gates_dollar_avalanche():
    """Test that evaluate_fx_netting_permit blocks an excessive directional dollar bet."""
    equity = 10_000.0  # max_net_usd_beta_fraction = 0.025 -> $250 max net dollar beta risk
    open_positions = [
        {"symbol": "EURUSD", "side": "BUY", "risk_amount": 100.0},  # -100
        {"symbol": "GBPUSD", "side": "BUY", "risk_amount": 100.0},  # -85  (total -185)
    ]

    # Adding AUDUSD BUY 100.0: new beta contribution is -75 -> total -260 > 250 limit
    permitted, reason, details = evaluate_fx_netting_permit(
        open_positions=open_positions,
        proposed_symbol="AUDUSD",
        proposed_side="BUY",
        proposed_risk=100.0,
        equity=equity,
        max_net_usd_beta_fraction=0.025,
    )
    assert not permitted
    assert "net dollar beta limit exceeded" in reason
    assert details["candidate_net_dollar_beta"] == -260.0

    # But proposing USDJPY BUY 100.0 (Long USD): beta is +65 -> reduces net dollar beta from -185 to -120!
    permitted_hedge, reason_hedge, details_hedge = evaluate_fx_netting_permit(
        open_positions=open_positions,
        proposed_symbol="USDJPY",
        proposed_side="BUY",
        proposed_risk=100.0,
        equity=equity,
        max_net_usd_beta_fraction=0.025,
    )
    assert permitted_hedge
    assert "hedging" in reason_hedge or "approved" in reason_hedge
    assert details_hedge["is_hedging"] is True


def test_evaluate_fx_netting_permit_single_currency_cap():
    """Test that single currency risk cap blocks extreme concentration in one currency."""
    equity = 10_000.0  # max_single_currency_fraction = 0.03 -> $300 max
    open_positions = [
        {"symbol": "EURUSD", "side": "BUY", "risk_amount": 150.0},  # EUR +150
        {"symbol": "EURGBP", "side": "BUY", "risk_amount": 100.0},  # EUR +100 (total EUR +250)
    ]

    # Proposing EURJPY BUY 100.0 -> EUR becomes +350 > $300
    permitted, reason, _ = evaluate_fx_netting_permit(
        open_positions=open_positions,
        proposed_symbol="EURJPY",
        proposed_side="BUY",
        proposed_risk=100.0,
        equity=equity,
        max_single_currency_fraction=0.03,
    )
    assert not permitted
    assert "net currency risk limit exceeded for EUR" in reason


def test_portfolio_risk_arbiter_with_fx_netting(tmp_path):
    """Verify integration between PortfolioRiskArbiter, TradingStore, and FX Netting."""
    db_path = str(tmp_path / "test_safety.db")
    store = TradingStore(db_path)
    arbiter = PortfolioRiskArbiter(
        store,
        max_open_risk_fraction=0.05,
        max_net_usd_beta_fraction=0.025,
        max_single_currency_fraction=0.03,
    )
    equity = 10_000.0  # $250 max net dollar beta, $300 max single currency

    # 1. Backwards compatible call without symbol/side passes
    ok, msg = arbiter.permit(equity=equity, new_risk=100.0, bucket="EUR_LONG")
    assert ok
    assert msg == "approved"

    # 2. Insert open trades directly into store
    with store._connect() as con:
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, risk_amount, opened_at, payload, provenance) "
            "VALUES (1, 1001, 'swing', 'EURUSD', 'BUY', 1.0, 1.1000, 120.0, '2026-09-01T00:00:00Z', '{}', 'mt5_broker')"
        )
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, risk_amount, opened_at, payload, provenance) "
            "VALUES (2, 1001, 'swing', 'GBPUSD', 'BUY', 1.0, 1.3000, 100.0, '2026-09-01T00:00:00Z', '{}', 'mt5_broker')"
        )

    # Open positions for netting query
    open_pos = store.open_positions_for_netting()
    assert len(open_pos) == 2
    assert open_pos[0]["symbol"] == "EURUSD"

    # 3. Propose AUDUSD BUY 100.0 -> breaches dollar beta limit
    # (120*1.0 + 100*0.85 = 205. AUDUSD BUY 100 adds 75 -> 280 > 250)
    ok_aud, reason_aud = arbiter.permit(
        equity=equity,
        new_risk=100.0,
        bucket="USD_SHORT",
        symbol="AUDUSD",
        side="BUY",
    )
    assert not ok_aud
    assert "net dollar beta limit exceeded" in reason_aud

    # 4. Propose USDJPY BUY 100.0 -> acts as USD hedge (reduces net dollar beta)
    ok_hedge, reason_hedge = arbiter.permit(
        equity=equity,
        new_risk=100.0,
        bucket="USD_LONG",
        symbol="USDJPY",
        side="BUY",
    )
    assert ok_hedge
    assert reason_hedge == "approved"
