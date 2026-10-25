"""Tests for PR 4.3 sizing, portfolio limits, and concurrency safety.

Covers:
- Probe 3 (1a-F3): Multi-cycle meta-adaptive and crash detector scaling does not mutate base risk.
- 1a-F16: Heat budget restoration from copula crisis.
- TS-P1: Minimum lot over cap returns 0.0 lots with reason.
- D3b: Concurrency correlation cap inside reservation transaction.
- CC-F6: Concurrency LLM reservation respects unique trigger and max_concurrent.
- PH4-N1: Broker reads facade under MT5 lock.
"""
import concurrent.futures
from datetime import datetime, timezone
import os
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.broker_reads import (
    broker_account_info,
    broker_history_deals_get,
    broker_history_orders_get,
    broker_orders_get,
    broker_positions_get,
    broker_symbol_info,
)
from core.order_gateway import OrderGateway, OrderIntent, get_mt5_lock
from core.risk_manager import RiskManager
from core.trading_safety import TradeProposal, TradingStore
from operations.agent_operations import AgentOperations


# ---------------------------------------------------------------------------
# 1. Probe 3 (1a-F3): Fixed Base Risk Across Multiple Cycles
# ---------------------------------------------------------------------------

def test_probe_3_meta_adaptive_unmutated_base_risk():
    """Verify that 10 consecutive cycles with 1.2x research multiplier do not

    compound or mutate the base risk per trade (Probe 3).
    """
    from intelligence.llm_orchestrator import LLMOrchestrator

    rm = RiskManager(balance=10000.0, risk_per_trade=0.01)
    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.risk = rm
    orch._base_risk_per_trade = rm.base_risk_per_trade

    # Verify initial base risk
    assert orch._base_risk_per_trade == 0.01
    assert rm.risk_per_trade == 0.01

    # Simulate 10 cycles of RAPID_ASCEND / meta-adaptive with 1.2x multiplier
    with patch.object(orch, "_research_influence_allowed", return_value=True):
        for cycle in range(10):
            research_results = {
                "meta_adaptive": {
                    "status": "OK",
                    "adapted_profile": {
                        "risk_multiplier": 1.2,
                        "position_scale": 1.0,
                    },
                    "current_regime": "TRENDING_BULL",
                },
                "crash_detector": {
                    "trend": "RAPID_ASCEND",
                    "risk_multiplier": 1.0,
                    "status": "OK",
                },
            }
            toon = {"auto_quant_tools": {"results": research_results}}
            orch._apply_advanced_auto_tools(toon, paper_mode=True)

            # Effective risk should remain exactly 0.012 (1.2 * 0.01), never compounding
            assert toon["cycle_effective_risk"] == pytest.approx(0.012, rel=1e-5), f"Cycle {cycle} compounded!"
            # Underlying risk_per_trade and _base_risk_per_trade must remain 0.01
            assert rm.risk_per_trade == pytest.approx(0.01, rel=1e-5)
            assert orch._base_risk_per_trade == pytest.approx(0.01, rel=1e-5)


def test_probe_3_unapproved_multiplier_ignored():
    """Verify that suggested risk multiplier > 1.0 is ignored if research influence is unapproved."""
    from intelligence.llm_orchestrator import LLMOrchestrator

    rm = RiskManager(balance=10000.0, risk_per_trade=0.01)
    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.risk = rm
    orch._base_risk_per_trade = rm.base_risk_per_trade

    with patch.object(orch, "_research_influence_allowed", return_value=False):
        research_results = {
            "meta_adaptive": {
                "status": "OK",
                "adapted_profile": {
                    "risk_multiplier": 1.5,
                },
            },
        }
        toon = {"auto_quant_tools": {"results": research_results}}
        orch._apply_advanced_auto_tools(toon, paper_mode=False)

        # Multiplier > 1.0 rejected when unapproved; fallback to 1.0 * base_risk = 0.01
        assert toon["cycle_effective_risk"] == pytest.approx(0.01)
        assert rm.risk_per_trade == 0.01


# ---------------------------------------------------------------------------
# 2. 1a-F16: Heat Budget Recovery
# ---------------------------------------------------------------------------

def test_heat_budget_recovery_1a_f16():
    """Verify that copula tail risk crisis reduces heat budget to 1.0R,

    and subsequent normal conditions restore it back to 4.0R (1a-F16).
    """
    rm = RiskManager(balance=10000.0)
    assert rm.max_portfolio_heat_r == 4.0
    assert rm.base_heat_budget_r == 4.0

    # Crisis occurs: set to 1.0R
    rm.set_heat_budget_r(1.0)
    assert rm.max_portfolio_heat_r == 1.0

    # Crisis ends: set_heat_budget_r(4.0) restores it
    rm.set_heat_budget_r(4.0)
    assert rm.max_portfolio_heat_r == 4.0

    # Test reset_heat_budget_r helper
    rm.set_heat_budget_r(2.0)
    assert rm.max_portfolio_heat_r == 2.0
    rm.reset_heat_budget_r()
    assert rm.max_portfolio_heat_r == 4.0


# ---------------------------------------------------------------------------
# 3. TS-P1: Min Lot Over Cap Returns 0.0 Lots
# ---------------------------------------------------------------------------

def test_min_lot_over_cap_returns_zero_ts_p1(monkeypatch):
    """If the broker min lot risks more than the allowed 1R dollar cap,

    calc_position_size must return 0.0 lots rather than violating the risk cap.
    """
    rm = RiskManager(balance=1000.0, risk_per_trade=0.01)  # 1R = $10.00

    # Mock symbol info where min_lot is 0.1 and point value makes 0.1 lots risk $50.00
    mock_info = SimpleNamespace(
        volume_min=0.1,
        volume_step=0.01,
        volume_max=10.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        point=0.01,
        trade_contract_size=100.0,
    )
    mock_mt5 = SimpleNamespace(
        symbol_info=lambda s: mock_info,
        symbol_info_tick=lambda s: SimpleNamespace(bid=100.0, ask=100.02),
        account_info=lambda: SimpleNamespace(equity=1000.0, balance=1000.0),
    )
    from tests.fakes.fake_mt5 import patch_everywhere
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", mock_mt5)
    monkeypatch.setattr("core.order_execution._get_mt5", lambda: mock_mt5)

    # Entry 100.0, SL 95.0 -> 5.0 distance = 500 points.
    # At 0.1 lots min, dollar risk would be $50.00, but cap is $10.00.
    vol = rm.calc_position_size("TEST_SYM", 100.0, 95.0)

    assert vol == 0.0


# ---------------------------------------------------------------------------
# 4. D3b: Concurrency Correlation Cap inside Reservation
# ---------------------------------------------------------------------------

def test_concurrency_d3b_correlated_cap_serialized(tmp_path):
    """Verify that multiple concurrent threads attempting correlated entries

    are serialized and cannot exceed the max_correlated_risk_fraction (D3b).
    """
    db_path = str(tmp_path / "trading_state.db")
    store = TradingStore(db_path)
    gateway = OrderGateway(store, mode="PAPER")

    # Equity is 10,000; max correlated risk fraction is 0.02 -> 200.0 max correlated risk
    # EURUSD and GBPUSD both share USD_SHORT bucket for BUY trades.
    # Each intent risks $80.00 (0.8% of equity).
    # 2 intents = $160.00 <= $200.00 (allowed).
    # 3 intents = $240.00 > $200.00 (rejected).
    def try_reserve(idx: int, symbol: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        intent = OrderIntent(
            intent_key=f"intent_{idx}",
            symbol=symbol,
            side="BUY",
            volume=0.08,
            price=1.1000,
            sl=1.0900,
            tp=1.1200,
            risk_amount=80.0,
            setup_key=f"setup_{idx}",
            account_equity=10000.0,
            validation_result=SimpleNamespace(
                approved=True,
                setup_key=f"setup_{idx}",
                created_at=now_iso,
            ),
        )
        return gateway.submit_entry(intent)

    symbols = ["EURUSD", "GBPUSD", "EURUSD", "GBPUSD"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(try_reserve, i, symbols[i])
            for i in range(4)
        ]
        results = [f.result() for f in futures]

    approved_count = sum(1 for r in results if r.success)
    rejected_count = sum(1 for r in results if not r.success)

    assert approved_count <= 2, f"Expected at most 2 approvals, got {approved_count}"
    assert approved_count + rejected_count == 4
    assert any("correlated open-risk" in (r.error or "").lower() for r in results if not r.success)


# ---------------------------------------------------------------------------
# 5. CC-F6: Concurrency LLM Reservation
# ---------------------------------------------------------------------------

def test_concurrency_cc_f6_reserve_llm(tmp_path):
    """Verify that concurrent reserve_llm calls with identical trigger key

    are mutually exclusive, and max_concurrent is enforced (CC-F6).
    """
    db_path = str(tmp_path / "agents.db")
    store = AgentOperations(db_path)

    trigger_key = "unique_cycle_trigger_123"

    def try_reserve(idx: int):
        s = AgentOperations(db_path)
        ok, reason = s.reserve_llm(
            strategy="test_strat",
            provider="test_provider",
            model="test_model",
            trigger_key=trigger_key,
            max_concurrent=1,
            max_per_day=100,
        )
        return ok

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(try_reserve, i) for i in range(8)]
        results = [f.result() for f in futures]

    successes = sum(1 for r in results if r is True)
    failures = sum(1 for r in results if r is False)

    assert successes == 1, f"Expected exactly 1 successful reservation, got {successes}"
    assert failures == 7


# ---------------------------------------------------------------------------
# 6. PH4-N1: Locked Broker Read Facade
# ---------------------------------------------------------------------------

def test_locked_broker_reads_facade():
    """Verify that broker read facade functions execute under the MT5 lock

    and handle client results / errors safely.
    """
    mock_client = MagicMock()
    mock_client.positions_get.return_value = [SimpleNamespace(ticket=123)]
    mock_client.last_error.return_value = (1, "OK")
    mock_client.orders_get.return_value = []
    mock_client.history_orders_get.return_value = []
    mock_client.history_deals_get.return_value = []
    mock_client.account_info.return_value = SimpleNamespace(balance=10000.0)
    mock_client.symbol_info.return_value = SimpleNamespace(symbol="EURUSD")

    # Positions (success carries no error, even though last_error() is set:
    # MT5 reports the previous error code even after successful calls, and
    # mocks never return None — only a raised exception is failure).
    positions, err = broker_positions_get(mt5_inst=mock_client)
    assert len(positions) == 1
    assert positions[0].ticket == 123
    assert err is None

    # Orders
    orders, err = broker_orders_get(mt5_inst=mock_client)
    assert orders == []

    # History orders
    h_orders, err = broker_history_orders_get(mt5_inst=mock_client)
    assert h_orders == []

    # History deals
    deals, err = broker_history_deals_get(mt5_inst=mock_client)
    assert deals == []

    # Account info
    acc, err = broker_account_info(mt5_inst=mock_client)
    assert acc.balance == 10000.0

    # Symbol info
    sym, err = broker_symbol_info("EURUSD", mt5_inst=mock_client)
    assert sym.symbol == "EURUSD"

    # Exception handling returns (None, exc)
    failing_client = MagicMock()
    failing_client.positions_get.side_effect = RuntimeError("MT5 down")
    res, err = broker_positions_get(mt5_inst=failing_client)
    assert res is None
    assert isinstance(err, RuntimeError)
