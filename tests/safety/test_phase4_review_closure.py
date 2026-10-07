"""Pins for the Phase 4 review fixes that §8 claimed but nothing tested.

docs/audit/REVIEW_PHASE4_2026-10-05.md §9 has the re-review. Covers:
- H1: the arbiter's daily loss window starts at broker midnight, not UTC midnight.
- H2: proposal size scales apply before lot rounding; the gateway refuses off-step volume.
- H3: outside PAPER, a missing tick is an invalid clock (NO_TICK), never PAPER_SIMULATION.
- H4: a per-proposal heat budget only tightens the configured cap; NaN refuses.
- H5: a failing reconcile side effect doesn't lose the risk outcome or stall other tickets.
- B2: unknown live position count / heat blocks entries.
- M3: unparseable broker epochs raise instead of becoming now().
- B1/TL-T1 regression: a PASS verdict never enters the REDUCE branch.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest

import core.broker_time as broker_time
from core.order_gateway import OrderGateway, OrderIntent
from core.risk_manager import RiskManager
from core.trading_safety import PortfolioRiskArbiter, TradeProposal, TradingStore, ValidationResult
from intelligence.llm_orchestrator import LLMOrchestrator
from tests.safety.test_mode_correct_wiring import (  # noqa: F401  (fixtures)
    _closed_trades,
    _make_open_trade,
    fake_mt5,
    store,
)


@pytest.fixture
def broker_offset_plus_3h():
    saved = dict(broker_time._SERVER_OFFSET_CACHE)
    broker_time.set_broker_utc_offset_seconds(3 * 3600, ttl=3600)
    yield
    broker_time._SERVER_OFFSET_CACHE.clear()
    broker_time._SERVER_OFFSET_CACHE.update(saved)


# --------------------------------------------------------------------------- H1
def test_h1_broker_day_start_is_previous_utc_evening(broker_offset_plus_3h):
    now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)  # 13:00 broker time
    assert broker_time.broker_trading_day_start_utc(now) == datetime(2026, 10, 5, 21, 0, tzinfo=timezone.utc)
    # Tuesday 6 Oct broker → week began broker Monday 00:00 = Sunday 4 Oct 21:00 UTC
    assert broker_time.broker_trading_week_start_utc(now) == datetime(2026, 10, 4, 21, 0, tzinfo=timezone.utc)
    late = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)  # already 7 Oct 01:00 broker
    assert broker_time.broker_trading_day_start_utc(late) == datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)


def test_h1_arbiter_counts_losses_since_broker_midnight(store, broker_offset_plus_3h, monkeypatch):
    fixed_now = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)  # 04:00 broker, broker day began 21:00 UTC

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr("core.trading_safety.datetime", _FixedDT)
    _make_open_trade(store, ticket=4242)
    # Loss at 22:30 UTC on 5 Oct: before UTC midnight, inside the broker day.
    assert store.record_close(4242, 1.0950, -400.0, "test",
                              closed_at=datetime(2026, 10, 5, 22, 30, tzinfo=timezone.utc))
    arbiter = PortfolioRiskArbiter(store, daily_loss_fraction=0.03)
    ok, reason = arbiter.permit(equity=10_000.0, new_risk=10.0)
    assert not ok and reason == "daily loss limit"


# --------------------------------------------------------------------------- H2
def _sizing_mt5():
    info = NS(volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_tick_size=0.00001,
              trade_tick_value=1.0, point=0.00001, trade_contract_size=100_000.0)
    return NS(
        symbol_info=lambda s: info,
        symbol_info_tick=lambda s: NS(bid=1.1, ask=1.1001),
        positions_get=lambda *a, **k: [],
        orders_get=lambda *a, **k: [],
        account_info=lambda: NS(equity=10_000.0, balance=10_000.0),
    )


def test_h2_sizing_scale_applies_before_lot_rounding(monkeypatch):
    from tests.fakes.fake_mt5 import patch_everywhere
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", _sizing_mt5())
    rm = RiskManager(balance=10_000.0, risk_per_trade=0.01)
    full = rm.calc_position_size("EURUSD", 1.1000, 1.0950)
    scaled = rm.calc_position_size("EURUSD", 1.1000, 1.0950, sizing_scale=0.37)
    assert full > 0
    assert 0 < scaled <= full * 0.37 + 1e-9
    assert abs(scaled / 0.01 - round(scaled / 0.01)) < 1e-6, f"off-step volume {scaled}"
    # A scale that would land below the broker minimum refuses instead of sending 0.002 lots.
    assert rm.calc_position_size("EURUSD", 1.1000, 1.0950, sizing_scale=0.01) == 0.0


@pytest.mark.parametrize("volume,needle", [(0.015, "off-step"), (0.003, "below minimum")])
def test_h2_gateway_refuses_invalid_broker_volume(tmp_path, volume, needle):
    s = TradingStore(str(tmp_path / "g.db"))
    s.set_state("equity", 10_000.0)
    gw = OrderGateway(s, mode="PAPER")
    broker = NS(symbol_info=lambda sym: NS(volume_min=0.01, volume_max=100.0, volume_step=0.01))
    res = gw.submit_entry(
        OrderIntent(intent_key="v", symbol="EURUSD", side="BUY", volume=volume, price=1.1, sl=1.09, tp=1.12,
                    risk_amount=10.0, setup_key="s", order_type="buy_limit",
                    validation_result=ValidationResult(approved=True, reasons=[], confluence_score=5, setup_key="s")),
        mt5_client=broker,
    )
    assert not res.success and needle in (res.error or "")


# --------------------------------------------------------------------------- H3
def test_h3_missing_live_tick_is_invalid_clock(store, fake_mt5):
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    orch = LLMOrchestrator(client=fake_mt5, risk_manager=risk, perf_monitor=MagicMock(),
                           api_key="k", journal_mode="LIVE", store=store)
    orch.overfit_gate = MagicMock()
    orch.overfit_gate.check_strategy.return_value = NS(strategy="swing", status="PASS", risk_level="LOW",
                                                       grade="A", reason="ok", checked_at=0)
    seen = {}

    def _capture(toon, **kw):
        seen["clock"] = dict(toon["context"]["clock_health"])
        return MagicMock(passed=False, reasons=["stop here"])

    orch.data_quality = MagicMock()
    orch.data_quality.validate.side_effect = _capture
    decision = {"action": "BUY", "entry": 1.1, "stop_loss": 1.095, "take_profit": 1.115, "confidence": 0.85}
    # The provider handed over a stale paper default: it must not count as a valid live clock.
    toon = {"dom": {}, "ohlc": [], "indicators": {}, "news": [],
            "context": {"clock_health": {"valid": True, "status": "PAPER_SIMULATION", "offset_seconds": 0.0}}}
    with patch("mt5_trader.classify_setup", return_value={"setup_id": "t", "eligible": True, "reason": "ok", "spec": {"min_rr": 1.5}}), \
         patch("mt5_trader.evaluate_data_requirements", return_value={"available": True, "reasons": []}), \
         patch("mt5_trader.evaluate_market_conditions", return_value=MagicMock(allowed=True, reasons=[])):
        orch._validate_proposal(decision, toon, "EURUSD", "swing", paper_mode=False)
    assert seen.get("clock") == {"valid": False, "status": "NO_TICK", "offset_seconds": 0.0}


# --------------------------------------------------------------------------- H4
def test_h4_proposal_heat_budget_only_tightens(monkeypatch):
    from tests.fakes.fake_mt5 import patch_everywhere
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", _sizing_mt5())
    rm = RiskManager(balance=10_000.0, risk_per_trade=0.01)
    heat = {"r": 2.5}
    rm.set_portfolio_heat_provider(lambda: heat["r"])
    assert rm.calc_position_size("EURUSD", 1.1, 1.095) > 0  # 2.5 + 1 <= 4
    assert rm.calc_position_size("EURUSD", 1.1, 1.095, heat_budget_r=3.0) == 0.0  # 2.5 + 1 > 3
    heat["r"] = 3.5
    assert rm.calc_position_size("EURUSD", 1.1, 1.095, heat_budget_r=99.0) == 0.0  # can't widen past 4R
    assert rm.calc_position_size("EURUSD", 1.1, 1.095, heat_budget_r=float("nan")) == 0.0
    assert rm.max_portfolio_heat_r == rm.base_heat_budget_r  # no shared write


# --------------------------------------------------------------------------- B2
def test_b2_unknown_live_counts_block_entries(monkeypatch):
    from tests.fakes.fake_mt5 import patch_everywhere
    patch_everywhere(monkeypatch, "core/risk_manager.py", "mt5", _sizing_mt5())
    rm = RiskManager(balance=10_000.0, risk_per_trade=0.01)
    rm.set_active_positions_provider(lambda: None)
    assert rm.check_active_trade_limit() is False
    rm.set_portfolio_heat_provider(lambda: None)
    assert rm.calc_position_size("EURUSD", 1.1, 1.095) == 0.0


# --------------------------------------------------------------------------- H5
def test_h5_side_effect_failure_keeps_outcome_and_other_tickets(store, fake_mt5):
    risk = RiskManager(10_000.0, risk_per_trade=0.01)
    risk.set_store(store)
    perf = MagicMock()
    perf.history = []
    perf.close_trade.side_effect = RuntimeError("perf down")
    orch = LLMOrchestrator(client=fake_mt5, risk_manager=risk, perf_monitor=perf,
                           api_key="k", journal_mode="LIVE", store=store)
    orch.trade_memory = MagicMock()
    orch.trade_memory.label_closed_trade.side_effect = RuntimeError("memory down")
    for t in (601, 602):
        _make_open_trade(store, ticket=t)
    fake_mt5.positions = []
    fake_mt5.deals = [
        NS(ticket=7001, position_id=601, entry=1, profit=-20.0, commission=0.0, swap=0.0, time=1_700_000_000,
           symbol="EURUSD", volume=0.1, price=1.0980, order=1),
        NS(ticket=7002, position_id=602, entry=1, profit=-30.0, commission=0.0, swap=0.0, time=1_700_000_000,
           symbol="EURUSD", volume=0.1, price=1.0970, order=2),
    ]
    # FakeMT5 ignores the position filter; serve each ticket its own deal.
    fake_mt5.history_deals_get = lambda *a, position=None, **k: [d for d in fake_mt5.deals if d.position_id == position]
    with patch.object(risk, "record_trade_outcome", wraps=risk.record_trade_outcome) as outcome:
        orch.reconcile_open_positions()
        orch.reconcile_open_positions()  # second pass must not double-count
    assert sorted(int(r["ticket"]) for r in _closed_trades(store)) == [601, 602]
    assert outcome.call_count == 2


# --------------------------------------------------------------------------- M3
@pytest.mark.parametrize("bad", [None, "abc", 0, -5, float("nan"), float("inf")])
def test_m3_bad_broker_epoch_raises(bad):
    with pytest.raises(ValueError):
        broker_time.broker_epoch_to_utc(bad, mt5_inst=None)


# ------------------------------------------------------------- REDUCE branch
def test_pass_verdict_does_not_enter_reduce_branch():
    import inspect
    src = inspect.getsource(LLMOrchestrator._validate_proposal)
    assert "REUCE" not in src
    assert "if constraint_eval.overall_verdict == ConstraintVerdict.REDUCE:" in src


# ------------------------------------------- trade_manager pyramid equity read
def test_trade_manager_pyramid_authorization_can_read_live_equity():
    """The PH4-N1 migration called broker_account_info without importing it, so the
    NameError was swallowed and pyramid authorization silently used the cached balance."""
    import core.trade_manager as tm
    assert callable(getattr(tm, "broker_account_info", None))
