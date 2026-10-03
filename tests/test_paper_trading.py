import unittest
import sys
import os

# Add parent directory to path to import mt5_trader
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_trader import PaperBroker

class TestPaperTrading(unittest.TestCase):
    def test_market_and_pending(self):
        pb = PaperBroker()
        res = pb.market_order("EURUSD", "buy", 0.1, 1.0, None, None, "test")
        self.assertEqual(res["retcode"], 10009)
        ticket = res["order"]
        self.assertIn(ticket, pb.positions)
        res2 = pb.pending_order("EURUSD", "buy_limit", 1.05, 0.1, None, None, "test2")
        self.assertEqual(res2["retcode"], 10009)
        ticket2 = res2["order"]
        self.assertIn(ticket2, pb.orders)
        self.assertTrue(pb.cancel_order(ticket2))

    def test_modify(self):
        pb = PaperBroker()
        pb.market_order("EURUSD", "buy", 0.1, 1.0, None, None, "t", magic=9001)
        count = pb.modify_position_sltp("EURUSD", 0.9, 1.1, magic=9001)
        self.assertGreaterEqual(count, 1)

    def test_modify_does_not_touch_other_magic_or_manual_position(self):
        pb = PaperBroker()
        owned = pb.market_order("EURUSD", "buy", 0.1, 1.0, None, None, "owned", magic=9001)
        manual = pb.market_order("EURUSD", "sell", 0.1, 1.0, None, None, "manual", magic=0)
        count = pb.modify_position_sltp("EURUSD", 0.9, 1.1, magic=9001)
        self.assertEqual(count, 1)
        self.assertEqual(pb.positions[owned["order"]]["sl"], 0.9)
        self.assertIsNone(pb.positions[manual["order"]]["sl"])

if __name__ == '__main__':
    unittest.main()
