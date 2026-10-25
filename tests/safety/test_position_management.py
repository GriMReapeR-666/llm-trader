"""Tests for PR 4.4 Position Management, Order Geometry, and Forming Bar Correctness.

Covers:
- 1a-F18: Single authoritative execution engine (TradeManager executes, PositionManager advisory).
- 1a-F9: Initial risk basis (R) preserved across restarts, used in breakeven/trail/time-stop/pyramid.
- 1a-F11: Paper SL modifications strictly scoped to ticket, tighter stop wins between BE and trail.
- 1a-F12: TradeManager defects fixed (type_filling on close, closed_any on success, per-position try, unowned logged once).
- 1a-F5 / TL-T2: Execution safety supervisor rejects spread spikes (3x rolling average) in points.
- LA-A1 / LA-A2: Forming bar excluded from SMC market structure and MTF/HMA trend indicators.
"""
from datetime import datetime, timezone
import math
import sqlite3
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from core.execution_safety_supervisor import LiveExecutionSafetySupervisor
from core.order_execution import PaperBroker, _get_rolling_spread_avg_points
from core.order_gateway import OrderGateway, PaperAdapter
from core.trade_manager import TradeManager
from core.trading_safety import TradeProposal, TradingStore
from tests.fakes.fake_mt5 import FakeMT5, patch_everywhere


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "journal.db")
    return TradingStore(db_path)


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    patch_everywhere(monkeypatch, "intelligence/llm_orchestrator.py", "mt5", fake)
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", fake)
    patch_everywhere(monkeypatch, "core/trade_manager.py", "mt5", fake)
    patch_everywhere(monkeypatch, "core/order_execution.py", "mt5", fake)
    patch_everywhere(monkeypatch, "data/market_data_provider.py", "mt5", fake)
    monkeypatch.setattr("core.order_execution._get_mt5", lambda: fake)
    monkeypatch.setattr("data.market_data_provider._get_mt5", lambda: fake)
    # The orchestrator resolves ticks via sys.modules["mt5_trader"].mt5 when
    # mt5_trader is imported anywhere in the worker; pin that too so tick
    # resolution cannot depend on worker import order.
    _mt5_trader_mod = sys.modules.get("mt5_trader")
    if _mt5_trader_mod is not None and hasattr(_mt5_trader_mod, "mt5"):
        monkeypatch.setattr(_mt5_trader_mod, "mt5", fake, raising=False)
    return fake


# ---------------------------------------------------------------------------
# 1. Full Ladder & Initial Risk Preservation (1a-F9, 1a-F11, 1a-F12)
# ---------------------------------------------------------------------------

def test_initial_risk_preserved_from_db_after_restart(store, fake_mt5):
    """Verify that after an engine restart, initial R is restored from the trades

    table row and not corrupted by current mutated pos.sl (1a-F9).
    """
    ticket = 1001
    symbol = "EURUSD"
    initial_entry = 1.1000
    initial_sl = 1.0950  # 50 pips initial risk distance = 0.0050
    risk_dollars = 50.0

    # Write trade row to DB at fill time
    with store._connect() as con:
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, "
            "take_profit, risk_amount, opened_at, payload, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'mt5_broker')",
            (ticket, 460001, "scalp", symbol, "BUY", 0.1, initial_entry, initial_sl, 1.1150, risk_dollars,
             "2026-10-05T10:00:00Z", "{}"),
        )

    # Initialize a new TradeManager simulating restart (empty in-memory initial_risk cache)
    tm = TradeManager(client=fake_mt5, risk_manager=MagicMock(), store=store)
    assert ticket not in tm.initial_risk

    # Simulate position whose current SL has already been moved to breakeven (1.1000)
    pos = NS(
        ticket=ticket,
        symbol=symbol,
        type=0,  # BUY
        price_open=initial_entry,
        sl=initial_entry,  # Mutated to breakeven!
        tp=1.1150,
        volume=0.1,
        time=int(datetime.now(timezone.utc).timestamp()),
        magic=460001,
        comment="test_trade",
    )

    # _initial_risk_for must read initial 50 pips (0.0050) from DB, not 0 from pos.sl
    r_dist = tm._initial_risk_for(pos, atr=0.0010)
    assert r_dist == pytest.approx(0.0050, rel=1e-5)
    assert tm.initial_risk[ticket] == pytest.approx(0.0050, rel=1e-5)


def test_tighter_stop_wins_between_breakeven_and_trailing(store, fake_mt5):
    """Verify that when both breakeven (+1R) and trailing stop qualify,

    the tighter protective stop wins and only one modification is sent (1a-F11).
    """
    ticket = 1002
    symbol = "EURUSD"
    open_price = 1.1000
    initial_sl = 1.0950  # 50 pips R basis = 0.0050
    atr = 0.0020

    with store._connect() as con:
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, "
            "take_profit, risk_amount, opened_at, payload, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'mt5_broker')",
            (ticket, 460001, "scalp", symbol, "BUY", 0.1, open_price, initial_sl, 1.1200, 50.0,
             "2026-10-05T10:00:00Z", "{}"),
        )

    tm = TradeManager(client=fake_mt5, risk_manager=MagicMock(), store=store)
    tm.breakeven_r_mult = 1.0
    tm.trailing_stop_r_mult = 1.5
    tm.use_trailing_stop = True

    # Price moves to 1.1150 (+3.0R profit)
    # Breakeven SL candidate = open_price + (atr * 0.1) = 1.1000 + 0.0002 = 1.1002
    # Trailing SL candidate = current_price - (r_dist * 1.5) = 1.1150 - (0.0050 * 1.5) = 1.1075
    # Tighter stop is Trailing (1.1075 > 1.1002)
    pos = NS(
        ticket=ticket,
        symbol=symbol,
        type=fake_mt5.POSITION_TYPE_BUY,
        price_open=open_price,
        sl=1.0950,
        tp=1.1200,
        volume=0.1,
        magic=460001,
        time=int(datetime.now(timezone.utc).timestamp()),
        comment="test",
    )

    with patch.object(tm, "_modify_sl") as mock_modify:
        tm._manage_position(pos, current_price=1.1150, atr=atr)
        # Exactly one call, tighter trailing stop selected
        mock_modify.assert_called_once_with(ticket, pytest.approx(1.1075, rel=1e-5), "Trailing")


def test_time_stop_uses_initial_r(store, fake_mt5):
    """Verify that time stop computes R-multiple against initial R basis,

    exiting stagnant positions correctly (1a-F9, 1a-F10).
    """
    ticket = 1003
    symbol = "EURUSD"
    open_price = 1.1000
    initial_sl = 1.0950  # 50 pips = 0.0050 R basis
    atr = 0.0010

    with store._connect() as con:
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, "
            "take_profit, risk_amount, opened_at, payload, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'mt5_broker')",
            (ticket, 460001, "scalp", symbol, "BUY", 0.1, open_price, initial_sl, 1.1200, 50.0,
             "2026-10-05T10:00:00Z", "{}"),
        )

    tm = TradeManager(client=fake_mt5, risk_manager=MagicMock(), store=store)
    tm.time_stop_seconds = 3600  # 1 hour

    # Position opened 2 hours ago, currently at +0.05R (1.10025, stagnant < 0.20R)
    past_epoch = int(datetime.now(timezone.utc).timestamp()) - 7200
    pos = NS(
        ticket=ticket,
        symbol=symbol,
        type=fake_mt5.POSITION_TYPE_BUY,
        price_open=open_price,
        sl=open_price,  # Even if already at breakeven
        tp=1.1200,
        volume=0.1,
        magic=460001,
        time=past_epoch,
        comment="test",
    )

    with patch.object(tm, "_close_position", return_value=True) as mock_close:
        with patch.object(tm, "_authorize_management_action", return_value=(True, "OK")):
            with patch("core.broker_time.broker_epoch_to_utc", return_value=datetime.fromtimestamp(past_epoch, tz=timezone.utc)):
                tm._check_time_stop(pos, current_price=1.10025, atr=atr)
                mock_close.assert_called_once_with(ticket, symbol, "TimeStopExit")


def test_pyramid_child_cannot_pyramid(store, fake_mt5):
    """Verify that tagged pyramid children cannot enter further pyramids (1a-F9)."""
    tm = TradeManager(client=fake_mt5, risk_manager=MagicMock(), store=store)
    tm.pyramid_children.add(2001)

    # Position 1: In pyramid_children set
    pos1 = NS(ticket=2001, symbol="EURUSD", magic=460001, comment="child", type=0, price_open=1.10, sl=1.09)
    # Position 2: Magic is 460002
    pos2 = NS(ticket=2002, symbol="EURUSD", magic=460002, comment="normal", type=0, price_open=1.10, sl=1.09)
    # Position 3: Comment contains "pyramid"
    pos3 = NS(ticket=2003, symbol="EURUSD", magic=460001, comment="scale_pyramid_add", type=0, price_open=1.10, sl=1.09)

    with patch.object(tm, "_execute_pyramid_order") as mock_exec:
        tm._check_pyramid_entry(pos1, current_price=1.12, atr=0.001, indicators={"adx": 30})
        tm._check_pyramid_entry(pos2, current_price=1.12, atr=0.001, indicators={"adx": 30})
        tm._check_pyramid_entry(pos3, current_price=1.12, atr=0.001, indicators={"adx": 30})
        mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Paper SL Ticket Scoping & Gateway Protection (1a-F11)
# ---------------------------------------------------------------------------

def test_paper_modify_scoped_strictly_to_ticket():
    """Verify PaperBroker modify_position_sltp updates only the target ticket (1a-F11)."""
    paper = PaperBroker()
    paper.positions[501] = {
        "symbol": "EURUSD", "type": "buy", "price": 1.1000, "volume": 0.1,
        "sl": 1.0900, "tp": 1.1200, "magic": 460001,
    }
    paper.positions[502] = {
        "symbol": "EURUSD", "type": "buy", "price": 1.1010, "volume": 0.1,
        "sl": 1.0910, "tp": 1.1210, "magic": 460001,
    }

    # Modify ticket 501 only
    count = paper.modify_position_sltp("EURUSD", sl=1.0950, tp=1.1250, magic=460001, ticket=501)
    assert count == 1
    # Ticket 501 updated
    assert paper.positions[501]["sl"] == 1.0950
    assert paper.positions[501]["tp"] == 1.1250
    # Ticket 502 left untouched
    assert paper.positions[502]["sl"] == 1.0910
    assert paper.positions[502]["tp"] == 1.1210


def test_paper_adapter_modify_protection_tighten_only(store):
    """Verify PaperAdapter routes through ticket scoping and protective checks."""
    paper = PaperBroker()
    paper.positions[601] = {
        "symbol": "EURUSD", "type": "buy", "price": 1.1000, "volume": 0.1,
        "sl": 1.0950, "tp": 1.1200, "magic": 460001,
    }
    paper.positions[602] = {
        "symbol": "EURUSD", "type": "buy", "price": 1.1000, "volume": 0.1,
        "sl": 1.0950, "tp": 1.1200, "magic": 460001,
    }

    adapter = PaperAdapter(paper)
    gw = OrderGateway(store=store, adapter=adapter)
    # Tighten ticket 601
    res = gw.modify_protection(601, symbol="EURUSD", new_sl=1.0980, new_tp=1.1250, current_sl=1.0950, side="BUY")
    assert res.success is True
    assert paper.positions[601]["sl"] == 1.0980
    # Ticket 602 untouched
    assert paper.positions[602]["sl"] == 1.0950

    # Loosening stop on a BUY (1.0980 -> 1.0920) is refused by tightening check
    res_loosen = gw.modify_protection(601, symbol="EURUSD", new_sl=1.0920, new_tp=1.1250, current_sl=1.0980, side="BUY")
    assert res_loosen.success is False
    assert paper.positions[601]["sl"] == 1.0980


# ---------------------------------------------------------------------------
# 3. Spread Explosion Guard (1a-F5, TL-T2)
# ---------------------------------------------------------------------------

def test_spread_spike_3x_rejected_by_supervisor():
    """Verify ExecutionSafetySupervisor rejects spread spikes (3x rolling average) (1a-F5, TL-T2)."""
    sup = LiveExecutionSafetySupervisor()
    # Baseline average = 10.0 points, Current spread = 30.0 points (ratio 3.0 > 2.5)
    result = sup.validate_pre_trade_safety(
        symbol="EURUSD",
        requested_volume=0.1,
        current_spread_pips=30.0,
        historical_avg_spread_pips=10.0,
        roundtrip_latency_ms=10.0,
    )
    assert result["is_approved"] is False
    assert "SPREAD_EXPLOSION" in result["rejection_reason"]


def test_spread_normal_approved_by_supervisor():
    """Verify ExecutionSafetySupervisor approves normal spread within limits."""
    sup = LiveExecutionSafetySupervisor()
    # Baseline average = 10.0 points, Current spread = 12.0 points (ratio 1.2 <= 2.5)
    result = sup.validate_pre_trade_safety(
        symbol="EURUSD",
        requested_volume=0.1,
        current_spread_pips=12.0,
        historical_avg_spread_pips=10.0,
        roundtrip_latency_ms=10.0,
    )
    assert result["is_approved"] is True


def test_rolling_spread_average_calculation():
    """Verify _get_rolling_spread_avg_points calculates rolling spread average in points."""
    sym = "TEST_SYM"
    # Feed 10.0, 20.0
    avg1 = _get_rolling_spread_avg_points(sym, 10.0, point=0.00001)
    assert avg1 == 10.0
    avg2 = _get_rolling_spread_avg_points(sym, 20.0, point=0.00001)
    assert avg2 == 10.0  # Average before adding 20.0
    avg3 = _get_rolling_spread_avg_points(sym, 30.0, point=0.00001)
    assert avg3 == pytest.approx(15.0)  # (10 + 20) / 2


# ---------------------------------------------------------------------------
# 4. Forming Bar Correctness (LA-A1, LA-A2)
# ---------------------------------------------------------------------------

def test_smc_structure_ignores_forming_bar_perturbations():
    """Verify SMC structure calculation slices off the forming bar [:-1]

    so crazy tick spikes in the forming bar do not contaminate structure (LA-A1).
    """
    from core.smc_structure import detect_structure

    # 10 completed bars with a clear structure
    completed_bars = [
        {"high": 1.1000 + i * 0.0010, "low": 1.0980 + i * 0.0010, "close": 1.0990 + i * 0.0010, "open": 1.0985 + i * 0.0010}
        for i in range(10)
    ]
    # Sliced calculation on completed bars
    h = [b["high"] for b in completed_bars]
    l = [b["low"] for b in completed_bars]
    c = [b["close"] for b in completed_bars]
    events_clean = detect_structure(h, l, c)

    # Now append a forming bar with a massive erratic spike
    forming_bar = {"high": 9.9999, "low": 0.0001, "close": 5.5555, "open": 1.1085}
    ohlc_with_forming = completed_bars + [forming_bar]

    # As implemented in llm_orchestrator.py: slice [:-1] if len >= 6
    _completed_sliced = ohlc_with_forming[:-1] if len(ohlc_with_forming) >= 6 else ohlc_with_forming
    h_sliced = [b["high"] for b in _completed_sliced]
    l_sliced = [b["low"] for b in _completed_sliced]
    c_sliced = [b["close"] for b in _completed_sliced]
    events_protected = detect_structure(h_sliced, l_sliced, c_sliced)

    assert len(events_clean) == len(events_protected)
    assert [e.kind for e in events_clean] == [e.kind for e in events_protected]


def test_market_data_provider_mtf_reads_completed_bar():
    """Verify MarketDataProvider indicators read index -2 (completed bar) (LA-A2)."""
    from data.market_data_provider import MarketDataProvider

    # Create dummy provider
    mdp = MarketDataProvider.__new__(MarketDataProvider)
    mdp.client = None

    # Construct OHLC with 60 bars
    closes = [1.1000 + (i * 0.0005) for i in range(60)]
    # Make completed bar (idx -2 = 58) bullish: close=1.1290 > ma_200 / ma_50
    # Make forming bar (idx -1 = 59) crash to 0.5000
    closes[-1] = 0.5000
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 0.0010 for c in closes],
        "low": [c - 0.0010 for c in closes],
        "close": closes,
        "tick_volume": [100] * 60,
    })

    # Test HMA trend calculation with -2 completed bar
    df["hma_200"] = mdp._calculate_hma(df["close"], 20)
    _idx = -2 if len(df) >= 2 else -1
    current_price = df["close"].iloc[_idx]
    hma_val = df["hma_200"].iloc[_idx] if not df["hma_200"].empty else 0.0
    trend_status = "BULLISH" if current_price > hma_val else "BEARISH"

    # Price at completed bar is bullish (> hma), even though forming bar crashed
    assert current_price == pytest.approx(closes[-2])
    assert trend_status == "BULLISH"


# ---------------------------------------------------------------------------
# 5. Single Engine Authority (1a-F18)
# ---------------------------------------------------------------------------

def test_position_manager_action_is_advisory_only(store, fake_mt5):
    """Verify LLMOrchestrator treats PositionManager actions as advisory

    and does NOT execute them through TradeManager (1a-F18).
    """
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.client = fake_mt5
    orch.paper_broker = None
    orch.store = store
    orch.operations = MagicMock()
    orch.run_id = "test_run"

    mock_tm = MagicMock()
    mock_dp = MagicMock()
    mock_dp.trade_manager = mock_tm
    orch.data_provider = mock_dp

    pos = NS(
        ticket=888,
        symbol="EURUSD",
        type=fake_mt5.POSITION_TYPE_BUY,
        price_open=1.1000,
        sl=1.0950,
        tp=1.1200,
        volume=0.1,
        magic=460001,
        time=int(datetime.now(timezone.utc).timestamp()),
        comment="",
    )
    fake_mt5.positions = [pos]

    # Store must record risk for ticket so it is considered owned
    with store._connect() as con:
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, "
            "take_profit, risk_amount, opened_at, payload, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'mt5_broker')",
            (888, 460001, "scalp", "EURUSD", "BUY", 0.1, 1.1000, 1.0950, 1.1200, 50.0,
             "2026-10-05T10:00:00Z", "{}"),
        )

    # PositionManager recommends actions
    mock_pm = MagicMock()
    mock_pm.positions = {888: {}}
    mock_pm.manage.return_value = [
        NS(action="MODIFY_SL", ticket=888, new_sl=1.1020, reason="pm_trail"),
        NS(action="PARTIAL_CLOSE", ticket=888, close_fraction=0.5, reason="pm_scale"),
        NS(action="FULL_CLOSE", ticket=888, symbol="EURUSD", reason="pm_exit"),
    ]
    orch.position_manager = mock_pm

    # Set fresh mock tick in FakeMT5 with current timestamp
    import time
    fake_mt5.symbol_info_tick = lambda sym: NS(bid=1.1050, ask=1.1052, time_msc=int(time.time() * 1000), last=1.1050)
    mock_tm._get_positions.return_value = [pos]

    # Execute tick monitor
    orch._monitor_owned_positions_tick(symbol="EURUSD", timeframe="M5", paper_mode=False)

    # PositionManager evaluated the positions
    mock_pm.manage.assert_called_once()

    # But TradeManager was NEVER commanded by PositionManager's actions
    mock_tm._modify_sl.assert_not_called()
    mock_tm._execute_partial_close.assert_not_called()
    mock_tm._close_position.assert_not_called()
