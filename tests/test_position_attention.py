from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import time

from mt5_trader import LLMOrchestrator


class _Store:
    def __init__(self):
        self.observations = []

    def get_state(self, key, default=None):
        return {"atr": 1.5} if key.startswith("position_atr:") else default

    def trade_risk(self, ticket):
        return 10.0

    def record_position_observation(self, *args, **kwargs):
        self.observations.append((args, kwargs))


def _orchestrator():
    orchestrator = LLMOrchestrator.__new__(LLMOrchestrator)
    orchestrator.client = object()
    orchestrator.store = _Store()
    orchestrator.operations = MagicMock()
    orchestrator.run_id = "run-1"
    orchestrator.data_provider = SimpleNamespace(trade_manager=MagicMock())
    # The tick path reads positions via trade_manager._get_positions(symbol=...),
    # not via mt5.positions_get.
    orchestrator.data_provider.trade_manager._get_positions.return_value = [
        SimpleNamespace(ticket=77, type=0, profit=12.0)
    ]
    orchestrator.position_manager = None
    orchestrator._ingest_lag_envelope = lambda *a, **kw: {}
    return orchestrator


@patch("mt5_trader.mt5")
def test_owned_position_attention_manages_and_marks_fresh_quote(mock_mt5):
    orchestrator = _orchestrator()
    now_msc = int(time.time() * 1000)
    mock_mt5.POSITION_TYPE_BUY = 0
    mock_mt5.ORDER_TYPE_BUY = 0
    mock_mt5.symbol_info_tick.return_value = SimpleNamespace(bid=2300.0, ask=2300.2, time_msc=now_msc - 100)
    mock_mt5.positions_get.return_value = [SimpleNamespace(ticket=77, type=0, profit=12.0)]

    result = orchestrator._monitor_owned_positions_tick("XAUUSD", "M15", paper_mode=False)

    assert result["status"] == "MANAGED"
    orchestrator.data_provider.trade_manager.monitor_positions.assert_called_once_with("XAUUSD", 2300.1, 1.5)
    assert len(orchestrator.store.observations) == 1
    assert orchestrator.store.observations[0][1]["mark_quality"] == "FRESH"


@patch("mt5_trader.mt5")
def test_owned_position_attention_refuses_stale_quote(mock_mt5):
    orchestrator = _orchestrator()
    mock_mt5.symbol_info_tick.return_value = SimpleNamespace(bid=2300.0, ask=2300.2, time_msc=1)
    mock_mt5.positions_get.return_value = [SimpleNamespace(ticket=77, type=0, profit=12.0)]

    result = orchestrator._monitor_owned_positions_tick("XAUUSD", "M15", paper_mode=False)

    assert result["status"] == "SKIPPED"
    assert result["reason"] == "stale_quote"
    orchestrator.data_provider.trade_manager.monitor_positions.assert_not_called()
    assert not orchestrator.store.observations
