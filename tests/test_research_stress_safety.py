import math
from pathlib import Path

from research_stress import monte_carlo, stress_report


def test_monte_carlo_rejects_nonfinite_and_unsafe_inputs():
    assert monte_carlo([1.0, math.nan])["status"] == "INVALID_INPUT"
    assert monte_carlo([1.0], risk_fraction=1.0)["status"] == "INVALID_INPUT"
    assert monte_carlo([1.0], paths=1.5)["status"] == "INVALID_INPUT"


def test_stress_report_enforces_sample_floor_and_cost_buffers(tmp_path: Path):
    low = stress_report(str(tmp_path / "stress.db"), minimum_samples=10)
    assert low["status"] == "INVALID_INPUT"
    assert low["minimum_samples"] == 100

    malformed = stress_report(str(tmp_path / "stress.db"), cost_buffers_r=[0.0, math.inf])
    assert malformed["status"] == "INVALID_INPUT"
    assert malformed["scenarios"] == []

