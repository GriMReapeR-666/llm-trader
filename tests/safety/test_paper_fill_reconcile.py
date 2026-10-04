"""Paper market fills are journaled by the reconciler (Phase 2 review D2).

Before: a paper market entry stayed PENDING -> UNRESOLVED (no MT5 history in
paper) and, with pending intents counting toward the open-risk cap, held its
reservation forever, so paper mode stopped trading after a few market orders.
"""
from types import SimpleNamespace as NS

import pytest

from agent_operations import AgentOperations
from trading_safety import TradeProposal, TradingStore


@pytest.fixture
def orchestrator(tmp_path):
    from core.order_execution import PaperBroker
    from llm_orchestrator import LLMOrchestrator
    store = TradingStore(str(tmp_path / "s.db"))
    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.store, orch.paper_broker = store, PaperBroker()
    orch.operations = AgentOperations(store.path)
    orch.perf = NS(history=[], save_history=lambda: None, log_trade=lambda *a, **k: None)
    orch.run_id = "test"
    orch.trade_memory = NS(link_retrievals_to_trade=lambda *a, **k: None)
    orch._record_slippage_feedback = lambda **kw: None
    return orch


def test_paper_market_fill_becomes_a_trade_and_releases_on_close(orchestrator):
    store, broker, ops = orchestrator.store, orchestrator.paper_broker, orchestrator.operations
    proposal = TradeProposal(strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1, stop_loss=1.09,
                             take_profit=1.12, confidence=.8, features={"setup_id": "x"})
    from trading_safety import ValidationResult
    store.log_proposal(proposal, ValidationResult(True, [], 5))
    fill = broker.market_order("EURUSD", "BUY", 0.1, 1.1, 1.09, 1.12, "llm_auto", magic=460001)
    ops.create_order_intent("k", {"symbol": "EURUSD", "side": "BUY", "entry": 1.1, "volume": 0.1,
                                  "risk_amount": 100.0, "setup_key": proposal.setup_key(), "magic": 460001},
                            actor="RiskController")
    ops.resolve_order_intent("k", "PENDING", ticket=fill["order"])
    store.transition_latest_setup(proposal.setup_key(), "ACCEPTED", ticket=fill["order"], reason="submitted")
    assert store.open_risk() == pytest.approx(100.0)

    orchestrator.reconcile_pending_orders(paper_mode=True)

    intent = [i for i in ops.order_intents(("FILLED", "PARTIAL", "UNRESOLVED", "PENDING")) if i["intent_key"] == "k"][0]
    assert intent["status"] == "FILLED"
    rows = [r for r in store.open_trade_rows() if int(r["ticket"]) == fill["order"]]
    assert len(rows) == 1                                     # the fill is journaled as a trade
    assert store.open_risk() == pytest.approx(100.0)          # carried by the trade row, counted once

    # Closing the paper position and running the close reconciler releases it.
    broker.close_position(fill["order"], price=1.11, magic=460001)
    store.record_close(fill["order"], 1.11, 10.0, "paper close")
    assert store.open_risk() == 0.0
