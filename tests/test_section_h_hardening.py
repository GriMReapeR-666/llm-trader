import pytest

from margin_safety import MarginSafetyGate, MarginRiskPolicy
from baseline_research import _probability_metrics
from journal_audit import qualified_metrics


def test_margin_safety_gate_evaluation():
    gate = MarginSafetyGate(MarginRiskPolicy(
        max_margin_to_equity=0.35,
        min_free_margin_to_equity=0.50,
        max_new_symbol_margin_to_equity=0.20,
    ))

    # Valid account snapshot with healthy equity, free margin, and stop-out level
    valid_account = {
        "equity": 10000.0,
        "margin": 1000.0,
        "margin_free": 9000.0,
        "margin_so_mode": 0,
        "margin_so_so": 50.0,
    }
    
    assessment = gate.assess(
        account=valid_account,
        new_margin=500.0,
        new_risk=100.0,
        symbol="EURUSD",
        driver_buckets=["EUR"],
        position_margin_estimates=[{"symbol": "EURUSD", "buckets": ["EUR"], "margin": 1500.0}],
        mode="mt5",
    )
    assert assessment.approved is True
    assert assessment.projected_margin == 1500.0
    assert assessment.projected_free_margin == 8500.0

    # Excessively low free margin scenario -> Rejection
    low_free_margin_account = {
        "equity": 10000.0,
        "margin": 6000.0,
        "margin_free": 4000.0,  # 40% < 50% required
        "margin_so_mode": 0,
        "margin_so_so": 50.0,
    }
    rejection = gate.assess(
        account=low_free_margin_account,
        new_margin=500.0,
        new_risk=100.0,
        symbol="EURUSD",
        driver_buckets=["EUR"],
        position_margin_estimates=[{"symbol": "EURUSD", "buckets": ["EUR"], "margin": 6500.0}],
        mode="mt5",
    )
    assert rejection.approved is False
    assert any("free-margin" in reason.lower() for reason in rejection.reasons)


def test_probability_metrics_and_calibration():
    # Synthetic trade rows and predicted probabilities
    rows = [
        {"label": "WIN", "r_multiple": 2.0},
        {"label": "WIN", "r_multiple": 1.5},
        {"label": "LOSS", "r_multiple": -1.0},
        {"label": "LOSS", "r_multiple": -1.0},
    ]
    probs = [0.8, 0.7, 0.3, 0.2]

    metrics = _probability_metrics(rows, probs, threshold=0.5)
    assert metrics["sample_size"] == 4
    assert metrics["accuracy"] == 1.0  # All predictions correct at >= 0.5 threshold
    assert metrics["brier"] == pytest.approx(0.065, abs=1e-3)
    assert metrics["expectancy_r"] == pytest.approx(0.375, abs=1e-3)
    assert metrics["net_return_r"] == 1.5

    # Empty inputs handling
    empty_metrics = _probability_metrics([], [])
    assert empty_metrics["sample_size"] == 0
    assert empty_metrics["brier"] is None
    assert empty_metrics["expectancy_r"] is None


def test_qualified_metrics_reporting(tmp_path):
    db_file = str(tmp_path / "test_journal.db")
    report = qualified_metrics(db_file)
    assert isinstance(report, dict)
    assert report["sample_size"] == 0
    assert "trade_quality" in report
