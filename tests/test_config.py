import unittest
import os
import sys

# Add parent directory to path to import mt5_trader
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_trader import Config

class TestConfig(unittest.TestCase):
    def test_env_override(self):
        os.environ["MT5_LOGIN"] = "999999"
        os.environ["MT5_PASSWORD"] = "pw"
        os.environ["MT5_SERVER"] = "srv"
        cfg = Config.from_sources(None)
        self.assertEqual(cfg.login, 999999)
        self.assertEqual(cfg.password, "pw")
        self.assertEqual(cfg.server, "srv")
        for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"):
            os.environ.pop(k, None)

if __name__ == '__main__':
    unittest.main()