"""Tests for PR 4.5: Removing the two accidental blocks (1a-F13, TL-T1).

Verifies:
- 1a-F13: fetch_data collects tick and clock observation with server offset subtracted,
  setting clock_health and provider_time so DataQualityGate passes in LIVE/DEMO.
- TL-T1: TradingConstraintEngine evaluation checks ConstraintVerdict.REJECT only (no HOLD enum crash),
  allowing valid orders to reach order_send on FakeMT5 and blocking REJECT orders.
- Characterization: live pending entry with a token reaches order_send on FakeMT5.
- Real-money switch: LIVE requires LLM_TRADER_LIVE_ENTRIES=1; DEMO works on demo account.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pytest

from core.broker_time import set_broker_utc_offset_seconds, reset_broker_time_cache
from data.data_quality import DataQualityGate
from core.harness.sandbox import CapabilitySandbox, PermissionLevel
from core.order_execution import PaperBroker, place_pending_order
from core.order_gateway import configure_default_gateway, OrderGateway
from core.trading_constraints import ConstraintVerdict, TradingConstraintEngine
from core.trading_safety import TradingStore
from data.market_data_provider import MarketDataProvider
from intelligence.llm_orchestrator import LLMOrchestrator
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
    monkeypatch.setattr("intelligence.llm_orchestrator._get_mt5", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# 1. 1a-F13: fetch_data sets clock_health & provider_time with offset subtracted
# ---------------------------------------------------------------------------

def test_1a_f13_fetch_data_sets_clock_health_and_provider_time(fake_mt5, store):
    """Verify fetch_data collects tick and subtracts server offset (e.g. UTC+2 = 7200s),

    producing valid clock_health and provider_time that pass DataQualityGate (1a-F13).
    """
    reset_broker_time_cache()
    # Simulate a broker server at UTC+2 (offset = +7200s)
    server_offset = 7200
    set_broker_utc_offset_seconds(server_offset)

    now_utc = datetime.now(timezone.utc)
    # Server time is UTC + 7200s
    server_time_msc = int((now_utc.timestamp() + server_offset) * 1000)

    fake_mt5.symbol_info_tick = lambda sym: NS(
        bid=1.1000, ask=1.1002, time_msc=server_time_msc, last=1.1000
    )

    mdp = MarketDataProvider(client=fake_mt5, risk_manager=MagicMock(risk_per_trade=0.01, max_drawdown=0.05, balance=10000.0))
    mdp._state_store = lambda: store
    mdp._get_higher_timeframe = lambda tf: "H1"
    mdp.check_data_sources = lambda: {"mt5": True}
    mdp._calculate_hma = lambda series, period: series
    mdp._identify_support_resistance = lambda df: [1.1000]
    mdp._analyze_volume = lambda df: {"trend": "NEUTRAL"}
    mdp.analyze_confluence = lambda sym, **kw: {"score": 8, "max_score": 12, "breakdown": {}}
    mdp._alpha_rotation_engine = None
    mdp._hierarchical_portfolio_engine = None

    # Patch fetch_ohlc to return valid DataFrame
    import pandas as pd
    bars = 50
    dates = pd.date_range(end=now_utc, periods=bars, freq="5min")
    df = pd.DataFrame({
        "time": dates,
        "open": [1.1000] * bars,
        "high": [1.1010] * bars,
        "low": [1.0990] * bars,
        "close": [1.1000] * bars,
        "tick_volume": [100] * bars,
    })

    with patch("data.market_data_provider._get_fetch_ohlc", return_value=lambda *a, **k: df):
        with patch("data.market_data_provider.DOMCollector") as mock_dom_cls:
            mock_dom = MagicMock()
            mock_dom.get_snapshot.return_value = {
                "available": True, "best_bid": 1.1000, "best_ask": 1.1002, "spread": 0.0002,
                "captured_at": now_utc.isoformat(),
            }
            mock_dom_cls.return_value = mock_dom

            toon = mdp.fetch_data("EURUSD", "M5")

    context = toon["context"]
    assert "clock_health" in context
    assert "provider_time" in context
    assert context["clock_health"]["valid"] is True
    assert abs(context["clock_health"]["offset_seconds"]) <= 5.0

    # Verify provider_time is within 5s of now_utc (not 2 hours off!)
    provider_dt = datetime.fromisoformat(context["provider_time"])
    assert abs((now_utc - provider_dt).total_seconds()) <= 5.0

    # DataQualityGate must now PASS with require_live_quote=True!
    # Populate source_identity required by gate in live mode
    context["market_data_source"] = "mt5"
    context["source_identity"] = {
        "provider": "mt5",
        "server": "FakeServer",
        "account_hash": hashlib.sha256(b"12345|FakeServer").hexdigest(),
    }
    dq = DataQualityGate()
    dq_res = dq.validate(toon, require_live_quote=True)
    assert dq_res.passed is True, f"DataQualityGate rejected: {dq_res.reasons}"
    reset_broker_time_cache()


# ---------------------------------------------------------------------------
# 2. TL-T1: TradingConstraintEngine check evaluates without missing enum error
# ---------------------------------------------------------------------------

def test_tl_t1_live_pending_order_reaches_fake_mt5(fake_mt5, store, tmp_path):
    """Verify live pending order passes TradingConstraintEngine without HOLD enum crash

    and reaches FakeMT5 order_send with expected request (TL-T1 / Characterization).
    """
    configure_default_gateway(store, mode="LIVE")
    token = NS(can_access=lambda level, scope="": True)
    now_str = datetime.now(timezone.utc).isoformat()
    val = NS(approved=True, reasons=[], setup_key="test_setup", created_at=now_str)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    result = place_pending_order(
        symbol="EURUSD",
        otype="buy_limit",
        price=1.0990,
        volume=0.1,
        sl=1.0940,
        tp=1.1090,
        comment="char_test",
        paper=None,
        magic=460001,
        expires_at=expires,
        token=token,
        validation_result=val,
        setup_key="test_setup",
        risk_amount=50.0,
    )

    assert result["retcode"] == fake_mt5.TRADE_RETCODE_DONE
    assert len(fake_mt5.sent) == 1
    req = fake_mt5.sent[0]
    assert req["action"] == fake_mt5.TRADE_ACTION_PENDING
    assert req["type"] == fake_mt5.ORDER_TYPE_BUY_LIMIT
    assert req["price"] == 1.0990
    assert req["sl"] == 1.0940
    assert req["tp"] == 1.1090
    assert req["magic"] == 460001


def test_tl_t1_constraint_reject_verdict_blocks(fake_mt5, store):
    """Verify that when TradingConstraintEngine evaluates to REJECT, the order is blocked."""
    configure_default_gateway(store, mode="LIVE")
    token = NS(can_access=lambda level, scope="": True)
    now_str = datetime.now(timezone.utc).isoformat()
    val = NS(approved=True, reasons=[], setup_key="test_setup", created_at=now_str)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    with patch("core.trading_constraints.TradingConstraintEngine.evaluate") as mock_eval:
        mock_eval.return_value = NS(
            overall_verdict=ConstraintVerdict.REJECT,
            reject_reasons=["Daily loss limit breached"],
        )
        with pytest.raises(RuntimeError, match="TradingConstraintEngine REJECT"):
            place_pending_order(
                symbol="EURUSD",
                otype="buy_limit",
                price=1.0990,
                volume=0.1,
                sl=1.0940,
                tp=1.1090,
                comment="reject_test",
                paper=None,
                magic=460001,
                expires_at=expires,
                token=token,
                validation_result=val,
                setup_key="test_setup",
                risk_amount=50.0,
            )
    assert fake_mt5.sent == []


# ---------------------------------------------------------------------------
# 3. Real-money switch: LIVE needs LLM_TRADER_LIVE_ENTRIES=1
# ---------------------------------------------------------------------------

def test_real_money_live_entries_switch(store, monkeypatch):
    """Verify that LIVE mode requires LLM_TRADER_LIVE_ENTRIES=1 to issue order tokens."""
    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.store = NS(scope={"mode": "LIVE"})
    orch.order_write_sandbox = CapabilitySandbox(default_level=PermissionLevel.ORDER_WRITE)
    orch.order_write_mode = "LIVE"

    # Without flag -> refused
    monkeypatch.delenv("LLM_TRADER_LIVE_ENTRIES", raising=False)
    assert orch._live_order_token() is None

    # With flag -> issued
    monkeypatch.setenv("LLM_TRADER_LIVE_ENTRIES", "1")
    token = orch._live_order_token()
    assert token is not None
    assert token.can_access(PermissionLevel.ORDER_WRITE, scope="broker")
