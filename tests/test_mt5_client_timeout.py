import mt5_trader
from mt5_trader import Config, MT5Client


def _config():
    return Config(1, "secret-not-used", "Demo", 0.01, 0.10, 0.01, "INFO", 300, 5)


def test_mt5_client_clamps_initialize_timeout(monkeypatch):
    calls = []

    class FakeMT5:
        def initialize(self, **kwargs):
            calls.append(kwargs)
            return False

    monkeypatch.setenv("MT5_INIT_TIMEOUT_MS", "999999")
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5())
    client = MT5Client(_config())
    assert client.initialize_timeout_ms == 30000
    assert client._initialize(None, login=1, password="secret", server="Demo") is False
    assert calls[0]["timeout"] == 30000


def test_mt5_client_supports_wrappers_without_timeout_keyword(monkeypatch):
    calls = []

    class LegacyFakeMT5:
        def initialize(self, login, password, server):
            calls.append((login, password, server))
            return True

    monkeypatch.setattr(mt5_trader, "mt5", LegacyFakeMT5())
    client = MT5Client(_config())
    assert client._initialize(None, login=1, password="secret", server="Demo") is True
    assert calls == [(1, "secret", "Demo")]
