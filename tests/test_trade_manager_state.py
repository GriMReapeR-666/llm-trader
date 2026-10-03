from unittest.mock import MagicMock

from trade_manager import TradeManager
from trading_safety import TradingStore


def test_trade_manager_action_state_uses_authoritative_store(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    files = dict(state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, **files)
    manager.partial_exits.add(77)
    manager.pyramid_entries.add(88)
    manager.save_state()

    restored = TradeManager(MagicMock(), MagicMock(), store=store, **files)
    assert restored.partial_exits == {77}
    assert restored.pyramid_entries == {88}
    assert store.get_state("trade_manager_state")["partial_exits"] == [77]
