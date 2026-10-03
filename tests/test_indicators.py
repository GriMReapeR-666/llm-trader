import unittest
import pandas as pd
import numpy as np
import sys
import os

# Add parent directory to path to import mt5_trader
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_trader import ema, rsi, macd, atr, hma

class TestIndicators(unittest.TestCase):
    def setUp(self):
        np.random.seed(0)
        self.close = pd.Series(np.linspace(100, 120, 200) + np.random.normal(0, 0.5, 200))
        self.high = self.close + np.abs(np.random.normal(0.2, 0.1, 200))
        self.low = self.close - np.abs(np.random.normal(0.2, 0.1, 200))

    def test_ema_monotonic_period(self):
        e10 = ema(self.close, 10)
        e20 = ema(self.close, 20)
        self.assertEqual(len(e10), len(self.close))
        self.assertTrue(np.isfinite(e20.iloc[-1]))

    def test_rsi_bounds(self):
        r = rsi(self.close, 14)
        self.assertTrue(((r >= 0) & (r <= 100)).all())

    def test_macd_shapes(self):
        m, s, h = macd(self.close)
        self.assertEqual(len(m), len(self.close))
        self.assertEqual(len(s), len(self.close))
        self.assertEqual(len(h), len(self.close))

    def test_atr_positive(self):
        a = atr(self.high, self.low, self.close, 14)
        self.assertTrue((a >= 0).all())

    def test_hma_defined(self):
        h = hma(self.close, 21)
        self.assertEqual(len(h), len(self.close))

    def test_ema_step_matches_batch(self):
        from core.technical_indicators import ema_step
        batch = ema(self.close, 14)
        prev = batch.iloc[-2]
        curr_price = self.close.iloc[-1]
        step_val = ema_step(prev, curr_price, 14)
        self.assertAlmostEqual(step_val, batch.iloc[-1], places=6)

    def test_atr_step_matches_batch(self):
        from core.technical_indicators import atr_step
        batch = atr(self.high, self.low, self.close, 14)
        prev = batch.iloc[-2]
        step_val = atr_step(prev, self.high.iloc[-1], self.low.iloc[-1], self.close.iloc[-2], 14)
        self.assertAlmostEqual(step_val, batch.iloc[-1], places=6)

    def test_macd_step_matches_batch(self):
        from core.technical_indicators import macd_step
        m, s, h = macd(self.close, 12, 26, 9)
        fast_ema = ema(self.close, 12)
        slow_ema = ema(self.close, 26)
        macd_line, sig_line, hist, _, _ = macd_step(
            fast_ema.iloc[-2], slow_ema.iloc[-2], s.iloc[-2], self.close.iloc[-1], 12, 26, 9
        )
        self.assertAlmostEqual(macd_line, m.iloc[-1], places=6)
        self.assertAlmostEqual(sig_line, s.iloc[-1], places=6)
        self.assertAlmostEqual(hist, h.iloc[-1], places=6)

if __name__ == '__main__':
    unittest.main()