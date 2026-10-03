import json

import forward_test_report
from forward_test_report import MIN_FORWARD_SAMPLE, compare


def _report(tmp_path, payload):
    path = tmp_path / "backtest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _qualified(sample=100, cost_model="broker-replay-v1"):
    return {
        "sample_size": sample,
        "cost_model": cost_model,
        "date_range": {"start": "2025-01-01", "end": "2026-01-01"},
        "portfolio_quality": {"closed_net_pnl": 12.0},
        "trade_quality": {"expectancy_currency": 0.12, "expectancy_r": 0.2},
    }


def test_forward_report_rejects_threshold_below_research_floor(tmp_path):
    result = compare(_report(tmp_path, {"closed_trades": 100, "net_pnl": 1,
                                        "cost_model": "broker-replay-v1"}),
                     minimum_demo_sample=30)
    assert result["status"] == "INVALID_INPUT"
    assert result["execution_authority"] is False
    assert result["required_minimum_demo_sample"] == MIN_FORWARD_SAMPLE


def test_forward_report_malformed_and_uncosted_backtest_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_test_report, "qualified_metrics", lambda path: _qualified())
    result = compare(_report(tmp_path, {"closed_trades": "not-a-number", "net_pnl": "nan"}))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["execution_authority"] is False
    assert result["backtest"]["valid"] is False


def test_forward_report_requires_large_cost_aware_samples_and_records_cost_match(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_test_report, "qualified_metrics", lambda path: _qualified())
    payload = {
        "qualified_metrics": {
            "sample_size": 100,
            "cost_model": "broker-replay-v1",
            "portfolio_quality": {"closed_net_pnl": 10.0},
            "trade_quality": {"expectancy_currency": 0.1, "expectancy_r": 0.15},
        }
    }
    result = compare(_report(tmp_path, payload))
    assert result["status"] == "READY_FOR_OPERATOR_REVIEW"
    assert result["cost_model_comparison"] == {"match": True, "requires_operator_review": False}
    assert result["discrepancy"]["net_pnl"] == 2.0
    assert result["execution_authority"] is False
