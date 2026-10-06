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
    # Pin sys.modules["mt5_trader"].mt5 too: the orchestrator resolves ticks
    # and reconcile reads through it when mt5_trader is imported anywhere in
    # the worker, so leaving it makes tick resolution depend on worker import
    # order (position-management advisory test caught this).
    import sys as _sys

    _mt5_trader_mod = _sys.modules.get("mt5_trader")
    if _mt5_trader_mod is not None and hasattr(_mt5_trader_mod, "mt5"):
        monkeypatch.setattr(_mt5_trader_mod, "mt5", fake, raising=False)
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


class WrapperClient:
    """The public surface of mt5_trader.MT5Client (lacks read methods like positions_get)."""
    def login(self): return True
    def get_account_info(self): return {"equity": 10_000.0, "balance": 10_000.0}
    def account_info(self): return NS(equity=10_000.0, balance=10_000.0, trade_mode=0, login=1)
    def shutdown(self): pass


def test_probe_a_wrapper_client_shape(store, fake_mt5):
    """Probe A (Appendix A): wrapper-shaped client (MT5Client shape) resolves broker reads via module handle."""
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    perf = MagicMock()
    perf.history = []

    # Owned 101 (magic 460001) + owned 103 (magic 460003) + manual 900 (magic 0)
    fake_mt5.positions = [
        NS(ticket=101, symbol="EURUSD", type=0, magic=460001, volume=0.1, price_open=1.1000, sl=1.0950, tp=1.1100),
        NS(ticket=103, symbol="GBPUSD", type=0, magic=460003, volume=0.1, price_open=1.2500, sl=1.2450, tp=1.2600),
        NS(ticket=900, symbol="USDJPY", type=0, magic=0, volume=0.1, price_open=150.00, sl=149.50, tp=151.00),
    ]
    # Owned pending 201 (magic 460002)
    fake_mt5.orders = [
        NS(ticket=201, symbol="EURUSD", type=2, magic=460002, volume_current=0.1, price_open=1.1050, sl=1.1000, tp=1.1150),
    ]

    orch = LLMOrchestrator(
        client=WrapperClient(),
        risk_manager=risk,
        perf_monitor=perf,
        api_key="mock_key",
        journal_mode="LIVE",
        store=store,
    )

    # 1. Active positions: exactly 2 owned positions (101, 103)
    assert orch._calc_live_active_positions() == 2

    # 2. Portfolio heat: > 0 (2 owned positions + 1 owned pending order)
    assert orch._calc_live_portfolio_heat() > 0

    # 3. Between-candle monitoring runs without AttributeError / ERROR
    mon_res = orch._monitor_owned_positions_tick("EURUSD", "M15", paper_mode=False)
    assert mon_res.get("status") != "ERROR"

    # 4. Reconcile with a closing deal books closed trade cleanly
    _make_open_trade(store, ticket=888)
    fake_mt5.deals = [
        NS(ticket=9001, position_id=888, entry=1, profit=25.0, commission=-0.5, time=1700000000, symbol="EURUSD", volume=0.1, price=1.1025)
    ]
    orch.reconcile_open_positions()
    assert 888 not in {int(r["ticket"]) for r in store.open_trade_rows()}


def test_probe_b_failed_read_and_entry_deal_isolation(store, fake_mt5, monkeypatch):
    """Probe B (Appendix A): failed positions read skips pass and entry-only deal never closes."""
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

    _make_open_trade(store, ticket=777)
    fake_mt5.positions = [
        NS(ticket=777, symbol="EURUSD", type=0, magic=460002, volume=0.1, price_open=1.1000, sl=1.0950, tp=1.1100)
    ]
    fake_mt5.deals = [
        NS(ticket=8001, position_id=777, entry=0, profit=0.0, commission=-0.35, time=1700000000, symbol="EURUSD", volume=0.1, price=1.1000)
    ]

    # One failed read: positions_get returns None
    monkeypatch.setattr(fake_mt5, "positions_get", lambda *a, **k: None)
    orch.reconcile_open_positions()
    assert 777 in {int(r["ticket"]) for r in store.open_trade_rows()}

    # Broker positions empty, but history has ONLY entry deal (entry=0) -> keeps open!
    monkeypatch.setattr(fake_mt5, "positions_get", lambda *a, **k: [])
    orch.reconcile_open_positions()
    assert 777 in {int(r["ticket"]) for r in store.open_trade_rows()}


def test_real_proposal_passes_constraint_engine_and_sizes(store, fake_mt5):
    """B1 & H2: Real unmocked TradingConstraintEngine allows valid proposal at validation and checks sized volume."""
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    perf = MagicMock()
    orch = LLMOrchestrator(
        client=WrapperClient(),
        risk_manager=risk,
        perf_monitor=perf,
        api_key="mock_key",
        journal_mode="PAPER",
        store=store,
    )
    orch.overfit_gate = MagicMock()
    orch.overfit_gate.check_strategy.return_value = NS(
        strategy="swing", status="PASS", risk_level="LOW", grade="A", reason="test pass", checked_at=0,
    )
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
    decision = {
        "action": "BUY",
        "entry": 1.1000,
        "stop_loss": 1.0950,
        "take_profit": 1.1150,
        "confidence": 0.85,
    }
    toon = {
        "dom": {"best_bid": 1.0999, "best_ask": 1.1000, "spread": 0.0001},
        "ohlc": [{"close": 1.0990}, {"close": 1.0995}, {"close": 1.1000}],
        "indicators": {"ema_50": 1.0500, "adx_14": 25.0, "atr_14": 0.0020},
        "multi_timeframe": {"H1": {"trend": "BULLISH"}, "M15": {"trend": "BULLISH"}},
        "technical_analysis": {"key_levels": [1.1000]},
        "live_advisory_hub": {"bocpd": {"changepoint_probability": 0.0}},
        "news": [],
        "volume_analysis": {"volume_ratio": 1.2},
        "microstructure": {"allowed": True, "flags": []},
        "context": {
            "calendar_source": "mt5_calendar",
            "clock_health": {"valid": True, "status": "PAPER_SIMULATION", "offset_seconds": 0.0},
        },
    }
    with patch("mt5_trader.classify_setup", return_value={"setup_id": "test", "eligible": True, "reason": "ok", "spec": {"min_rr": 1.5}}), \
         patch("mt5_trader.evaluate_data_requirements", return_value={"available": True, "reasons": []}), \
         patch("mt5_trader.evaluate_market_conditions", return_value=MagicMock(allowed=True, reasons=[])):
        proposal, validation = orch._validate_proposal(decision, toon, "EURUSD", "swing", paper_mode=True)
        assert proposal is not None
        assert validation.approved is True
        assert proposal.features.get("constraint_engine_verdict") == "PASS"


def test_full_lifecycle_on_fake_mt5_with_wrapper_client(store, fake_mt5):
    """B5: Full lifecycle on FakeMT5 with WrapperClient:

    pending -> fill -> reconcile -> breakeven -> trail -> partial -> close -> exactly one outcome.
    """
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    perf = MagicMock()
    perf.history = []

    orch = LLMOrchestrator(
        client=WrapperClient(),
        risk_manager=risk,
        perf_monitor=perf,
        api_key="mock_key",
        journal_mode="LIVE",
        store=store,
    )
    orch.overfit_gate = MagicMock()
    orch.overfit_gate.check_strategy.return_value = NS(
        strategy="scalp", status="PASS", risk_level="LOW", grade="A", reason="test pass", checked_at=0,
    )
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
    tm = TradeManager(client=WrapperClient(), risk_manager=risk, store=store)
    orch.wire_trade_manager(tm)

    # 1. Proposal validation & sizing
    decision = {"action": "BUY", "entry": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1150, "confidence": 0.85}
    toon = {
        "dom": {"best_bid": 1.0999, "best_ask": 1.1000, "spread": 0.0001},
        "ohlc": [{"close": 1.0990}, {"close": 1.0995}, {"close": 1.1000}],
        "indicators": {"ema_50": 1.0500, "adx_14": 25.0, "atr_14": 0.0020},
        "multi_timeframe": {"H1": {"trend": "BULLISH"}, "M15": {"trend": "BULLISH"}},
        "technical_analysis": {"key_levels": [1.1000]},
        "live_advisory_hub": {"bocpd": {"changepoint_probability": 0.0}},
        "news": [],
        "volume_analysis": {"volume_ratio": 1.2},
        "microstructure": {"allowed": True, "flags": []},
        "context": {
            "calendar_source": "mt5_calendar",
            "clock_health": {"valid": True, "status": "PAPER_SIMULATION", "offset_seconds": 0.0},
        },
    }
    with patch("mt5_trader.classify_setup", return_value={"setup_id": "test", "eligible": True, "reason": "ok", "spec": {"min_rr": 1.5}}), \
         patch("mt5_trader.evaluate_data_requirements", return_value={"available": True, "reasons": []}), \
         patch("mt5_trader.evaluate_market_conditions", return_value=MagicMock(allowed=True, reasons=[])):
        prop, val = orch._validate_proposal(decision, toon, "EURUSD", "scalp", paper_mode=True)
        assert prop is not None and val.approved

    # 2. Pending intent creation
    ticket = 5555
    ops = AgentOperations(store.path)
    ops.create_order_intent(
        "intent_5555",
        {"symbol": "EURUSD", "side": "BUY", "entry": 1.1000, "volume": 0.2, "risk_amount": 50.0, "magic": 460001, "setup_key": prop.setup_key()},
        actor="RiskController",
    )
    ops.resolve_order_intent("intent_5555", "PENDING", ticket=ticket)
    store.transition_latest_setup(prop.setup_key(), "ACCEPTED", ticket=ticket, reason="submitted")
    store.record_open_trade(ticket, prop, volume=0.2, risk_amount=50.0, payload={"magic": 460001})

    # 3. Fill: position appears on FakeMT5
    pos = NS(
        ticket=ticket, symbol="EURUSD", type=0, magic=460001, volume=0.2,
        price_open=1.1000, sl=1.0950, tp=1.1150, price_current=1.1000, profit=0.0,
    )
    fake_mt5.positions = [pos]
    fake_mt5.bid = 1.1000
    fake_mt5.ask = 1.1002

    # 4. Reconcile pending -> FILLED
    ops.resolve_order_intent("intent_5555", "FILLED", ticket=ticket)
    assert len(store.open_trade_rows()) == 1

    # 5. Breakeven: price rises to 1.1060 (> 1R = 50 pips)
    fake_mt5.bid = 1.1060
    fake_mt5.ask = 1.1062
    pos.price_current = 1.1060
    pos.profit = 120.0
    tm.breakeven_r_mult = 1.0
    tm.use_trailing_stop = False
    tm._manage_position(pos, current_price=1.1060, atr=0.0020)
    # Position SL updated towards BE (>= 1.1000)
    assert pos.sl >= 1.1000

    # 6. Trail: price rises further to 1.1150 (3R)
    fake_mt5.bid = 1.1150
    fake_mt5.ask = 1.1152
    pos.price_current = 1.1150
    pos.profit = 300.0
    old_sl = pos.sl
    tm.trailing_stop_r_mult = 1.5
    tm.use_trailing_stop = True
    tm._manage_position(pos, current_price=1.1150, atr=0.0020)
    # SL trailed tighter than previous BE
    assert pos.sl > old_sl

    # 7. Partial close: 50% of volume closed
    partial_ok = tm.execute_partial_close(ticket, percentage=0.5, reason="Target 1 hit")
    assert partial_ok is True
    assert pos.volume == pytest.approx(0.1, abs=0.01)

    # 8. Final close at broker with closing deal
    fake_mt5.positions = []
    fake_mt5.deals = [
        NS(ticket=88881, position_id=ticket, entry=0, profit=0.0, commission=-0.5, time=1700000000, symbol="EURUSD", volume=0.2, price=1.1000),
        NS(ticket=88882, position_id=ticket, entry=1, profit=85.0, commission=-0.5, time=1700000100, symbol="EURUSD", volume=0.2, price=1.1085),
    ]

    with patch.object(risk, "record_trade_outcome", wraps=risk.record_trade_outcome) as mock_outcome:
        orch.reconcile_open_positions()

        # Trade is closed in journal
        assert len(store.open_trade_rows()) == 0
        closed = _closed_trades(store)
        assert len(closed) == 1
        assert closed[0]["ticket"] == ticket

        # A second reconcile pass finds nothing left to close.
        orch.reconcile_open_positions()

        # Exactly one outcome recorded to risk manager!
        assert mock_outcome.call_count == 1
        assert mock_outcome.call_args[0][0] == pytest.approx(84.0, abs=1.0)

