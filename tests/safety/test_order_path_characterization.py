"""Characterization of today's order path, recorded before the Phase 2 gateway.

These tests pin CURRENT behaviour, including behaviour the audit says is
wrong (each such test names its roadmap ID). Phase 2 changes this path; any
difference must show up here and be accepted deliberately in that PR.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from tests.fakes.fake_mt5 import FakeMT5, patch_everywhere

from core.order_execution import PaperBroker, place_pending_order

EXPIRES = lambda: datetime.now(timezone.utc) + timedelta(hours=1)  # noqa: E731


@pytest.fixture
def broker(monkeypatch):
    fake = FakeMT5()
    assert patch_everywhere(monkeypatch, "core/order_execution.py", "_get_mt5", lambda: fake)
    patch_everywhere(monkeypatch, "core/order_execution.py", "mt5", fake)
    return fake


def _pending(**kw):
    args = dict(symbol="EURUSD", otype="buy_limit", price=1.0990, volume=0.1, sl=1.0940,
                tp=1.1090, comment="char", paper=None, magic=460002, expires_at=EXPIRES())
    args.update(kw)
    return place_pending_order(**args)


def test_paper_pending_order_stays_in_the_paper_book(broker):
    paper = PaperBroker()
    result = _pending(paper=paper)
    assert broker.sent == []
    assert result["retcode"] == 10009 and result["status"] == "PENDING"
    order = paper.orders[result["order"]]
    assert (order["type"], order["price"], order["sl"], order["tp"], order["magic"]) == \
        ("buy_limit", 1.0990, 1.0940, 1.1090, 460002)


def test_live_pending_order_without_capability_token_is_refused(broker):
    with pytest.raises(PermissionError):
        _pending(paper=None, token=None)
    assert broker.sent == []


def test_live_pending_order_with_token_is_refused_without_validation_evidence(broker):
    """TL-T1 / L4 + single-door rule: a capability token alone is not enough to
    reach order_send from the pending path. The order is refused unless a
    DeterministicValidator verdict, setup key and risk amount accompany it.

    Renamed from ``..._by_missing_enum_today``: the enum-era guard it described
    was replaced by the (stronger) single-door evidence guard."""
    token = NS(can_access=lambda level, scope="": True)
    with pytest.raises(RuntimeError, match="single-door rule"):
        _pending(paper=None, token=token)
    assert broker.sent == []


def test_marketable_pending_geometry_is_refused_before_any_send(broker):
    with pytest.raises(ValueError):
        _pending(price=1.1010, paper=PaperBroker())   # buy_limit above ask
    assert broker.sent == []


def test_paper_pyramid_never_reaches_the_broker(monkeypatch, tmp_path, broker):
    from core import trade_manager as tm_mod
    patch_everywhere(monkeypatch, "core/trade_manager.py", "mt5", broker)
    broker.bid, broker.ask = 1.1030, 1.1031
    paper = PaperBroker()
    paper.market_order("EURUSD", "BUY", 0.2, 1.1000, 1.0990, 1.1100, "t", magic=460001)
    risk = MagicMock(risk_per_trade=0.01)
    risk.pending_flatten_reason.return_value = None
    arbiter = MagicMock()
    arbiter.permit.return_value = (True, "ok")
    tm = tm_mod.TradeManager(client=None, risk_manager=risk, paper_broker=paper, arbiter=arbiter,
                             state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "c.json"))
    tm.partial_tp_enabled = False
    tm.time_stop_enabled = False
    tm.monitor_positions("EURUSD", 1.1030, 0.0005, indicators={"adx": [30.0]})
    assert broker.sent == []
