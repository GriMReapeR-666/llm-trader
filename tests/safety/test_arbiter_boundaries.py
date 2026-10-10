"""Pin PortfolioRiskArbiter.permit at each limit: just below, at, just above.

Mutation testing showed the daily, weekly, open-risk and correlated
comparisons (`>=` vs `>`, `+` vs `-`, `*` vs `/`) could all be flipped
without a test failing. Equity is 10,000 throughout, so with the default
fractions: daily 300, weekly 600, drawdown 10 %, open risk 300, correlated 200.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.safety._clock import freeze
from core.trading_safety import PortfolioRiskArbiter, TradeProposal, TradingStore

EQUITY = 10_000.0
EPS = 0.01
# A Wednesday, so "earlier this week" and "today" are different days.
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    freeze(monkeypatch, "core/trading_safety.py", NOW)


@pytest.fixture
def store(tmp_path):
    return TradingStore(str(tmp_path / "state.db"))


@pytest.fixture
def arbiter(store):
    return PortfolioRiskArbiter(store)


def _proposal(n):
    return TradeProposal(strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1,
                         stop_loss=1.09, take_profit=1.12, confidence=.8,
                         features={"setup_id": f"s{n}"})


_ticket = iter(range(1, 10_000))


def _open(store, risk):
    t = next(_ticket)
    store.record_open_trade(t, _proposal(t), 0.1, risk, {})
    return t


def _closed_loss(store, loss, when):
    t = _open(store, 0.0)
    store.record_close(t, 1.09, -loss, "stop", closed_at=when)


def _permit(arbiter, new_risk=0.0, **kw):
    return arbiter.permit(EQUITY, new_risk, **kw)


def test_clean_book_is_approved(arbiter):
    assert _permit(arbiter, 100.0) == (True, "approved")


@pytest.mark.parametrize("loss,ok", [(300 - EPS, True), (300, False), (300 + EPS, False)],
                         ids=["below", "at", "above"])
def test_daily_loss_limit_boundary(store, arbiter, loss, ok):
    _closed_loss(store, loss, NOW - timedelta(hours=1))
    allowed, reason = _permit(arbiter, 1.0)
    assert allowed is ok, reason
    if not ok:
        assert reason == "daily loss limit"


@pytest.mark.parametrize("loss,ok", [(600 - EPS, True), (600, False), (600 + EPS, False)],
                         ids=["below", "at", "above"])
def test_weekly_loss_limit_boundary(store, arbiter, loss, ok):
    # Monday of this week: counts toward the week, not toward today.
    _closed_loss(store, loss, NOW - timedelta(days=2))
    allowed, reason = _permit(arbiter, 1.0)
    assert allowed is ok, reason
    if not ok:
        assert reason == "weekly loss limit"


def test_wins_do_not_offset_realized_losses(store, arbiter):
    _closed_loss(store, 300, NOW - timedelta(hours=1))
    t = _open(store, 0.0)
    store.record_close(t, 1.12, 500.0, "target", closed_at=NOW - timedelta(minutes=30))
    assert _permit(arbiter, 1.0) == (False, "daily loss limit")


@pytest.mark.parametrize("dd,ok", [(0.10 - 1e-6, True), (0.10, False), (0.15, False)],
                         ids=["below", "at", "above"])
def test_max_drawdown_boundary_and_latches_kill_switch(store, arbiter, dd, ok):
    assert _permit(arbiter, 1.0)[0]  # records peak = 10,000
    allowed, reason = arbiter.permit(EQUITY * (1 - dd), 1.0)
    assert allowed is ok, reason
    assert store.is_kill_switch_active() is (not ok)
    if not ok:
        assert reason == "maximum drawdown kill switch"


@pytest.mark.parametrize("open_risk,new_risk,ok", [
    (200, 100, True),          # 300 == cap -> allowed
    (200, 100 + EPS, False),   # just over
    (300, 0, True),            # at cap with no new risk
    (0, 300 + EPS, False),     # a single oversized trade
], ids=["at_cap", "over_by_eps", "full_no_new", "single_oversized"])
def test_open_risk_cap_boundary(store, arbiter, open_risk, new_risk, ok):
    if open_risk:
        _open(store, open_risk)
    allowed, reason = _permit(arbiter, new_risk)
    assert allowed is ok, reason
    if not ok:
        assert reason == "portfolio open-risk limit"


def test_closed_trades_release_open_risk(store, arbiter):
    t = _open(store, 300)
    assert _permit(arbiter, 1.0) == (False, "portfolio open-risk limit")
    store.record_close(t, 1.12, 50.0, "target", closed_at=NOW)
    assert _permit(arbiter, 1.0)[0]


@pytest.mark.parametrize("correlated,ok", [(100, True), (100 + EPS, False)], ids=["at", "over"])
def test_correlated_exposure_boundary(arbiter, correlated, ok):
    allowed, reason = _permit(arbiter, 100.0, correlated_risk=correlated)
    assert allowed is ok, reason
    if not ok:
        assert reason == "rolling correlated exposure risk limit"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, "abc", None])
def test_invalid_inputs_fail_closed(arbiter, bad):
    assert arbiter.permit(bad, 1.0)[0] is False
    assert arbiter.permit(EQUITY, bad)[0] is False


def test_pending_order_intents_count_toward_open_risk(store, arbiter):
    from operations.agent_operations import AgentOperations
    ops = AgentOperations(store.path)
    created, _ = ops.create_order_intent("pending-1", {"risk_amount": 300.0, "symbol": "EURUSD"},
                                         actor="RiskController")
    assert created
    allowed, _reason = _permit(arbiter, 1.0)
    assert allowed is False
