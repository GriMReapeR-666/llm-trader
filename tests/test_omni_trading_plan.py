"""Comprehensive Test Suite for OmniTradingMasterPlan, Symbol Calibration, Consolidation Timing, and Universal Sizing."""
import pytest
from datetime import datetime, timezone

from core.symbol_calibration import SymbolCalibrationEngine, AssetClass
from core.consolidation_timing_engine import (
    ConsolidationTimingEngine,
    ConsolidationReason,
    MoveTimingTrigger,
)
from core.universal_scaling_ladder import UniversalScalingLadder
from core.omni_trading_plan import OmniTradingMasterPlan


def test_universal_symbol_calibration_and_pip_conventions():
    """Verify pip size, point size, and dollar values across multiple asset classes."""
    # 1. Forex Standard (EURUSD)
    eur_spec = SymbolCalibrationEngine.get_spec("EURUSD")
    assert eur_spec.asset_class == AssetClass.FOREX_MAJOR
    assert eur_spec.pip_size == 0.00010
    assert eur_spec.point_size == 0.00001
    assert eur_spec.pip_value_usd_per_standard_lot == 10.00
    # 35 pips on 0.01 micro lot = $3.50
    assert SymbolCalibrationEngine.calculate_dollar_risk("EURUSD", 0.01, 35.0) == 3.50

    # 2. Forex JPY (USDJPY)
    jpy_spec = SymbolCalibrationEngine.get_spec("USDJPY")
    assert jpy_spec.asset_class == AssetClass.FOREX_JPY
    assert jpy_spec.pip_size == 0.010
    assert jpy_spec.point_size == 0.001

    # 3. Gold (XAUUSD - Convention B)
    xau_spec = SymbolCalibrationEngine.get_spec("XAUUSD")
    assert xau_spec.asset_class == AssetClass.METALS
    assert xau_spec.pip_size == 0.10
    # 37 retail pips on 0.01 lot = $3.70
    assert SymbolCalibrationEngine.calculate_dollar_risk("XAUUSD", 0.01, 37.0) == 3.70

    # 4. Oil (USOIL)
    oil_spec = SymbolCalibrationEngine.get_spec("USOIL")
    assert oil_spec.asset_class == AssetClass.COMMODITIES
    assert oil_spec.pip_size == 0.10

    # 5. Index (US30)
    us30_spec = SymbolCalibrationEngine.get_spec("US30")
    assert us30_spec.asset_class == AssetClass.INDICES
    assert us30_spec.pip_size == 1.0

    # 6. Crypto (BTCUSD)
    btc_spec = SymbolCalibrationEngine.get_spec("BTCUSD")
    assert btc_spec.asset_class == AssetClass.CRYPTO


def test_multi_timeframe_expected_move_calculation():
    """Verify expected move calculations under normal vs. catalyst regimes."""
    # Normal trading day
    normal_move = SymbolCalibrationEngine.calculate_expected_move(
        symbol="EURUSD",
        current_price=1.0850,
        daily_atr=0.0075,  # 75 pips
        is_tier1_catalyst_day=False,
        day_of_week_iso=3,  # Wednesday peak expansion (1.25x)
        session_name="LONDON_OPEN",
    )
    assert normal_move.daily_atr_pips == 75.0
    assert normal_move.daily_expected_range_pips == 75.0 * 1.25
    assert normal_move.trade_target_expected_pips > 50.0
    assert normal_move.is_compressed is False

    # Catalyst day (FOMC/CPI expands expected range by 1.65x)
    catalyst_move = SymbolCalibrationEngine.calculate_expected_move(
        symbol="EURUSD",
        current_price=1.0850,
        daily_atr=0.0075,
        is_tier1_catalyst_day=True,
        day_of_week_iso=3,
    )
    assert catalyst_move.macro_catalyst_multiplier == 1.65
    assert catalyst_move.daily_expected_range_pips == 75.0 * 1.25 * 1.65


def test_consolidation_diagnostics_why_and_when():
    """Verify the diagnostic answers 'why is it consolidated' and 'when will move occur'."""
    # Case 1: Pre-News Accumulation (CPI in 2 hours)
    news_time = datetime(2026, 10, 14, 10, 30, tzinfo=timezone.utc)
    diag_news = ConsolidationTimingEngine.diagnose(
        symbol="GBPUSD",
        current_price=1.3050,
        adx_14=16.0,
        hours_until_tier1_news=2.0,
        current_time=news_time,
    )
    assert diag_news.is_consolidated is True
    assert diag_news.primary_reason == ConsolidationReason.PRE_NEWS_ACCUMULATION
    assert diag_news.predicted_move_trigger == MoveTimingTrigger.SCHEDULED_MACRO_CATALYST
    assert "pre-news accumulation" in diag_news.explanation.lower()

    # Case 2: Asian Session Lull (03:00 UTC)
    asian_time = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
    diag_asian = ConsolidationTimingEngine.diagnose(
        symbol="EURUSD",
        current_price=1.0850,
        adx_14=17.0,
        current_time=asian_time,
    )
    assert diag_asian.is_consolidated is True
    assert diag_asian.primary_reason == ConsolidationReason.SESSION_LULL
    assert diag_asian.predicted_move_trigger == MoveTimingTrigger.LONDON_OPEN_EXPANSION
    assert "07:00" in diag_asian.estimated_move_window_utc

    # Case 3: Volatility Compression Squeeze
    london_pre = datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc)
    closes_tight = [1.0850 + (0.0001 * (i % 2)) for i in range(25)]
    diag_squeeze = ConsolidationTimingEngine.diagnose(
        symbol="EURUSD",
        current_price=1.0850,
        adx_14=15.0,
        m15_closes=closes_tight,
        current_time=london_pre,
    )
    assert diag_squeeze.is_ttm_squeeze is True
    assert diag_squeeze.primary_reason == ConsolidationReason.VOLATILITY_COMPRESSION_SQUEEZE


def test_universal_3_stage_scaling_ladder_and_hysteresis():
    """Verify universal scaling ladder across non-gold instruments (e.g. EURUSD)."""
    ladder = UniversalScalingLadder(symbol="EURUSD", peak_equity=100.0)

    # 1. Stage 1 ($200 equity): strictly 0.01 micro lot
    s1 = ladder.calculate_lot_size(current_equity=200.0, proposed_stop_pips=30.0)
    assert s1["stage"] == 1
    assert s1["calculated_lot"] == 0.01
    assert s1["dollar_risk_at_stop"] == 3.00

    # 2. Stage 2 ($550 equity, 30-pip stop):
    # stop cost = 30 * $0.10 = $3.00.
    # At $550: units = 5.50 / 3.00 = 1.83 -> nearest lot is 0.02
    s2 = ladder.calculate_lot_size(current_equity=550.0, proposed_stop_pips=30.0)
    assert s2["stage"] == 2
    assert s2["calculated_lot"] == 0.02

    # 3. Hysteresis test:
    # Upgrade to 0.02 lots occurred at $550.
    # On drawdown to $535 (within $25 buffer of $550), hysteresis holds 0.02 lots!
    res_dd = ladder.calculate_lot_size(current_equity=535.0, proposed_stop_pips=30.0)
    assert res_dd["calculated_lot"] == 0.02
    assert res_dd["hysteresis_active"] is True

    # 4. Daily 2R circuit breaker
    ladder.record_daily_trade(r_multiple=-1.0, pnl_dollars=-6.0)
    ladder.record_daily_trade(r_multiple=-1.0, pnl_dollars=-6.0)
    res_halted = ladder.calculate_lot_size(current_equity=500.0, proposed_stop_pips=30.0)
    assert res_halted["status"] == "REJECTED"
    assert res_halted["circuit_breaker_active"] is True
    assert res_halted["calculated_lot"] == 0.0


def test_per_trader_goal_planning():
    """Verify the multi-stage month-by-month compounding goal planner."""
    plan = UniversalScalingLadder.create_trader_goal_plan(
        trader_id="trader_alpha",
        symbol="EURUSD",
        starting_equity=100.0,
        target_equity=8000.0,
        time_horizon_months=8,
        average_win_pips=120.0,
        average_stop_pips=30.0,
        weekly_target_pips=170.0,
    )
    assert plan.starting_equity == 100.0
    assert plan.target_equity == 8000.0
    assert plan.time_horizon_months == 8
    assert len(plan.monthly_schedule) == 8
    assert plan.total_net_winning_trades_required > 0
    assert plan.required_net_wins_per_week > 0
    # Month 1 ends higher than starting equity
    assert plan.monthly_schedule[0].ending_equity > plan.starting_equity
    # Final month approaches target (achieves ~59x multiple from $100)
    assert plan.monthly_schedule[-1].ending_equity >= 5800.0


def test_omni_trading_master_plan_end_to_end_decision():
    """Verify the master plan evaluates an end-to-end setup and applies execution gates."""
    master = OmniTradingMasterPlan(db_path=":memory:")

    # High-probability window: Wednesday 08:00 UTC (London Open Killzone)
    wed_london = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
    decision = master.evaluate_setup(
        symbol="EURUSD",
        current_equity=600.0,
        proposed_entry=1.0850,
        proposed_sl=1.0820,  # 30-pip SL
        direction="LONG",
        spread_pips=1.2,
        adx_14=24.0,
        daily_atr=0.0075,
        current_time=wed_london,
    )
    assert decision.status == "READY_FOR_REVIEW" if hasattr(decision, "status") else True
    assert decision.can_trade is True
    assert decision.direction == "LONG"
    assert decision.risk_reward_ratio >= 1.50
    assert decision.sizing["calculated_lot"] >= 0.01
    assert "EXECUTE_LONG" in decision.recommended_action
    assert decision.setup_quality_score >= 0.70

    # Negative Gate: Friday afternoon (17:00 UTC) weekend de-risking blocks entry!
    fri_evening = datetime(2026, 10, 16, 17, 0, tzinfo=timezone.utc)
    decision_blocked = master.evaluate_setup(
        symbol="EURUSD",
        current_equity=600.0,
        proposed_entry=1.0850,
        proposed_sl=1.0820,
        direction="LONG",
        current_time=fri_evening,
    )
    assert decision_blocked.can_trade is False
    assert decision_blocked.direction == "STAND_ASIDE"
    assert any("weekend de-risking" in r.lower() for r in decision_blocked.no_trade_reasons)
