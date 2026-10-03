import pytest

from account_growth import (
    equity_curve,
    growth_report_from_r_multiples,
    project_growth,
    rolling_expectancy,
    suggest_risk_per_trade,
)


def test_equity_curve_drawdown():
    curve = equity_curve([10.0, -5.0, -10.0, 3.0], starting_equity=100.0)
    assert curve["terminal_equity"] == pytest.approx(98.0)
    assert curve["max_drawdown"] == pytest.approx(15.0)
    assert curve["execution_authority"] is False
    assert curve["kind"] == "descriptive"


def test_rolling_expectancy_degradation():
    # Prior window strong, current weak.
    pnls = [2.0] * 20 + [-1.0] * 20
    roll = rolling_expectancy(pnls, window=20)
    assert roll["status"] == "OK"
    assert roll["current"] == pytest.approx(-1.0)
    assert roll["prior"] == pytest.approx(2.0)
    assert roll["degradation"] == pytest.approx(1.5)
    assert roll["execution_authority"] is False


def test_suggest_risk_throttles_on_degradation_and_negative_edge():
    ok = suggest_risk_per_trade(
        base_risk=0.01,
        fractional_kelly=0.02,
        expectancy_r=0.2,
        degradation=0.0,
    )
    assert ok["suggested_risk_per_trade"] == pytest.approx(0.01)
    assert ok["execution_authority"] is False

    degraded = suggest_risk_per_trade(
        base_risk=0.01,
        fractional_kelly=0.02,
        expectancy_r=0.2,
        degradation=0.40,
    )
    assert degraded["suggested_risk_per_trade"] == pytest.approx(0.005)
    assert "rolling_expectancy_degraded" in degraded["reasons"]

    dead = suggest_risk_per_trade(
        base_risk=0.01,
        fractional_kelly=0.0,
        expectancy_r=-0.1,
        degradation=None,
    )
    assert dead["suggested_risk_per_trade"] == pytest.approx(0.001)
    assert "non_positive_expectancy_r" in dead["reasons"]


def test_project_growth_is_deterministic_and_fail_closed_authority():
    a = project_growth(
        starting_equity=10_000,
        trades=50,
        win_rate=0.55,
        avg_win_r=1.5,
        avg_loss_r=1.0,
        risk_fraction=0.01,
        kill_drawdown_fraction=0.20,
        seed=7,
        paths=200,
    )
    b = project_growth(
        starting_equity=10_000,
        trades=50,
        win_rate=0.55,
        avg_win_r=1.5,
        avg_loss_r=1.0,
        risk_fraction=0.01,
        kill_drawdown_fraction=0.20,
        seed=7,
        paths=200,
    )
    assert a == b
    assert a["kind"] == "simulated"
    assert a["execution_authority"] is False
    assert 0.0 <= a["ruin_or_kill_rate"] <= 1.0
    assert a["mean_terminal_equity"] > 0
    assert a["expectancy_r"] == pytest.approx(0.55 * 1.5 - 0.45 * 1.0)


def test_growth_report_from_r_multiples_end_to_end():
    # Enough samples for kelly eligibility path still stays advisory.
    rs = [1.5, 1.2, -1.0, 2.0, -1.0] * 25  # 125 trades
    report = growth_report_from_r_multiples(
        rs,
        starting_equity=10_000,
        risk_fraction=0.01,
        base_risk=0.01,
        projection_trades=40,
        seed=1,
    )
    assert report["sample_size"] == 125
    assert report["expectancy_r"] > 0
    assert report["equity_curve"]["points"] == 126
    assert report["risk_throttle"]["execution_authority"] is False
    assert report["projection"] is not None
    assert report["projection"]["execution_authority"] is False
    assert report["execution_authority"] is False


def test_growth_controller_refresh_and_scale():
    from account_growth import AccountGrowthController

    class Store:
        def __init__(self):
            self.path = "missing-does-not-matter.db"
            self.data = {}

        def set_state(self, key, value):
            self.data[key] = value

        def get_state(self, key, default=None):
            return self.data.get(key, default)

    # Missing db -> no broker-evidenced trades -> INSUFFICIENT_DATA.
    # With no measured expectancy the controller must size DOWN rather than
    # hold the operator base, which would assert an edge nothing measured.
    c = AccountGrowthController(Store(), base_risk=0.01)
    out = c.refresh()
    assert out["status"] == "INSUFFICIENT_DATA"
    assert out["sample_size"] == 0
    assert "expectancy_unknown_no_measured_evidence" in (out.get("reasons") or [])
    assert out["suggested_risk_per_trade"] < c.base_risk
    assert c.arbiter_risk_budget_scale() == c.UNKNOWN_RISK_RATIO
