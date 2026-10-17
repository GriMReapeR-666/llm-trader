import pytest
from datetime import datetime, timezone, timedelta
from core.execution_planning import (
    build_rotation_action,
    build_two_leg_pair_entry,
    RotationAction,
    TwoLegPlannedEntry,
)
from core.order_execution import (
    PaperBroker,
    execute_rotation_action,
    execute_two_leg_pair,
)


def test_rotation_action_success():
    broker = PaperBroker()
    # Create an initial position to rotate away from
    open_res = broker.market_order("EURUSD", "BUY", 0.1, 1.0850, 1.0800, 1.0950, "initial", magic=111)
    ticket = open_res["order"]
    assert ticket in broker.positions

    action = build_rotation_action(
        decayed_ticket=ticket,
        decayed_symbol="EURUSD",
        decayed_side="BUY",
        decayed_volume=0.1,
        decayed_magic=111,
        new_symbol="GBPUSD",
        new_side="BUY",
        new_volume=0.15,
        new_entry=1.2750,
        new_sl=1.2700,
        new_tp=1.2850,
        magic=222,
        reason="momentum_decay",
    )

    result = execute_rotation_action(action, paper=broker)
    assert result["success"] is True
    assert result["step"] == "complete"
    # Old position must be closed (freed sleeve margin)
    assert ticket not in broker.positions
    # New position must be open
    new_ticket = result["entry_result"]["order"]
    assert new_ticket in broker.positions
    assert broker.positions[new_ticket]["symbol"] == "GBPUSD"
    assert broker.positions[new_ticket]["volume"] == 0.15


def test_rotation_action_fails_if_decayed_exit_fails():
    broker = PaperBroker()
    # Pass a non-existent decayed ticket
    action = build_rotation_action(
        decayed_ticket=999999,
        decayed_symbol="EURUSD",
        decayed_side="BUY",
        decayed_volume=0.1,
        decayed_magic=111,
        new_symbol="GBPUSD",
        new_side="BUY",
        new_volume=0.15,
        new_entry=1.2750,
        new_sl=1.2700,
        new_tp=1.2850,
        magic=222,
    )

    result = execute_rotation_action(action, paper=broker)
    assert result["success"] is False
    assert result["step"] == "exit_decayed"
    # Crucial: New position must NEVER be entered if exit failed
    assert len(broker.positions) == 0


def test_two_leg_pair_success():
    broker = PaperBroker()
    pair_plan = build_two_leg_pair_entry(
        leg1_symbol="EURUSD",
        leg1_side="BUY",
        leg1_volume=0.10,
        leg1_entry=1.0850,
        leg1_sl=1.0750,
        leg1_tp=1.0950,
        leg2_symbol="GBPUSD",
        hedge_ratio=1.2,
        leg2_entry=1.2750,
        leg2_sl=1.2850,
        leg2_tp=1.2650,
        magic=333,
    )

    assert pair_plan.leg2_side == "SELL"
    assert pair_plan.leg2_volume == 0.12

    result = execute_two_leg_pair(pair_plan, paper=broker, order_type="MARKET")
    assert result["success"] is True
    assert result["unwound"] is False

    l1_ticket = result["leg1_result"]["order"]
    l2_ticket = result["leg2_result"]["order"]
    assert l1_ticket in broker.positions
    assert l2_ticket in broker.positions
    assert broker.positions[l1_ticket]["symbol"] == "EURUSD"
    assert broker.positions[l2_ticket]["symbol"] == "GBPUSD"


def test_two_leg_pair_auto_unwinds_leg1_when_leg2_fails():
    broker = PaperBroker()
    # Force Leg 2 to fail by passing invalid price (e.g. <= 0)
    pair_plan = build_two_leg_pair_entry(
        leg1_symbol="EURUSD",
        leg1_side="BUY",
        leg1_volume=0.10,
        leg1_entry=1.0850,
        leg1_sl=1.0750,
        leg1_tp=1.0950,
        leg2_symbol="GBPUSD",
        hedge_ratio=1.0,
        leg2_entry=0.0,  # Invalid price causing market order failure in PaperBroker
        leg2_sl=1.2850,
        leg2_tp=1.2650,
        magic=333,
    )

    result = execute_two_leg_pair(pair_plan, paper=broker, order_type="MARKET")
    assert result["success"] is False
    assert result["step"] == "leg2_entry"
    assert result["unwound"] is True
    assert "unwound Leg 1" in result["error"]

    # Invariant: Leg 1 must have been automatically unwound, so NO naked positions left!
    l1_ticket = result["leg1_result"]["order"]
    assert l1_ticket not in broker.positions
    assert len(broker.positions) == 0
