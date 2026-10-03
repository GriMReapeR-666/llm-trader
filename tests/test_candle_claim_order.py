import pandas as pd

import mt5_trader


class _Store:
    def __init__(self):
        self.state = {}

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value


def test_completed_candle_candidate_does_not_advance_checkpoint(monkeypatch):
    bars = pd.DataFrame({"close": [1.0, 1.1, 1.2]}, index=["bar-1", "bar-2", "bar-forming"])
    monkeypatch.setattr(mt5_trader, "fetch_ohlc", lambda *args, **kwargs: bars)
    orchestrator = object.__new__(mt5_trader.LLMOrchestrator)
    orchestrator.store = _Store()

    candidate = orchestrator._completed_candle_candidate("XAUUSD", "M15", "scalp")

    assert candidate == "bar-2"
    assert orchestrator.store.state == {}


def test_completed_candle_compatibility_helper_advances_checkpoint(monkeypatch):
    bars = pd.DataFrame({"close": [1.0, 1.1, 1.2]}, index=["bar-1", "bar-2", "bar-forming"])
    monkeypatch.setattr(mt5_trader, "fetch_ohlc", lambda *args, **kwargs: bars)
    orchestrator = object.__new__(mt5_trader.LLMOrchestrator)
    orchestrator.store = _Store()

    assert orchestrator._new_completed_candle("XAUUSD", "M15", "scalp") is True
    assert orchestrator.store.state["last_closed_bar:scalp:XAUUSD:M15"] == "bar-2"
