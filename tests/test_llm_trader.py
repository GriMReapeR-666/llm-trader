import unittest
import json
import tempfile
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent dir to path to import mt5_trader
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_trader import RiskManager, PerformanceMonitor, DOMCollector

class TestRiskManager(unittest.TestCase):
    def setUp(self):
        # Hermetic: RiskManager persists to a CWD-relative state file by
        # default; loading an ambient one (e.g. written by a paper session
        # with a different seeded balance) injects phantom drawdown state.
        self._tmp_state = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_state.cleanup)
        _state_file = os.path.join(self._tmp_state.name, "risk_state.json")
        self.risk = RiskManager(balance=10000, risk_per_trade=0.01, state_file=_state_file)
        self.risk.consecutive_losses = 0
        # `calc_position_size` scales risk off `_account_equity()`, which reads
        # the module-level `mt5` global inside core/risk_manager.py. Patching
        # `mt5_trader.mt5` does not touch it - that is a different module's
        # global - so a sibling test that faked MT5 account equity silently
        # resized this position (1.0 lots instead of 0.1) depending on which
        # xdist worker ran which file. Neutralise the real source so the
        # documented fallback to `self.balance` is what gets exercised.
        _risk_module = sys.modules[type(self.risk).__module__]
        self._mt5_global = patch.object(_risk_module, "mt5", None)
        self._mt5_global.start()
        self.addCleanup(self._mt5_global.stop)

    def test_position_sizing(self):
        # Mock mt5.symbol_info
        with patch('mt5_trader.mt5') as mock_mt5:
            mock_info = MagicMock()
            mock_info.trade_tick_value = 1.0
            mock_info.trade_tick_size = 0.00001
            mock_info.volume_step = 0.01
            mock_info.volume_min = 0.01
            mock_info.volume_max = 100.0
            mock_mt5.symbol_info.return_value = mock_info
            
            # Entry 1.1000, SL 1.0900 (1000 points / 100 pips)
            # Risk = $100
            # Loss per lot = (0.0100 / 0.00001) * 1.0 = 1000
            # Volume = 100 / 1000 = 0.1
            vol = self.risk.calc_position_size("EURUSD", 1.1000, 1.0900)
            self.assertEqual(vol, 0.1)

    def test_drawdown_check(self):
        self.risk.peak_equity = 10000
        self.assertTrue(self.risk.check_drawdown(9500)) # 5% DD
        self.assertFalse(self.risk.check_drawdown(8000)) # 20% DD > 10% Limit

class TestPerformanceMonitor(unittest.TestCase):
    def setUp(self):
        self.perf = PerformanceMonitor(history_file="test_rewards.json")
        self.perf.trades = []

    def tearDown(self):
        if os.path.exists("test_rewards.json"):
            os.remove("test_rewards.json")

    def test_sharpe_calc(self):
        # Add some dummy closed trades
        self.perf.trades = [
            {"status": "CLOSED", "pnl": 100},
            {"status": "CLOSED", "pnl": -50},
            {"status": "CLOSED", "pnl": 100},
            {"status": "CLOSED", "pnl": 50},
        ]
        sharpe = self.perf.calc_rolling_sharpe(window=10)
        # Mean = 50, Std > 0
        self.assertGreater(sharpe, 0)

    def test_max_drawdown_calc(self):
        self.perf.trades = [
            {"status": "CLOSED", "pnl": 100},  # +100
            {"status": "CLOSED", "pnl": -50},  # +50 (DD 50)
            {"status": "CLOSED", "pnl": -20},  # +30 (DD 70)
            {"status": "CLOSED", "pnl": 100},  # +130
        ]
        dd = self.perf.calc_max_drawdown()
        self.assertEqual(dd, 70)

    def test_sortino_calc_uses_downside_only(self):
        self.perf.trades = [
            {"status": "CLOSED", "pnl": 100},
            {"status": "CLOSED", "pnl": -50},
            {"status": "CLOSED", "pnl": 100},
            {"status": "CLOSED", "pnl": -20},
        ]
        sortino = self.perf.calc_rolling_sortino(window=10)
        self.assertGreater(sortino, 0)

    def test_sortino_returns_zero_without_downside_observations(self):
        self.perf.trades = [
            {"status": "CLOSED", "pnl": 10},
            {"status": "CLOSED", "pnl": 20},
        ]
        self.assertEqual(self.perf.calc_rolling_sortino(window=10), 0.0)

if __name__ == '__main__':
    unittest.main()
