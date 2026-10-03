"""Unit tests for P16: Triune Brain Architecture & Retention Protection (Jolley 2026).

Validates Section 4.2:
- Macro minimum hold prevents limbic override of neocortex thesis.
- Neocortex positions are immune to premature stagnation/time-stop exits during retention window.
- Premature rotation requests are blocked during the retention window.
- Catastrophic stop-loss, breakeven, and trailing stop protections remain inviolable.
- Expiration of retention window allows normal lifecycle and rotation.
"""
from __future__ import annotations

import pytest
from core.position_manager import (
    PositionManager,
    PositionManagerConfig,
    ManagedPosition,
    ManagementAction,
)
from core.trading_safety import TradeProposal, NEOCORTEX_MAGIC, SWING_MAGIC, SCALP_MAGIC


def test_neocortex_magic_and_proposal():
    """Verify NEOCORTEX_MAGIC assignment and TradeProposal classification."""
    macro_prop = TradeProposal(
        strategy="macro",
        symbol="EURUSD",
        side="BUY",
        entry=1.1000,
        stop_loss=1.0900,
        take_profit=1.1300,
        confidence=8.0,
        features={"thesis": "geopolitical_supply_shift"},
    )
    assert macro_prop.magic == NEOCORTEX_MAGIC

    causal_prop = TradeProposal(
        strategy="causal",
        symbol="XAUUSD",
        side="BUY",
        entry=2000.0,
        stop_loss=1980.0,
        take_profit=2060.0,
        confidence=8.5,
        features={},
    )
    assert causal_prop.magic == NEOCORTEX_MAGIC

    swing_prop = TradeProposal(
        strategy="swing",
        symbol="EURUSD",
        side="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1150,
        confidence=7.0,
        features={},
    )
    assert swing_prop.magic == SWING_MAGIC


def test_standard_position_stagnation_exits_on_time_stop():
    """Standard positions exit on time stop when stagnant."""
    cfg = PositionManagerConfig(time_stop_bars=10, time_stop_min_r=0.2, neocortex_min_hold_bars=24)
    pm = PositionManager(cfg)
    pm.register_position(
        ticket=101,
        symbol="EURUSD",
        side="BUY",
        entry_price=1.1000,
        sl=1.0950,
        tp=1.1200,
        volume=0.1,
        atr=0.0010,
        entry_bar=0,
        is_neocortex=False,
    )

    # 15 bars later, price barely moved (+0.05R)
    actions = pm.manage(current_price=1.10025, atr=0.0010, bar_number=15)
    close_actions = [a for a in actions if a.action == "FULL_CLOSE"]
    assert len(close_actions) == 1
    assert "time_stop" in close_actions[0].reason


def test_neocortex_position_blocks_time_stop_during_retention_window():
    """Neocortex position suppresses stagnation time stop during minimum hold window."""
    cfg = PositionManagerConfig(time_stop_bars=10, time_stop_min_r=0.2, neocortex_min_hold_bars=24)
    pm = PositionManager(cfg)
    pos = pm.register_position(
        ticket=202,
        symbol="EURUSD",
        side="BUY",
        entry_price=1.1000,
        sl=1.0950,
        tp=1.1200,
        volume=0.1,
        atr=0.0010,
        entry_bar=0,
        is_neocortex=True,
    )
    assert pos.is_neocortex is True

    # At bar 15: beyond standard time_stop_bars (10), but within neocortex_min_hold_bars (24)
    actions = pm.manage(current_price=1.10025, atr=0.0010, bar_number=15)
    close_actions = [a for a in actions if a.action == "FULL_CLOSE"]
    assert len(close_actions) == 0, "Neocortex position must not be closed prematurely by time stop"

    # At bar 25: beyond neocortex_min_hold_bars (24), time stop is now permitted to exit stagnant thesis
    actions_expired = pm.manage(current_price=1.10025, atr=0.0010, bar_number=25)
    close_actions_expired = [a for a in actions_expired if a.action == "FULL_CLOSE"]
    assert len(close_actions_expired) == 1
    assert "time_stop" in close_actions_expired[0].reason


def test_neocortex_rotation_protection():
    """Premature rotation requests are blocked during retention window."""
    cfg = PositionManagerConfig(neocortex_min_hold_bars=24)
    pm = PositionManager(cfg)
    pm.register_position(
        ticket=303,
        symbol="XAUUSD",
        side="BUY",
        entry_price=2000.0,
        sl=1980.0,
        tp=2060.0,
        volume=0.5,
        atr=5.0,
        entry_bar=100,
        is_neocortex=True,
    )

    # Attempt rotation at bar 110 (10 bars held < 24)
    can_rot, reason = pm.can_rotate(ticket=303, bar_number=110)
    assert can_rot is False
    assert "retention" in reason.lower()

    rot_action = pm.request_rotation_exit(ticket=303, bar_number=110, reason="rotation_competing_signal")
    assert rot_action is None, "Rotation action must be blocked during retention window"

    # Attempt rotation at bar 130 (30 bars held >= 24)
    can_rot_mature, reason_mature = pm.can_rotate(ticket=303, bar_number=130)
    assert can_rot_mature is True

    rot_action_mature = pm.request_rotation_exit(ticket=303, bar_number=130, reason="rotation_competing_signal")
    assert rot_action_mature is not None
    assert rot_action_mature.action == "FULL_CLOSE"
    assert rot_action_mature.reason == "rotation_competing_signal"


def test_neocortex_preserves_breakeven_and_trailing():
    """Neocortex trades still benefit from profit protection (breakeven + trailing stop)."""
    cfg = PositionManagerConfig(
        breakeven_r_mult=1.0,
        trailing_enabled=True,
        trailing_atr_mult=1.5,
        trailing_min_profit_r=0.5,
        neocortex_min_hold_bars=24,
    )
    pm = PositionManager(cfg)
    pm.register_position(
        ticket=404,
        symbol="EURUSD",
        side="BUY",
        entry_price=1.1000,
        sl=1.0950,
        tp=1.1200,
        volume=0.1,
        atr=0.0010,
        entry_bar=0,
        is_neocortex=True,
    )

    # Price advances to 1.1055 (+1.1R) at bar 5 (well within retention window)
    actions = pm.manage(current_price=1.1055, atr=0.0010, bar_number=5)
    sl_actions = [a for a in actions if a.action == "MODIFY_SL"]
    assert len(sl_actions) >= 1
    assert sl_actions[0].new_sl >= 1.1000  # Breakeven applied


def test_neocortex_immune_to_routine_regime_jitter():
    """Routine regime change does not prematurely liquidate active neocortex position during retention."""
    cfg = PositionManagerConfig(neocortex_min_hold_bars=24, neocortex_immune_to_regime=True)
    pm = PositionManager(cfg)

    # Register standard position
    pm.register_position(
        ticket=501,
        symbol="EURUSD",
        side="BUY",
        entry_price=1.1000,
        sl=1.0950,
        tp=1.1200,
        volume=0.1,
        atr=0.0010,
        entry_bar=0,
        is_neocortex=False,
    )
    # Register neocortex position
    pm.register_position(
        ticket=502,
        symbol="XAUUSD",
        side="BUY",
        entry_price=2000.0,
        sl=1980.0,
        tp=2060.0,
        volume=0.2,
        atr=5.0,
        entry_bar=0,
        is_neocortex=True,
    )

    actions = pm.manage(current_price=1.1000, atr=0.0010, bar_number=5, regime_changed=True)
    closed_tickets = {a.ticket for a in actions if a.action == "FULL_CLOSE"}

    # Standard ticket 501 is closed by regime change
    assert 501 in closed_tickets
    # Neocortex ticket 502 is protected during its minimum hold window
    assert 502 not in closed_tickets
