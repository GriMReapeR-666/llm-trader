"""The risk manager's side of the autonomous sizing route.

The route may only ever reduce the risk amount, so these tests are mostly
about the one property that must never break: the new route can never raise
risk above the rule that is in production today.
"""
from core import sizing_route
from core.risk_manager import RiskManager


EQUITY = 100_000.0


class _Spec:
    trade_contract_size = 100_000.0


def _manager():
    return RiskManager(EQUITY, state_file=RiskManager.EPHEMERAL_STATE)


def _green_decision():
    return sizing_route.RouteDecision(route=sizing_route.CARVER, reasons=())


def _legacy_decision():
    return sizing_route.RouteDecision(route=sizing_route.LEGACY, reasons=("no evidence",))


def test_legacy_route_leaves_the_risk_amount_untouched(monkeypatch):
    monkeypatch.setattr(sizing_route, "current_route", lambda **_: _legacy_decision())
    manager = _manager()
    risk = manager._apply_autonomous_sizing_route(
        1000.0, symbol="EURUSD", entry=1.1000, sl=1.0980, equity=EQUITY,
        alpha_composite=2.0, atr=0.0020,
    )
    assert risk == 1000.0


def test_carver_route_can_only_reduce_the_risk(monkeypatch):
    monkeypatch.setattr(sizing_route, "current_route", lambda **_: _green_decision())
    manager = _manager()
    monkeypatch.setattr(RiskManager, "_symbol_spec", staticmethod(lambda symbol: _Spec()))
    risk = manager._apply_autonomous_sizing_route(
        1000.0, symbol="EURUSD", entry=1.1000, sl=1.0980, equity=EQUITY,
        alpha_composite=2.0, atr=0.0020,
    )
    assert 0.0 < risk < 1000.0
    # The route's own limit is 1% of equity.
    assert risk <= EQUITY * 0.01


def test_carver_route_without_a_forecast_leaves_the_size_alone(monkeypatch):
    monkeypatch.setattr(sizing_route, "current_route", lambda **_: _green_decision())
    manager = _manager()
    monkeypatch.setattr(RiskManager, "_symbol_spec", staticmethod(lambda symbol: _Spec()))
    risk = manager._apply_autonomous_sizing_route(
        1000.0, symbol="EURUSD", entry=1.1000, sl=1.0980, equity=EQUITY,
        alpha_composite=0.0, atr=0.0020,
    )
    assert risk == 1000.0


def test_carver_route_without_a_symbol_spec_leaves_the_size_alone(monkeypatch):
    monkeypatch.setattr(sizing_route, "current_route", lambda **_: _green_decision())
    manager = _manager()
    monkeypatch.setattr(RiskManager, "_symbol_spec", staticmethod(lambda symbol: None))
    risk = manager._apply_autonomous_sizing_route(
        1000.0, symbol="EURUSD", entry=1.1000, sl=1.0980, equity=EQUITY,
        alpha_composite=2.0, atr=0.0020,
    )
    assert risk == 1000.0


def test_carver_route_without_volatility_leaves_the_size_alone(monkeypatch):
    monkeypatch.setattr(sizing_route, "current_route", lambda **_: _green_decision())
    manager = _manager()
    monkeypatch.setattr(RiskManager, "_symbol_spec", staticmethod(lambda symbol: _Spec()))
    risk = manager._apply_autonomous_sizing_route(
        1000.0, symbol="EURUSD", entry=1.1000, sl=1.0980, equity=EQUITY,
        alpha_composite=2.0, atr=None,
    )
    assert risk == 1000.0


def test_a_failing_route_decision_never_raises(monkeypatch):
    def boom(**_):
        raise RuntimeError("evidence store unavailable")

    monkeypatch.setattr(sizing_route, "current_route", boom)
    manager = _manager()
    risk = manager._apply_autonomous_sizing_route(
        1000.0, symbol="EURUSD", entry=1.1000, sl=1.0980, equity=EQUITY,
        alpha_composite=2.0, atr=0.0020,
    )
    assert risk == 1000.0


def test_the_route_is_consulted_by_the_position_size_path(monkeypatch):
    """The wiring exists: sizing goes through the route decision."""
    monkeypatch.setattr(sizing_route, "current_route", lambda **_: _legacy_decision())
    manager = _manager()
    seen = {}

    def spy(_self, risk_amt, **kwargs):
        seen["called"] = True
        return risk_amt

    monkeypatch.setattr(RiskManager, "_apply_autonomous_sizing_route", spy)
    manager.opportunity_cost_scalar = lambda: 1.0
    manager.quarter_kelly_scalar = lambda: 1.0
    manager.calc_position_size("EURUSD", 1.1000, 1.0980, confidence=0.95,
                               atr=0.0020, alpha_composite=1.0)
    assert seen.get("called") is True
