"""Comprehensive test suite for the GoldScalingLadder micro-account scaling engine."""
import pytest
from datetime import datetime, timezone
from core.gold_scaling_ladder import (
    GoldScalingLadder,
    GOLD_RUNGS,
    FRICTION_PIPS,
    MAX_SPREAD_POINTS,
    MIN_ADX_THRESHOLD,
    MIN_DAILY_ATR_DOLLARS,
    STAGE_2A_TP_EXTENSION_PIPS,
    STANDARD_TP_PIPS,
    HYSTERESIS_BUFFER_DOLLARS,
)


def test_gold_scaling_rungs_mapping():
    ladder = GoldScalingLadder(peak_equity=100.0)

    # Stage 1: Under $500
    rung1 = ladder.get_active_rung(250.0)
    assert rung1.rung_id == 1
    assert rung1.base_lots == 0.01

    # Stage 2a: $500 - $554
    rung2 = ladder.get_active_rung(525.0)
    assert rung2.rung_id == 2
    assert rung2.base_lots == 0.01

    # Stage 2b: $555 - $924
    rung3 = ladder.get_active_rung(555.0)
    assert rung3.rung_id == 3
    assert rung3.base_lots == 0.02

    # Stage 3: $925 - $1,294
    rung4 = ladder.get_active_rung(1000.0)
    assert rung4.rung_id == 4
    assert rung4.base_lots == 0.03

    # Stage 3: $2,035 - $3,144 (Rung 7)
    rung7 = ladder.get_active_rung(2500.0)
    assert rung7.rung_id == 7
    assert rung7.base_lots == 0.06

    # Stage 3: $5,000 - $7,999 (Rung 9)
    rung9 = ladder.get_active_rung(6000.0)
    assert rung9.rung_id == 9
    assert rung9.base_lots == 0.14

    # Stage 3: Bonus Milestone $8,000+ (Rung 10)
    rung10 = ladder.get_active_rung(8500.0)
    assert rung10.rung_id == 10
    assert rung10.base_lots == 0.22


def test_stage1_fixed_micro_lot():
    ladder = GoldScalingLadder(peak_equity=100.0)
    res = ladder.calculate_lot_size(current_equity=200.0, proposed_stop_pips=32.0)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["stage"] == 1
    assert res["stage_name"] == "STAGE_1_LINEAR_GROWTH"
    assert res["calculated_lot"] == 0.01
    assert res["rule_applied"] == "PHASE_1_FIXED_MICRO_LOT"
    assert res["recommended_tp_pips"] == STANDARD_TP_PIPS
    assert res["tp_extension_active"] is False
    assert res["execution_authority"] is False


def test_stage2a_tp_extension_zone():
    ladder = GoldScalingLadder(peak_equity=500.0)
    # At $520 with standard 37-pip stop ($3.70):
    # units = 5.20 / 3.70 = 1.405 -> nearest lot is 0.01
    res = ladder.calculate_lot_size(current_equity=520.0, proposed_stop_pips=37.0)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["stage"] == 2
    assert res["stage_name"] == "STAGE_2_MICRO_LOT_BRIDGE"
    assert res["calculated_lot"] == 0.01
    assert res["recommended_tp_pips"] == STAGE_2A_TP_EXTENSION_PIPS  # 185 pips ($18.50)
    assert res["tp_extension_active"] is True
    assert res["rule_applied"] == "STAGE_2A_TP_EXTENSION"


def test_stage2b_nearest_step_rounding_and_risk_cap():
    ladder = GoldScalingLadder(peak_equity=500.0)

    # 1. At $554 with 37-pip stop: units = 5.54 / 3.70 = 1.497 -> rounds to 0.01
    res_554 = ladder.calculate_lot_size(current_equity=554.0, proposed_stop_pips=37.0)
    assert res_554["calculated_lot"] == 0.01
    assert res_554["tp_extension_active"] is True

    # 2. At $555 with 37-pip stop: units = 5.55 / 3.70 = 1.500 -> rounds to 0.02
    # Risk = 0.02 * $3.70 = $7.40. $7.40 / 555 = 1.33% (allowed under 1.33% cap)
    res_555 = ladder.calculate_lot_size(current_equity=555.0, proposed_stop_pips=37.0)
    assert res_555["status"] == "READY_FOR_REVIEW"
    assert res_555["calculated_lot"] == 0.02
    assert res_555["tp_extension_active"] is False
    assert "STEP_DELAY_ELIMINATED" in res_555["rule_applied"]

    # 3. Adaptive 30-pip pullback stop at $600:
    # units = 6.00 / 3.00 = 2.0 -> 0.02 lots at exactly 1.00% risk!
    res_600 = ladder.calculate_lot_size(current_equity=600.0, proposed_stop_pips=30.0)
    assert res_600["calculated_lot"] == 0.02

    # 4. Adaptive 30-pip pullback stop at $900:
    # units = 9.00 / 3.00 = 3.0 -> 0.03 lots at exactly 1.00% risk!
    res_900 = ladder.calculate_lot_size(current_equity=900.0, proposed_stop_pips=30.0)
    assert res_900["calculated_lot"] == 0.03


def test_phase2_step_delay_eliminated_adaptive():
    ladder = GoldScalingLadder(peak_equity=500.0)
    # At $550 with adaptive 25-pip stop ($2.50):
    # units = 5.50 / 2.50 = 2.20 -> 0.02 lots
    res = ladder.calculate_lot_size(current_equity=550.0, proposed_stop_pips=25.0)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["calculated_lot"] == 0.02
    assert "STEP_DELAY_ELIMINATED" in res["rule_applied"]


def test_stage3_dynamic_compounding_ladder_milestones():
    ladder = GoldScalingLadder(peak_equity=100.0)

    # $925 -> 0.03 lots (with 37-pip SL: 9.25 / 3.70 = 2.50 -> 0.03)
    res_925 = ladder.calculate_lot_size(current_equity=925.0, proposed_stop_pips=37.0)
    assert res_925["calculated_lot"] == 0.03

    # $1,295 -> 0.04 lots (12.95 / 3.70 = 3.50 -> 0.04)
    res_1295 = ladder.calculate_lot_size(current_equity=1295.0, proposed_stop_pips=37.0)
    assert res_1295["calculated_lot"] == 0.04

    # $1,665 -> 0.05 lots (16.65 / 3.70 = 4.50 -> 0.05)
    res_1665 = ladder.calculate_lot_size(current_equity=1665.0, proposed_stop_pips=37.0)
    assert res_1665["calculated_lot"] == 0.05

    # $2,035 -> 0.06 lots (20.35 / 3.70 = 5.50 -> 0.06)
    res_2035 = ladder.calculate_lot_size(current_equity=2035.0, proposed_stop_pips=37.0)
    assert res_2035["calculated_lot"] == 0.06

    # $3,145 -> 0.09 lots (31.45 / 3.70 = 8.50 -> 0.09)
    res_3145 = ladder.calculate_lot_size(current_equity=3145.0, proposed_stop_pips=37.0)
    assert res_3145["calculated_lot"] == 0.09

    # $5,000 -> 0.14 lots (50.00 / 3.70 = 13.51 -> 0.14)
    res_5000 = ladder.calculate_lot_size(current_equity=5000.0, proposed_stop_pips=37.0)
    assert res_5000["calculated_lot"] == 0.14

    # $8,000 -> 0.22 lots (80.00 / 3.70 = 21.62 -> 0.22)
    res_8000 = ladder.calculate_lot_size(current_equity=8000.0, proposed_stop_pips=37.0)
    assert res_8000["calculated_lot"] == 0.22

    # $10,000 -> 0.27 lots (100.00 / 3.70 = 27.02 -> 0.27)
    res_10000 = ladder.calculate_lot_size(current_equity=10000.0, proposed_stop_pips=37.0)
    assert res_10000["calculated_lot"] == 0.27


def test_hysteresis_band_drawdown_protection():
    ladder = GoldScalingLadder(peak_equity=500.0)

    # 1. Upgrade to 0.02 lots at $555
    res_upgrade = ladder.calculate_lot_size(current_equity=555.0, proposed_stop_pips=37.0)
    assert res_upgrade["calculated_lot"] == 0.02
    assert res_upgrade["hysteresis_active"] is False

    # 2. Drawdown to $540 (within $25 buffer of $555):
    # Without hysteresis, $540 would downgrade to 0.01 lot.
    # With $25 hysteresis buffer (downgrade threshold = $530), lot remains 0.02!
    res_dd1 = ladder.calculate_lot_size(current_equity=540.0, proposed_stop_pips=37.0)
    assert res_dd1["calculated_lot"] == 0.02
    assert res_dd1["hysteresis_active"] is True

    # 3. Further drawdown to $525 (below $530 threshold):
    # Triggers tier step-down to 0.01 lot
    res_dd2 = ladder.calculate_lot_size(current_equity=525.0, proposed_stop_pips=37.0)
    assert res_dd2["calculated_lot"] == 0.01
    assert res_dd2["hysteresis_active"] is False

    # 4. Partial bounce to $545 does NOT prematurely re-upgrade (must reclaim $555)
    res_bounce = ladder.calculate_lot_size(current_equity=545.0, proposed_stop_pips=37.0)
    assert res_bounce["calculated_lot"] == 0.01
    assert res_bounce["hysteresis_active"] is False

    # 5. Full reclaim of $555 unlocks 0.02 lots again
    res_reclaim = ladder.calculate_lot_size(current_equity=555.0, proposed_stop_pips=37.0)
    assert res_reclaim["calculated_lot"] == 0.02


def test_daily_2r_circuit_breaker():
    ladder = GoldScalingLadder(peak_equity=1000.0)

    # 1. Normal state: circuit breaker inactive
    res_normal = ladder.calculate_lot_size(current_equity=1000.0, proposed_stop_pips=37.0)
    assert res_normal["status"] == "READY_FOR_REVIEW"
    assert res_normal["circuit_breaker_active"] is False

    # 2. First losing trade (-1.0R): counter = 1, breaker still inactive
    t1 = ladder.record_daily_trade(r_multiple=-1.0, pnl_dollars=-10.0)
    assert t1["daily_losses_count"] == 1
    assert t1["circuit_breaker_active"] is False

    # Sizing still allowed after 1 loss
    res_after_1 = ladder.calculate_lot_size(current_equity=990.0, proposed_stop_pips=37.0)
    assert res_after_1["status"] == "READY_FOR_REVIEW"

    # 3. Second losing trade (-1.0R): counter = 2 -> circuit breaker trips!
    t2 = ladder.record_daily_trade(r_multiple=-1.0, pnl_dollars=-10.0)
    assert t2["daily_losses_count"] == 2
    assert t2["circuit_breaker_active"] is True

    # Sizing now REJECTED
    res_halted = ladder.calculate_lot_size(current_equity=980.0, proposed_stop_pips=37.0)
    assert res_halted["status"] == "REJECTED"
    assert res_halted["circuit_breaker_active"] is True
    assert res_halted["calculated_lot"] == 0.0
    assert "Circuit Breaker active" in res_halted["reason"]

    # 4. Daily reset restores trading
    ladder.reset_daily_circuit_breaker()
    res_restored = ladder.calculate_lot_size(current_equity=980.0, proposed_stop_pips=37.0)
    assert res_restored["status"] == "READY_FOR_REVIEW"
    assert res_restored["circuit_breaker_active"] is False
    assert res_restored["calculated_lot"] > 0.0


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
