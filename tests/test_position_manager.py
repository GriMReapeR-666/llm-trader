"""Tests for core/position_manager.py — rule-based position management."""
from __future__ import annotations

import time
import pytest
from core.position_manager import (
    PositionManager, PositionManagerConfig, ManagementAction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    defaults = {
        "breakeven_r_mult": 1.0,
        "breakeven_atr_buffer": 0.1,
        "trailing_enabled": True,
        "trailing_atr_mult": 1.5,
        "trailing_min_profit_r": 0.5,
        "partial_close_enabled": True,
        "partial_close_levels": [(2.0, 0.50), (3.0, 0.50)],
        "time_stop_enabled": True,
        "time_stop_bars": 48,
        "time_stop_min_r": 0.2,
    }
    defaults.update(overrides)
    return PositionManagerConfig(**defaults)


def _make_manager(**config_overrides):
    return PositionManager(_make_config(**config_overrides))


# ---------------------------------------------------------------------------
# Breakeven tests
# ---------------------------------------------------------------------------

class TestBreakeven:
    def test_breakeven_moves_sl_to_entry_at_1r(self):
        """At +1R, SL should move to entry + buffer."""
        pm = _make_manager()
        # BUY @ 1.1000, SL @ 1.0950 (50 pip risk)
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Price at 1.1050 = +50 pips = +1R
        actions = pm.manage(current_price=1.1050, atr=0.0010)
        sl_actions = [a for a in actions if a.action == "MODIFY_SL"]
        assert len(sl_actions) == 1
        assert sl_actions[0].new_sl >= 1.1000  # At or above entry
        assert sl_actions[0].reason == "breakeven"

    def test_breakeven_only_moves_sl_up_for_buy(self):
        """Breakeven should never move SL down for BUY."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Price at 1.1050 = +1R, but SL already above entry
        pm.positions[1].current_sl = 1.1020  # Already above entry
        actions = pm.manage(current_price=1.1050, atr=0.0010)
        sl_actions = [a for a in actions if a.action == "MODIFY_SL"]
        # Should not move SL down from 1.1020
        for a in sl_actions:
            assert a.new_sl >= 1.1020

    def test_breakeven_sell_moves_sl_down(self):
        """For SELL, breakeven moves SL below entry."""
        pm = _make_manager()
        pm.register_position(ticket=2, symbol="EURUSD", side="SELL",
                             entry_price=1.1000, sl=1.1050, tp=1.0800,
                             volume=0.1, atr=0.0010)
        # Price at 1.0950 = +50 pips = +1R
        actions = pm.manage(current_price=1.0950, atr=0.0010)
        sl_actions = [a for a in actions if a.action == "MODIFY_SL"]
        assert len(sl_actions) == 1
        assert sl_actions[0].new_sl <= 1.1000  # At or below entry

    def test_breakeven_not_triggered_below_1r(self):
        """Below +1R, breakeven should not trigger."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Price at 1.1020 = +20 pips = +0.4R
        actions = pm.manage(current_price=1.1020, atr=0.0010)
        sl_actions = [a for a in actions if a.action == "MODIFY_SL"]
        assert len(sl_actions) == 0

    def test_breakeven_only_once(self):
        """Breakeven should only trigger once."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # First trigger
        pm.manage(current_price=1.1050, atr=0.0010)
        # Second call — no new breakeven action
        actions = pm.manage(current_price=1.1060, atr=0.0010)
        be_actions = [a for a in actions if a.reason == "breakeven"]
        assert len(be_actions) == 0


# ---------------------------------------------------------------------------
# Trailing stop tests
# ---------------------------------------------------------------------------

class TestTrailingStop:
    def test_trailing_follows_price_up(self):
        """Trailing stop should move SL up as price rises."""
        pm = _make_manager(trailing_min_profit_r=0.5)
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # First trigger breakeven
        pm.manage(current_price=1.1050, atr=0.0010)
        # Now price at 1.1100 = +1R, trailing should kick in
        actions = pm.manage(current_price=1.1100, atr=0.0010)
        trail_actions = [a for a in actions if a.reason == "trailing_stop"]
        assert len(trail_actions) >= 1
        # Trail should be behind price
        assert trail_actions[0].new_sl < 1.1100

    def test_trailing_never_moves_sl_down(self):
        """Trailing stop should never move SL down for BUY."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Move SL up first
        pm.manage(current_price=1.1050, atr=0.0010)  # breakeven
        pm.manage(current_price=1.1100, atr=0.0010)  # trail
        # Price drops — trailing should not move SL down
        actions = pm.manage(current_price=1.1080, atr=0.0010)
        sl_actions = [a for a in actions if a.action == "MODIFY_SL"]
        for a in sl_actions:
            assert a.new_sl >= pm.positions[1].current_sl or a.new_sl > 1.1000

    def test_trailing_disabled(self):
        """When trailing is disabled, no trailing actions."""
        pm = _make_manager(trailing_enabled=False)
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        pm.manage(current_price=1.1100, atr=0.0010)
        trail_actions = [a for a in pm.get_all_actions() if a.reason == "trailing_stop"]
        assert len(trail_actions) == 0

    def test_trailing_sell_follows_price_down(self):
        """For SELL, trailing should move SL down."""
        pm = _make_manager(trailing_min_profit_r=0.5)
        pm.register_position(ticket=2, symbol="EURUSD", side="SELL",
                             entry_price=1.1000, sl=1.1050, tp=1.0800,
                             volume=0.1, atr=0.0010)
        # First trigger breakeven at +1R
        pm.manage(current_price=1.0950, atr=0.0010)
        # Now price drops further to 1.0930 = +1.4R, trailing should fire
        actions = pm.manage(current_price=1.0930, atr=0.0010)
        trail_actions = [a for a in actions if a.reason == "trailing_stop"]
        assert len(trail_actions) >= 1
        assert trail_actions[0].new_sl > 1.0930  # Above current price


# ---------------------------------------------------------------------------
# Partial close tests
# ---------------------------------------------------------------------------

class TestPartialClose:
    def test_partial_close_at_2r(self):
        """At +2R, 50% should be closed."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Price at 1.1100 = +2R
        actions = pm.manage(current_price=1.1100, atr=0.0010)
        pc_actions = [a for a in actions if a.action == "PARTIAL_CLOSE"]
        assert len(pc_actions) == 1
        assert pc_actions[0].close_fraction == 0.50
        assert pc_actions[0].reason == "scale_out_2.0R"

    def test_partial_close_at_3r(self):
        """At +3R, another 50% of remainder should be closed."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Price at 1.1150 = +3R
        actions = pm.manage(current_price=1.1150, atr=0.0010)
        pc_actions = [a for a in actions if a.action == "PARTIAL_CLOSE"]
        assert len(pc_actions) == 2  # Both 2R and 3R triggered

    def test_partial_close_only_once_per_level(self):
        """Each partial close level should only trigger once."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        # Trigger 2R
        pm.manage(current_price=1.1100, atr=0.0010)
        # Same price again — no new partial close
        actions = pm.manage(current_price=1.1100, atr=0.0010)
        pc_actions = [a for a in actions if a.action == "PARTIAL_CLOSE"]
        assert len(pc_actions) == 0

    def test_partial_close_disabled(self):
        """When disabled, no partial closes."""
        pm = _make_manager(partial_close_enabled=False)
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        actions = pm.manage(current_price=1.1100, atr=0.0010)
        pc_actions = [a for a in actions if a.action == "PARTIAL_CLOSE"]
        assert len(pc_actions) == 0


# ---------------------------------------------------------------------------
# Time stop tests
# ---------------------------------------------------------------------------

class TestTimeStop:
    def test_time_stop_exits_stagnant_position(self):
        """After time_stop_bars with low R, position should be closed."""
        pm = _make_manager(time_stop_bars=10, time_stop_min_r=0.2)
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010, entry_bar=0)
        # 15 bars later, price barely moved (+0.1R)
        actions = pm.manage(current_price=1.1005, atr=0.0010, bar_number=15)
        close_actions = [a for a in actions if a.action == "FULL_CLOSE"]
        assert len(close_actions) == 1
        assert "time_stop" in close_actions[0].reason

    def test_time_stop_not_triggered_if_profitable(self):
        """Time stop should not trigger if position is profitable."""
        pm = _make_manager(time_stop_bars=10, time_stop_min_r=0.2)
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010, entry_bar=0)
        # 15 bars later, price moved +0.5R
        actions = pm.manage(current_price=1.1025, atr=0.0010, bar_number=15)
        close_actions = [a for a in actions if a.action == "FULL_CLOSE"]
        assert len(close_actions) == 0


# ---------------------------------------------------------------------------
# Regime change tests
# ---------------------------------------------------------------------------

class TestRegimeChange:
    def test_regime_change_closes_all(self):
        """Regime change should close all positions."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        pm.register_position(ticket=2, symbol="GBPUSD", side="SELL",
                             entry_price=1.2500, sl=1.2550, tp=1.2300,
                             volume=0.1, atr=0.0020)
        actions = pm.manage(current_price=1.1000, atr=0.0010, regime_changed=True)
        close_actions = [a for a in actions if a.action == "FULL_CLOSE"]
        assert len(close_actions) == 2
        tickets = {a.ticket for a in close_actions}
        assert tickets == {1, 2}


# ---------------------------------------------------------------------------
# Management log tests
# ---------------------------------------------------------------------------

class TestManagementLog:
    def test_actions_logged(self):
        """Each management action should be logged."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        pm.manage(current_price=1.1050, atr=0.0010)  # breakeven
        pos = pm.get_position_state(1)
        assert len(pos.management_log) >= 1
        assert pos.management_log[0]["action"] == "BREAKEVEN"

    def test_summary_returns_all(self):
        """Summary should include all managed positions."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        s = pm.summary()
        assert s["active_positions"] == 1
        assert 1 in s["positions"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unregister_removes_position(self):
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.0950, tp=1.1200,
                             volume=0.1, atr=0.0010)
        pm.unregister_position(1)
        assert 1 not in pm.positions

    def test_manage_empty_positions(self):
        pm = _make_manager()
        actions = pm.manage(current_price=1.1000, atr=0.0010)
        assert actions == []

    def test_zero_risk_distance_uses_atr(self):
        """If SL is at entry, use ATR * 1.5 as risk distance."""
        pm = _make_manager()
        pm.register_position(ticket=1, symbol="EURUSD", side="BUY",
                             entry_price=1.1000, sl=1.1000, tp=1.1200,
                             volume=0.1, atr=0.0010)
        pos = pm.get_position_state(1)
        assert pos.initial_risk_distance == 0.0015  # 1.5 * ATR
