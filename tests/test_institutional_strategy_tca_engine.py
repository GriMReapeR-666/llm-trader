"""
Unit Tests for Institutional Strategy Suite, Pre-Trade TCA Gate & Archetype Exit Engine
======================================================================================
Verifies all 6 institutional strategies, Pre-Trade TCA gate, Currency Covariance Sizer,
Archetype Exit Manager, and the Master Tournament Coordinator.
"""

import math
import pytest
import numpy as np

from core.institutional_strategy_tca_engine import (
    BaseInstitutionalStrategy,
    StrategyArchetype,
    ExecutionStyle,
    ExitCategory,
    InstitutionalSignal,
    KalmanStatArbStrategy,
    CointegrationPairStrategy,
    AvellanedaStoikovMeanReversionStrategy,
    MacroEventPullbackStrategy,
    KronosNeuralForecastStrategy,
    MultiFactorEnsembleStrategy,
    InstitutionalPreTradeTCAGate,
    CurrencyCovarianceRiskSizer,
    ArchetypeExitManager,
    InstitutionalStrategyTCAEngine,
)


# ---------------------------------------------------------------------------
# 1. Kalman Statistical Arbitrage Strategy Tests
# ---------------------------------------------------------------------------

def test_kalman_statarb_valid_entry():
    strat = KalmanStatArbStrategy(strategy_id="statarb_eur_chf", primary_symbol="EURCHF", hedge_symbol="USDCHF")
    # Generate stationary mean-reverting OU residuals
    np.random.seed(42)
    residuals = []
    curr = 0.0
    for _ in range(50):
        curr = curr * 0.7 + np.random.normal(0, 0.5)
        residuals.append(curr)

    ctx = {
        "is_rollover_window": False,
        "bocpd_changepoint_prob": 0.15,
        "residuals": residuals,
        "z_score": -2.35,
        "beta": 0.88,
        "price": 0.9500,
        "atr": 0.0020,
    }
    sig = strat.evaluate_opportunity(ctx)
    assert sig is not None
    assert sig.strategy_id == "statarb_eur_chf"
    assert sig.symbol == "EURCHF"
    assert sig.side == "BUY"
    assert sig.is_two_leg is True
    assert sig.secondary_symbol == "USDCHF"
    assert sig.hedge_ratio == 0.88
    assert sig.gross_expected_edge_bps >= 25.0
    assert sig.execution_authority is False


def test_kalman_statarb_veto_conditions():
    strat = KalmanStatArbStrategy()
    np.random.seed(42)
    residuals = [float(x) for x in np.random.normal(0, 1, 30)]

    # Rollover veto
    assert strat.evaluate_opportunity({
        "is_rollover_window": True,
        "bocpd_changepoint_prob": 0.1,
        "residuals": residuals,
        "z_score": -2.5,
    }) is None

    # BOCPD change-point veto
    assert strat.evaluate_opportunity({
        "is_rollover_window": False,
        "bocpd_changepoint_prob": 0.70,
        "residuals": residuals,
        "z_score": -2.5,
    }) is None

    # Non-stationary / trending residuals (half-life > 48)
    trending_residuals = list(range(40))  # strictly explosive/trending
    assert strat.evaluate_opportunity({
        "is_rollover_window": False,
        "bocpd_changepoint_prob": 0.1,
        "residuals": trending_residuals,
        "z_score": -2.5,
    }) is None


# ---------------------------------------------------------------------------
# 2. Cointegration Pair Trading Strategy Tests
# ---------------------------------------------------------------------------

def test_cointegration_pair_strategy():
    strat = CointegrationPairStrategy(strategy_id="coint_aud_nzd", symbol="AUDNZD")

    # Valid cointegration entry
    ctx_valid = {
        "adf_pvalue": 0.025,
        "rolling_corr": 0.82,
        "z_score": 2.10,
        "price": 1.0850,
        "atr": 0.0030,
    }
    sig = strat.evaluate_opportunity(ctx_valid)
    assert sig is not None
    assert sig.side == "SELL"
    assert sig.archetype == StrategyArchetype.PAIR_TRADING
    assert sig.stop_loss > sig.entry_price

    # ADF p-value too high
    assert strat.evaluate_opportunity({**ctx_valid, "adf_pvalue": 0.08}) is None

    # Rolling correlation breakdown (< 0.72)
    assert strat.evaluate_opportunity({**ctx_valid, "rolling_corr": 0.60}) is None


# ---------------------------------------------------------------------------
# 3. Avellaneda-Stoikov Mean Reversion Strategy Tests
# ---------------------------------------------------------------------------

def test_avellaneda_stoikov_mean_reversion():
    strat = AvellanedaStoikovMeanReversionStrategy(strategy_id="avellaneda_scalp_eurusd", symbol="EURUSD")

    ctx_valid = {
        "z_score": -2.20,
        "spread": 0.00010,
        "median_spread": 0.00009,
        "htf_trend_bias": "BUY",
        "price": 1.0500,
        "inventory_units": 0.0,
        "gamma": 0.5,
        "volatility": 0.0015,
        "horizon_hours": 0.25,
        "kappa": 1.5,
        "atr": 0.0010,
    }
    sig = strat.evaluate_opportunity(ctx_valid)
    assert sig is not None
    assert sig.side == "BUY"
    assert sig.archetype == StrategyArchetype.MEAN_REVERSION
    assert sig.style == ExecutionStyle.SCALP
    # Reservation price should be below or equal to mid for zero inventory
    assert "reservation_price" in sig.metadata

    # Opposing HTF trend bias
    assert strat.evaluate_opportunity({**ctx_valid, "htf_trend_bias": "SELL"}) is None

    # Blown out spread (> 1.35x median)
    assert strat.evaluate_opportunity({**ctx_valid, "spread": 0.00020, "median_spread": 0.00010}) is None


# ---------------------------------------------------------------------------
# 4. Macro Event Pullback Strategy Tests
# ---------------------------------------------------------------------------

def test_macro_event_pullback_strategy():
    strat = MacroEventPullbackStrategy(strategy_id="macro_nfp_pullback", symbol="EURUSD")

    ctx_valid = {
        "surprise_sigma": 2.4,  # Strong positive US surprise -> USD strength -> EURUSD SELL
        "minutes_since_release": 8.0,  # inside 3-25 min cool-down window
        "spread": 0.00012,
        "median_spread": 0.00010,  # 1.20x <= 1.45x
        "pullback_ratio": 0.45,  # between 0.25 and 0.70
        "price": 1.0520,
        "atr": 0.0025,
    }
    sig = strat.evaluate_opportunity(ctx_valid)
    assert sig is not None
    assert sig.side == "SELL"
    assert sig.gross_expected_edge_bps >= 35.0
    assert sig.archetype == StrategyArchetype.MACRO_EVENT

    # Too early (first 3 minutes slippage spike)
    assert strat.evaluate_opportunity({**ctx_valid, "minutes_since_release": 1.5}) is None

    # Too late (stale > 25 minutes)
    assert strat.evaluate_opportunity({**ctx_valid, "minutes_since_release": 35.0}) is None

    # Insufficient surprise sigma (< 1.5)
    assert strat.evaluate_opportunity({**ctx_valid, "surprise_sigma": 0.8}) is None

    # Spread still un-normalized (> 1.45x)
    assert strat.evaluate_opportunity({**ctx_valid, "spread": 0.00020, "median_spread": 0.00010}) is None


# ---------------------------------------------------------------------------
# 5. Kronos Neural Forecast Strategy Tests
# ---------------------------------------------------------------------------

def test_kronos_neural_forecast_strategy():
    strat = KronosNeuralForecastStrategy(strategy_id="kronos_h1", symbol="USDJPY")

    ctx_valid = {
        "predictive_entropy": 0.48,  # <= 0.62
        "prob_up": 0.74,  # >= 0.60
        "prob_down": 0.26,
        "price": 152.00,
        "atr": 0.45,
    }
    sig = strat.evaluate_opportunity(ctx_valid)
    assert sig is not None
    assert sig.side == "BUY"
    assert sig.confidence == 0.74
    assert sig.archetype == StrategyArchetype.NEURAL_FORECAST

    # High predictive entropy veto (> 0.62)
    assert strat.evaluate_opportunity({**ctx_valid, "predictive_entropy": 0.78}) is None

    # Low directional probability (< 0.60)
    assert strat.evaluate_opportunity({**ctx_valid, "prob_up": 0.53, "prob_down": 0.47}) is None


# ---------------------------------------------------------------------------
# 6. Multi-Factor Ensemble Strategy Tests
# ---------------------------------------------------------------------------

def test_multi_factor_ensemble_strategy():
    strat = MultiFactorEnsembleStrategy(strategy_id="factor_carry_trend_d1", symbol="USDJPY")

    ctx_valid = {
        "adx": 26.5,  # >= 22.0
        "momentum_z": 1.85,  # >= 1.5
        "daily_swap_bps": 0.25,  # >= -0.60
        "price": 152.00,
        "atr": 0.85,
    }
    sig = strat.evaluate_opportunity(ctx_valid)
    assert sig is not None
    assert sig.side == "BUY"
    assert sig.archetype == StrategyArchetype.MOMENTUM
    assert sig.style == ExecutionStyle.LONG_TERM

    # Negative carry swap veto (< -0.60 bps/day)
    assert strat.evaluate_opportunity({**ctx_valid, "daily_swap_bps": -0.85}) is None

    # Trendless ADX (< 22.0)
    assert strat.evaluate_opportunity({**ctx_valid, "adx": 16.0}) is None


# ---------------------------------------------------------------------------
# 7. Institutional Pre-Trade TCA Gate Tests
# ---------------------------------------------------------------------------

def test_pre_trade_tca_gate():
    gate = InstitutionalPreTradeTCAGate(min_edge_to_cost=2.50, min_net_edge_bps=2.0)

    sig = InstitutionalSignal(
        strategy_id="test_strat",
        symbol="EURUSD",
        archetype=StrategyArchetype.MACRO_EVENT,
        style=ExecutionStyle.SWING,
        side="SELL",
        entry_price=1.0500,
        stop_loss=1.0530,
        take_profit=1.0420,
        gross_expected_edge_bps=38.80,
        confidence=0.85,
        expected_holding_bars=12,
    )

    ctx = {
        "spread": 0.00010,  # ~0.95 bps
        "adv_notional_usd": 1_000_000_000.0,
        "volatility": 0.0070,
        "daily_swap_bps": 0.05,
        "day_of_week": 2,  # Wednesday
    }

    tca_res = gate.evaluate(sig, ctx, order_notional_usd=100_000.0)
    assert tca_res.passed is True
    assert tca_res.edge_to_cost_ratio >= 2.50
    assert tca_res.net_edge_bps > 20.0
    assert tca_res.spans_wednesday_rollover is True

    # Test rejection on tiny edge
    low_edge_sig = InstitutionalSignal(
        strategy_id="low_edge",
        symbol="EURUSD",
        archetype=StrategyArchetype.MEAN_REVERSION,
        style=ExecutionStyle.SCALP,
        side="BUY",
        entry_price=1.0500,
        stop_loss=1.0490,
        take_profit=1.0515,
        gross_expected_edge_bps=1.5,  # smaller than costs
        confidence=0.55,
        expected_holding_bars=2,
    )
    tca_low = gate.evaluate(low_edge_sig, ctx)
    assert tca_low.passed is False
    assert "LOW_EDGE_TO_COST" in str(tca_low.rejection_reason)


# ---------------------------------------------------------------------------
# 8. Currency Covariance Risk Sizer Tests
# ---------------------------------------------------------------------------

def test_currency_covariance_risk_sizer():
    sizer = CurrencyCovarianceRiskSizer(max_currency_risk_fraction=0.025, base_nav=100_000.0)

    sig = InstitutionalSignal(
        strategy_id="statarb_eur_chf",
        symbol="EURCHF",
        archetype=StrategyArchetype.STAT_ARB,
        style=ExecutionStyle.SWING,
        side="BUY",
        entry_price=0.9500,
        stop_loss=0.9470,
        take_profit=0.9560,
        gross_expected_edge_bps=26.0,
        confidence=0.72,
        expected_holding_bars=12,
    )

    # Empty portfolio -> should admit
    res = sizer.compute_size(sig, current_portfolio_exposures={}, market_context={"bocpd_changepoint_prob": 0.1, "copula_tail_risk_prob": 0.05})
    assert res.admitted is True
    assert 0.0025 <= res.target_risk_fraction <= 0.0150
    assert "EUR" in res.currency_exposures
    assert "CHF" in res.currency_exposures
    assert res.currency_exposures["EUR"] > 0
    assert res.currency_exposures["CHF"] < 0

    # Stacked factor breach: EUR already at +2.2% net risk -> adding another EUR buy breaches 2.5% cap
    full_eur = {"EUR": 0.023}
    res_breach = sizer.compute_size(sig, current_portfolio_exposures=full_eur, market_context={})
    assert res_breach.admitted is False
    assert "CURRENCY_FACTOR_CAP_EXCEEDED" in str(res_breach.rejection_reason)


# ---------------------------------------------------------------------------
# 9. Archetype Exit Manager Tests
# ---------------------------------------------------------------------------

def test_archetype_exit_manager_convergent():
    mgr = ArchetypeExitManager()

    sig = InstitutionalSignal(
        strategy_id="statarb_eur_chf",
        symbol="EURCHF",
        archetype=StrategyArchetype.STAT_ARB,
        style=ExecutionStyle.SWING,
        side="BUY",
        entry_price=0.9500,
        stop_loss=0.9470,
        take_profit=0.9560,
        gross_expected_edge_bps=25.0,
        confidence=0.75,
        expected_holding_bars=15,
    )

    # Mean reversion achieved (|z| <= 0.20)
    eval_mr = mgr.evaluate_exit(sig, current_price=0.9530, holding_bars=6, market_context={"z_score": 0.12})
    assert eval_mr.action == "FULL_EXIT"
    assert eval_mr.exit_type == "MEAN_REVERSION_COMPLETE"

    # BOCPD regime break (>= 0.75)
    eval_break = mgr.evaluate_exit(sig, current_price=0.9510, holding_bars=4, market_context={"z_score": 1.2, "bocpd_changepoint_prob": 0.82})
    assert eval_break.action == "FULL_EXIT"
    assert eval_break.exit_type == "REGIME_BREAK"

    # 3x half-life time decay stop
    eval_time = mgr.evaluate_exit(sig, current_price=0.9505, holding_bars=50, market_context={"z_score": 1.5, "ou_half_life": 12.0})
    assert eval_time.action == "FULL_EXIT"
    assert eval_time.exit_type == "TIME_STOP"


def test_archetype_exit_manager_divergent():
    mgr = ArchetypeExitManager()

    sig = InstitutionalSignal(
        strategy_id="macro_nfp_pullback",
        symbol="EURUSD",
        archetype=StrategyArchetype.MACRO_EVENT,
        style=ExecutionStyle.EVENT,
        side="BUY",
        entry_price=1.0500,
        stop_loss=1.0450,  # 1R = 50 pips
        take_profit=1.0650,
        gross_expected_edge_bps=38.0,
        confidence=0.85,
        expected_holding_bars=10,
    )

    # Current price at +1.6R (1.0580) -> should trigger 33% scale-out & move SL to break-even (+1 pip = 1.0501)
    eval_partial = mgr.evaluate_exit(sig, current_price=1.0580, holding_bars=5, market_context={"atr": 0.0050}, is_partial_already_taken=False)
    assert eval_partial.action == "PARTIAL_SCALE_OUT"
    assert eval_partial.scale_out_pct == 0.33
    assert eval_partial.new_stop_loss == 1.0501


# ---------------------------------------------------------------------------
# 10. Master Tournament Orchestrator Integration Test
# ---------------------------------------------------------------------------

def test_master_tournament_orchestrator():
    engine = InstitutionalStrategyTCAEngine()

    # Generate stationary mean-reverting OU residuals for Kalman Stat-Arb
    np.random.seed(42)
    ou_res = []
    curr_ou = 0.0
    for _ in range(50):
        curr_ou = curr_ou * 0.7 + np.random.normal(0, 0.5)
        ou_res.append(curr_ou)

    market_contexts = {
        # 1. Macro NFP Pullback on EURUSD
        "macro_nfp_pullback": {
            "surprise_sigma": 2.5,
            "minutes_since_release": 10.0,
            "spread": 0.00010,
            "median_spread": 0.00010,
            "pullback_ratio": 0.40,
            "price": 1.0500,
            "atr": 0.0025,
            "adv_notional_usd": 1_000_000_000.0,
            "volatility": 0.0070,
            "daily_swap_bps": 0.05,
            "day_of_week": 2,
        },
        # 2. Avellaneda-Stoikov on EURUSD (asset collision with NFP Pullback!)
        "avellaneda_scalp_eurusd": {
            "z_score": 2.10,
            "spread": 0.00010,
            "median_spread": 0.00010,
            "htf_trend_bias": "SELL",
            "price": 1.0500,
            "inventory_units": 0.0,
            "gamma": 0.5,
            "volatility": 0.0015,
            "horizon_hours": 0.25,
            "kappa": 1.5,
            "atr": 0.0010,
            "adv_notional_usd": 1_000_000_000.0,
        },
        # 3. Kronos Neural on USDJPY
        "kronos_h1": {
            "predictive_entropy": 0.45,
            "prob_up": 0.78,
            "prob_down": 0.22,
            "price": 152.00,
            "atr": 0.45,
            "spread": 0.015,
            "adv_notional_usd": 800_000_000.0,
            "volatility": 0.0080,
            "daily_swap_bps": 0.20,
            "day_of_week": 2,
        },
        # 4. Factor Momentum on USDJPY (asset collision with Kronos on USDJPY!)
        "factor_carry_trend_d1": {
            "adx": 25.0,
            "momentum_z": 1.60,
            "daily_swap_bps": 0.20,
            "price": 152.00,
            "atr": 0.85,
            "spread": 0.015,
            "adv_notional_usd": 800_000_000.0,
            "volatility": 0.0080,
            "day_of_week": 2,
        },
        # 5. Kalman Stat-Arb on EURCHF
        "statarb_eur_chf": {
            "is_rollover_window": False,
            "bocpd_changepoint_prob": 0.10,
            "residuals": ou_res,
            "z_score": -2.25,
            "beta": 0.88,
            "price": 0.9500,
            "atr": 0.0020,
            "spread": 0.00012,
            "secondary_spread": 0.00010,
            "secondary_price": 0.9000,
            "adv_notional_usd": 400_000_000.0,
            "volatility": 0.0050,
            "daily_swap_bps": -0.05,
            "day_of_week": 2,
        },
        # 6. Cointegration Pair on AUDNZD
        "coint_aud_nzd": {
            "adf_pvalue": 0.03,
            "rolling_corr": 0.80,
            "z_score": 1.95,
            "price": 1.0850,
            "atr": 0.0030,
            "spread": 0.00018,
            "adv_notional_usd": 300_000_000.0,
            "volatility": 0.0060,
            "daily_swap_bps": 0.00,
            "day_of_week": 2,
        },
    }

    # Run tournament with 0 current exposure
    tournament = engine.run_tournament(market_contexts)

    assert tournament["execution_authority"] is False
    admitted = tournament["admitted_trades"]
    rejected = tournament["rejected_trades"]

    admitted_ids = [t["strategy_id"] for t in admitted]
    rejected_reasons = {r["strategy_id"]: r["reason"] for r in rejected}

    # Macro NFP Pullback should win EURUSD over Avellaneda
    assert "macro_nfp_pullback" in admitted_ids
    assert "avellaneda_scalp_eurusd" in rejected_reasons
    assert "OUTSCORED_ON_ASSET" in rejected_reasons["avellaneda_scalp_eurusd"]

    # Kronos Neural Forecast should win USDJPY over Factor Carry Trend
    assert "kronos_h1" in admitted_ids
    assert "factor_carry_trend_d1" in rejected_reasons
    assert "OUTSCORED_ON_ASSET" in rejected_reasons["factor_carry_trend_d1"]

    # Kalman StatArb should be admitted for EURCHF
    assert "statarb_eur_chf" in admitted_ids

    # Cointegration AUDNZD should be held back by SWING sleeve exhaustion
    assert "coint_aud_nzd" in rejected_reasons
    assert "MARGIN_SLEEVE_EXHAUSTED" in rejected_reasons["coint_aud_nzd"]

    # Currency exposures must respect the <= 2.5% cap
    for ccy, exp in tournament["updated_currency_exposures"].items():
        assert abs(exp) <= 0.025, f"Currency {ccy} exposure {exp} exceeded 2.5% cap"
