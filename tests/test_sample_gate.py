from sample_gate import evaluate, report


def test_sample_gate_requires_sample_and_multiple_known_regimes():
    rows = [{"strategy": "scalp", "symbol": "XAUUSD", "setup_id": "setup_v1",
             "closed_at": "2026-01-01T00:00:00+00:00",
             "features": {"regime": "TREND"}}]
    result = evaluate(rows, minimum_samples=2, minimum_regimes=2)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["setup_classes"][0]["eligible"] is False
    assert any("closed labelled rows" in reason for reason in result["setup_classes"][0]["reasons"])


def test_sample_gate_requires_point_in_time_dataset(tmp_path):
    result = report(str(tmp_path / "missing.db"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["execution_authority"] is False
