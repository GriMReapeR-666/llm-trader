from types import SimpleNamespace
import math

import pytest

import risk_manager
from risk_manager import RiskManager


@pytest.mark.parametrize("kwargs", [
    {"balance": math.nan},
    {"balance": 10000, "risk_per_trade": 1.0},
    {"balance": 10000, "risk_per_trade": math.inf},
    {"balance": 10000, "max_drawdown": math.nan},
    {"balance": 10000, "max_daily_loss": -0.01},
])
def test_invalid_risk_configuration_fails_closed(kwargs):
    with pytest.raises(ValueError):
        RiskManager(**kwargs)


def test_minimum_broker_volume_does_not_overrisk_account(monkeypatch):
    account = SimpleNamespace(equity=10000.0)
    info = SimpleNamespace(
        trade_tick_value=1.0, trade_tick_size=0.00001,
        volume_min=1.0, volume_max=100.0, volume_step=1.0,
    )
    monkeypatch.setattr(risk_manager.mt5, "account_info", lambda: account)
    monkeypatch.setattr(risk_manager.mt5, "symbol_info", lambda symbol: info)
    risk = RiskManager(balance=10000, risk_per_trade=0.01)
    assert risk.calc_position_size("EURUSD", 1.1, 1.09) == 0.0


def test_invalid_position_inputs_return_zero(monkeypatch):
    monkeypatch.setattr(risk_manager.mt5, "account_info", lambda: SimpleNamespace(equity=10000.0))
    risk = RiskManager(balance=10000, risk_per_trade=0.01)
    assert risk.calc_position_size("EURUSD", math.nan, 1.09) == 0.0
    assert risk.calc_position_size("EURUSD", 1.1, 1.09, confidence=math.inf) == 0.0


def test_quarter_kelly_scalar_handles_pure_win_and_loss_streaks():
    risk = RiskManager(balance=10000, risk_per_trade=0.01)

    # 1. Neutral scalar before min_trades
    assert risk.quarter_kelly_scalar(min_trades=20) == 1.0

    # 2. Pure win streak (25 consecutive wins: history has items, losses is empty)
    # Must NOT raise ZeroDivisionError and must remain in [0, 1]
    risk.trade_r_history = [1.5] * 25
    scalar_wins = risk.quarter_kelly_scalar(min_trades=20)
    assert 0.0 <= scalar_wins <= 1.0

    # 3. Pure loss streak (25 consecutive losses: history is empty, losses has items)
    # Must NOT raise ZeroDivisionError and must shrink toward 0.0
    risk.trade_r_history = [-1.0] * 25
    scalar_losses = risk.quarter_kelly_scalar(min_trades=20)
    assert scalar_losses == 0.0
