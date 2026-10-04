import time
import pytest
from unittest.mock import patch
from datetime import datetime, timezone

from core.risk_manager import RiskManager
from core.trading_safety import TradingStore


def test_risk_cooldown_and_rate_state_survive_restart(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    first = RiskManager(balance=10000, risk_per_trade=0.01, cooldown_seconds=300)
    first.state_file = str(tmp_path / "legacy-risk.json")
    first.set_store(store)
    
    assert first.check_cooldown("EURUSD")
    first.register_trade("EURUSD")
    assert not first.check_cooldown("EURUSD")

    # Verify that register_trade stored ISO strings
    assert isinstance(first.last_trade_time["EURUSD"], str)
    assert isinstance(first.trade_timestamps[0], str)
    assert "T" in first.last_trade_time["EURUSD"]

    second = RiskManager(balance=10000, risk_per_trade=0.01, cooldown_seconds=300)
    second.state_file = str(tmp_path / "legacy-risk-2.json")
    second.set_store(store)
    
    assert not second.check_cooldown("EURUSD")
    assert len(second.trade_timestamps) == 1
    assert "T" in second.trade_timestamps[0]
    
    # Test check_rate_limit parsing
    assert second.check_rate_limit()

def test_check_cooldown_with_reason():
    rm = RiskManager(balance=10000, cooldown_seconds=300)
    # Force last trade time to 100 seconds ago
    past_iso = datetime.fromtimestamp(time.time() - 100, timezone.utc).isoformat()
    rm.last_trade_time["EURUSD"] = past_iso
    
    ok, reason = rm.check_cooldown_with_reason("EURUSD")
    assert not ok
    assert int(reason) > 0 and int(reason) <= 200

def test_remaining_daily_risk():
    rm = RiskManager(balance=10000, max_daily_loss=0.03)
    rm.daily_start_equity = 10000
    
    # current equity = 9900 (lost 100)
    # max loss amt = 300
    # current dd = 100
    # remaining = 200
    assert rm.remaining_daily_risk(9900) == 200.0

    # current equity = 10100 (won 100)
    # dd = -100
    # remaining = 300 - (-100) = 400
    assert rm.remaining_daily_risk(10100) == 400.0

    # current equity = 9500 (lost 500)
    # remaining = 0
    assert rm.remaining_daily_risk(9500) == 0.0

@patch("core.risk_manager.mt5")
def test_daily_reset_fallback_when_mt5_fails(mock_mt5):
    # Simulate MT5 exception
    mock_mt5.account_info.side_effect = Exception("MT5 down")
    
    rm = RiskManager(balance=10000)
    rm.daily_start_equity = 0 # reset it to verify
    
    rm.reset_daily_limits()
    
    # Should fall back to balance
    assert rm.daily_start_equity == 10000
