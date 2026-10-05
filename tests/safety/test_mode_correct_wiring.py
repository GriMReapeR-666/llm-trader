"""Tests for PR 4.1: Mode-correct wiring, provider isolation, and one reconciler.

Verifies:
- 1a-F1 / Probe 1: Live mode orchestrator detachment from PaperBroker and risk limits.
- 1a-F2 / Probe 2: Live reconcile leaves open positions open; missing history counts misses and never fabricates 0-PnL closes.
- CC-F3 = 1a-F7 / Concurrency P4: Atomic CAS prevents duplicate close outcomes across threads.
- TL-T8: Durable rows dict conversion prevents sqlite3.Row .get() crashes.
- TL-T4: _get_mt5 import in mt5_trader enables live daily reconciliation.
"""
import sys
import threading
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pytest

from core.risk_manager import RiskManager
from core.trade_manager import TradeManager
from core.trading_safety import TradeProposal, TradingStore
from operations.agent_operations import AgentOperations
from intelligence.llm_orchestrator import LLMOrchestrator
from tests.fakes.fake_mt5 import FakeMT5, patch_everywhere


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "journal.db")
    return TradingStore(db_path)


def _closed_trades(store: TradingStore):
    with store._connect() as con:
        return con.execute("SELECT * FROM trades WHERE closed_at IS NOT NULL").fetchall()


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    patch_everywhere(monkeypatch, "intelligence/llm_orchestrator.py", "mt5", fake)
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", fake)
    patch_everywhere(monkeypatch, "core/trade_manager.py", "mt5", fake)
    patch_everywhere(monkeypatch, "core/order_execution.py", "mt5", fake)
    monkeypatch.setattr("core.order_execution._get_mt5", lambda: fake)
    return fake


def _make_open_trade(store: TradingStore, ticket: int = 777, symbol: str = "EURUSD"):
    prop = TradeProposal(
        strategy="scalp",
        symbol=symbol,
        side="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        confidence=0.8,
        features={"setup_id": "test_setup"},
    )
    store.record_open_trade(ticket, prop, volume=0.1, risk_amount=50.0, payload={})


def test_probe1_live_mode_wiring_and_provider_isolation(store, fake_mt5):
    """Probe 1: In LIVE mode, PaperBroker is None, TM has no PaperBroker,

    and active position limits count real MT5 positions + reserving intents.
    """
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.max_active_positions = 3
    risk.set_store(store)

    orch = LLMOrchestrator(
        client=fake_mt5,
        risk_manager=risk,
        perf_monitor=MagicMock(),
        api_key="mock_key",
        journal_mode="LIVE",
        store=store,
    )

    # 1. Orchestrator must not have a PaperBroker in LIVE mode
    assert orch.paper_broker is None, "LIVE orchestrator must not attach PaperBroker"

    # 2. Wire TradeManager -> TradeManager must not have a PaperBroker
    tm = TradeManager(client=fake_mt5, risk_manager=risk)
    orch.wire_trade_manager(tm)
    assert tm.paper_broker is None, "LIVE TradeManager must not have paper_broker attached"

    # 3. Position limit enforcement via live providers:
    # Set 3 owned live positions in FakeMT5 (magic=460001)
    fake_mt5.positions = [
        NS(ticket=101, symbol="EURUSD", type=0, magic=460001, volume=0.1, price_open=1.1000, sl=1.0950, tp=1.1100),
        NS(ticket=102, symbol="GBPUSD", type=0, magic=460001, volume=0.1, price_open=1.2500, sl=1.2450, tp=1.2600),
        NS(ticket=103, symbol="USDJPY", type=0, magic=460001, volume=0.1, price_open=150.00, sl=149.50, tp=151.00),
    ]
    # Limit is 3. With 3 active positions, limit check returns False
    assert risk.check_active_trade_limit() is False

    # Remove 1 position from FakeMT5 (now 2 owned)
    fake_mt5.positions.pop()
    assert risk.check_active_trade_limit() is True

    # Record 1 active reserving intent in the journal (2 + 1 = 3 >= 3)
    ops = AgentOperations(store.path)
    ops.create_order_intent(
        "res_intent_1",
        {"symbol": "AUDUSD", "side": "BUY", "entry": 0.6500, "volume": 0.1, "risk_amount": 50.0},
        actor="RiskController",
    )
    assert store.reserving_intents_count() == 1
    assert risk.check_active_trade_limit() is False

    # Once resolved (e.g. cancelled/rejected), limit check passes again
    ops.resolve_order_intent("res_intent_1", "REJECTED", error="cancelled")
    assert store.reserving_intents_count() == 0
    assert risk.check_active_trade_limit() is True


def test_probe2_live_reconcile_leaves_open_and_counts_misses(store, fake_mt5):
    """Probe 2: Live reconcile leaves open broker positions open.

    When a ticket disappears with no deal history, it keeps the trade open,
    never fabricates a 0-PnL close, and flags execution review after 3 misses.
    When deal history appears, real PnL is recorded.
    """
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    perf = MagicMock()
    perf.history = []

    orch = LLMOrchestrator(
        client=fake_mt5,
        risk_manager=risk,
        perf_monitor=perf,
        api_key="mock_key",
        journal_mode="LIVE",
        store=store,
    )

    # Record an open trade in store
    _make_open_trade(store, ticket=777)
    assert len(store.open_trade_rows()) == 1

    # Case A: Ticket 777 is open on broker -> remains open
    fake_mt5.positions = [
        NS(ticket=777, symbol="EURUSD", type=0, magic=460001, volume=0.1, price_open=1.1000, sl=1.0950, tp=1.1100)
    ]
    orch.reconcile_open_positions()
    assert len(store.open_trade_rows()) == 1
    assert orch._reconcile_misses.get(777, 0) == 0

    # Case B: Ticket disappears from broker positions, but NO deals exist in history
    fake_mt5.positions = []
    fake_mt5.deals = []

    # Cycle 1: absent, misses=1, trade stays open
    orch.reconcile_open_positions()
    assert len(store.open_trade_rows()) == 1
    assert orch._reconcile_misses[777] == 1

    # Cycle 2: absent, misses=2, trade stays open
    orch.reconcile_open_positions()
    assert len(store.open_trade_rows()) == 1
    assert orch._reconcile_misses[777] == 2

    # Cycle 3: absent, misses=3, trade stays open AND execution review is flagged
    with patch.object(store, "flag_execution_review", wraps=store.flag_execution_review) as mock_flag:
        orch.reconcile_open_positions()
        assert len(store.open_trade_rows()) == 1
        assert orch._reconcile_misses[777] == 3
        mock_flag.assert_called_once()
        assert mock_flag.call_args[0][0] == 777

    # Trade MUST NOT have been closed with fabricated 0 PnL!
    assert len(store.open_trade_rows()) == 1

    # Case C: History deals now become available (e.g. SL hit at 1.0950 with -$50 profit)
    deal_exit = NS(
        ticket=888,
        order=999,
        position_id=777,
        price=1.0950,
        volume=0.1,
        profit=-50.0,
        swap=0.0,
        commission=-1.0,
        entry=1,  # DEAL_ENTRY_OUT
        time=1700000000,
        time_msc=1700000000000,
    )
    fake_mt5.deals = [deal_exit]

    orch.reconcile_open_positions()
    # Now it must be closed!
    assert len(store.open_trade_rows()) == 0
    closed_rows = _closed_trades(store)
    assert len(closed_rows) == 1
    assert closed_rows[0]["ticket"] == 777
    assert closed_rows[0]["pnl"] == -51.0  # -50 profit + -1 commission
    assert closed_rows[0]["exit_price"] == 1.0950
    assert 777 not in orch._reconcile_misses


def test_concurrency_p4_atomic_cas_record_close(store):
    """Concurrency P4: Concurrent threads attempting to record_close on the

    same ticket result in exactly ONE success (atomic CAS at database level).
    """
    _make_open_trade(store, ticket=999)

    barrier = threading.Barrier(4)
    results = []

    def worker(worker_id):
        barrier.wait()
        res = store.record_close(999, 1.1050, 50.0, f"close from worker {worker_id}")
        results.append(res)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread succeeded (True), the other 3 returned False
    assert results.count(True) == 1
    assert results.count(False) == 3

    # Store has exactly 1 closed trade
    assert len(store.open_trade_rows()) == 0
    closed = _closed_trades(store)
    assert len(closed) == 1
    assert closed[0]["ticket"] == 999
    assert closed[0]["pnl"] == 50.0


def test_concurrency_p4_reconcile_lock_and_idempotence(store, fake_mt5):
    """Concurrency P4: Reconciler lock ensures single execution and

    subsequent runs for the same closed ticket do not duplicate risk outcomes.
    """
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    perf = MagicMock()
    perf.history = []

    orch = LLMOrchestrator(
        client=fake_mt5,
        risk_manager=risk,
        perf_monitor=perf,
        api_key="mock_key",
        journal_mode="LIVE",
        store=store,
    )

    _make_open_trade(store, ticket=999)

    # Position is absent from broker, deal history is present
    fake_mt5.positions = []
    fake_mt5.deals = [
        NS(
            ticket=1001,
            order=1002,
            position_id=999,
            price=1.1050,
            volume=0.1,
            profit=50.0,
            swap=0.0,
            commission=0.0,
            entry=1,
            time=1700000000,
            time_msc=1700000000000,
        )
    ]

    with patch.object(risk, "record_trade_outcome", wraps=risk.record_trade_outcome) as mock_outcome:
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            orch.reconcile_open_positions()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify atomic close in store
        assert len(store.open_trade_rows()) == 0
        closed_rows = _closed_trades(store)
        assert len(closed_rows) == 1
        assert closed_rows[0]["ticket"] == 999
        assert closed_rows[0]["pnl"] == 50.0

        # Risk outcome must have been called exactly once!
        assert mock_outcome.call_count == 1
        mock_outcome.assert_called_once_with(50.0)

        # Calling reconcile again in a subsequent pulse does NOT record outcome again
        orch.reconcile_open_positions()
        assert mock_outcome.call_count == 1


def test_tl_t4_live_reconciliation_imports_get_mt5(store, fake_mt5):
    """TL-T4: mt5_trader imports _get_mt5, allowing live daily reconciliation."""
    from mt5_trader import _get_mt5
    broker = _get_mt5()
    assert broker is not None, "_get_mt5 must return the MetaTrader5 module or fake"

    # Verify compare_day runs cleanly against FakeMT5 deals
    from datetime import datetime, timezone
    from operations.daily_reconciliation import compare_day
    start_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_dt = datetime(2026, 1, 2, tzinfo=timezone.utc)
    deals = broker.history_deals_get(start_dt, end_dt)
    report = compare_day(store, deals, start_dt, end_dt, require_timestamps=True)
    assert isinstance(report, dict)
    assert report["status"] == "CLEAN"
    assert report["journal"]["closed_trade_count"] == 0
