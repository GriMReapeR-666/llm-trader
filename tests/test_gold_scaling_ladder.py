"""Test suite for the GoldScalingLadder micro-account scaling engine."""
import pytest
from core.gold_scaling_ladder import (
    GoldScalingLadder,
    GOLD_RUNGS,
    FRICTION_PIPS,
    MAX_SPREAD_POINTS,
    MIN_ADX_THRESHOLD,
    MIN_DAILY_ATR_DOLLARS,
)


def test_gold_scaling_rungs_mapping():
    ladder = GoldScalingLadder(peak_equity=100.0)
    
    # Phase 1: Under $500
    rung1 = ladder.get_active_rung(250.0)
    assert rung1.rung_id == 1
    assert rung1.base_lots == 0.01

    # Phase 2: Rung 2 ($500 - $699)
    rung2 = ladder.get_active_rung(550.0)
    assert rung2.rung_id == 2
    assert rung2.base_lots == 0.02

    # Rung 6: ($2,100 - $3,149)
    rung6 = ladder.get_active_rung(2500.0)
    assert rung6.rung_id == 6
    assert rung6.base_lots == 0.07

    # Rung 9: ($6,300+)
    rung9 = ladder.get_active_rung(7500.0)
    assert rung9.rung_id == 9
    assert rung9.base_lots == 0.19


def test_phase1_fixed_micro_lot():
    ladder = GoldScalingLadder(peak_equity=100.0)
    res = ladder.calculate_lot_size(current_equity=200.0, proposed_stop_pips=32.0)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["calculated_lot"] == 0.01
    assert res["rule_applied"] == "PHASE_1_FIXED_MICRO_LOT"
    assert res["execution_authority"] is False


def test_phase2_step_delay_eliminated():
    ladder = GoldScalingLadder(peak_equity=500.0)
    # At $500, standard static rounding yields 0.01.
    # Step-delay elimination yields 0.02 on adaptive 22-32 pip SL!
    res = ladder.calculate_lot_size(current_equity=550.0, proposed_stop_pips=25.0)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["calculated_lot"] == 0.02
    assert res["rule_applied"] == "PHASE_2_STEP_DELAY_ELIMINATED_RUNG_2"


def test_drawdown_throttle_and_recovery_ladder():
    ladder = GoldScalingLadder(peak_equity=2000.0)
    
    # 1. Normal state
    state = ladder.evaluate_drawdown_throttle(current_equity=1950.0)
    assert state["throttle_active"] is False
    assert state["effective_risk_pct"] == 1.00

    # 2. Drawdown >= 3.9% (e.g. at $1910, dd = 4.5%)
    state_dd = ladder.evaluate_drawdown_throttle(current_equity=1910.0)
    assert state_dd["throttle_active"] is True
    assert state_dd["effective_risk_pct"] == 0.50
    assert state_dd["recovery_step"] == 1

    # 3. Recovery Win 1 (+4.5R) -> step up to 0.75%
    rec1 = ladder.record_trade_outcome(r_multiple=4.5, current_equity=1950.0)
    assert rec1["throttle_active"] is True
    assert rec1["recovery_step"] == 2
    state_step_up = ladder.evaluate_drawdown_throttle(current_equity=1950.0)
    assert state_step_up["effective_risk_pct"] == 0.75

    # 4. Recovery Win 2 (+4.5R) -> clears HWM, restores 1.00%
    rec2 = ladder.record_trade_outcome(r_multiple=4.5, current_equity=2050.0)
    assert rec2["throttle_active"] is False
    assert rec2["recovery_step"] == 0
    state_restored = ladder.evaluate_drawdown_throttle(current_equity=2050.0)
    assert state_restored["throttle_active"] is False
    assert state_restored["effective_risk_pct"] == 1.00


def test_volatility_and_spread_filters():
    ladder = GoldScalingLadder(peak_equity=1000.0)
    
    # Spread > 25 points -> REJECT
    res_spread = ladder.calculate_lot_size(current_equity=1000.0, proposed_stop_pips=30.0, spread_points=35.0)
    assert res_spread["status"] == "REJECTED"
    assert "Spread" in res_spread["reason"]

    # ADX < 22 -> REJECT
    res_adx = ladder.calculate_lot_size(current_equity=1000.0, proposed_stop_pips=30.0, spread_points=15.0, adx_14=18.0)
    assert res_adx["status"] == "REJECTED"
    assert "ADX" in res_adx["reason"]

    # Daily ATR < $45 -> REJECT
    res_atr = ladder.calculate_lot_size(
        current_equity=1000.0, proposed_stop_pips=30.0, spread_points=15.0, adx_14=25.0, daily_atr_dollars=35.0
    )
    assert res_atr["status"] == "REJECTED"
    assert "Daily ATR" in res_atr["reason"]


def test_3_stage_dynamic_exits():
    ladder = GoldScalingLadder()
    
    # Stage 0: +50 pips (+5.00/oz move)
    s0 = ladder.evaluate_dynamic_exits(entry_price=2650.0, current_price=2655.0, direction="LONG", current_sl=2646.8)
    assert s0["active_stage"] == "STAGE_0_INITIAL_RISK"
    assert s0["partial_close_pct"] == 0.0

    # Stage 1: +100 pips (+10.00/oz move) -> 50% partial + Entry + 10 pips (+1.00)
    s1 = ladder.evaluate_dynamic_exits(entry_price=2650.0, current_price=2660.0, direction="LONG", current_sl=2646.8)
    assert s1["active_stage"] == "STAGE_1_FLOOR_LOCKED"
    assert s1["partial_close_pct"] == 0.50
    assert s1["proposed_sl"] == 2651.0  # 2650.0 + 1.00

    # Stage 2: +150 pips (+15.00/oz move) -> ratchet to +110 pips + Chandelier
    s2 = ladder.evaluate_dynamic_exits(
        entry_price=2650.0,
        current_price=2665.0,
        direction="LONG",
        current_sl=2651.0,
        m15_atr=3.0,
        m15_highest_high_10=2666.0,
    )
    assert s2["active_stage"] == "STAGE_2_RATCHET_ACTIVE"
    # Ratchet SL: 2650.0 + 11.00 = 2661.0. Chandelier: 2666.0 - (1.5 * 3.0) = 2661.5. Max: 2661.5.
    assert s2["proposed_sl"] == 2661.5

    # Stage 3: +250 pips (+25.00/oz move) -> uncapped runner
    s3 = ladder.evaluate_dynamic_exits(
        entry_price=2650.0,
        current_price=2675.0,
        direction="LONG",
        current_sl=2661.5,
        m15_atr=3.0,
        m15_highest_high_10=2676.0,
    )
    assert s3["active_stage"] == "STAGE_3_UNCAPPED_RUNNER"
