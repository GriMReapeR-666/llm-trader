"""Profitability foundation report is advisory and fail-closed."""
from profitability_foundation import report, live_risk_bound, advisory_kelly_cap


def test_report_never_grants_authority(tmp_path):
    path = str(tmp_path / "pf.db")
    # Empty journal: still returns structured advisory payload.
    payload = report(path, operator_base=0.01)
    assert payload["execution_authority"] is False
    assert "status" in payload
    assert "sample_gate" in payload
    assert "kelly_cap" in payload
    assert "live_risk_bound" in payload


def test_live_risk_bound_is_min_of_caps():
    bound = live_risk_bound(
        operator_base=0.01,
        growth_advisory=0.005,
        tiny_live_cap=0.002,
        kelly_cap=0.05,
    )
    assert bound["status"] == "OK"
    assert bound["live_risk_fraction"] == 0.002
    assert bound["binding_cap"] == "tiny_live_cap"
    assert bound["execution_authority"] is False


def test_kelly_cap_never_exceeds_one_percent():
    metrics = [{"setup_id": "x", "kelly": {"fractional_kelly": 0.25}}]
    kelly = advisory_kelly_cap(metrics)
    assert kelly["advisory_risk_fraction_cap"] == 0.01
    assert kelly["execution_authority"] is False
