from baseline_research import baseline_report
from contextual_bandit import bandit_report
from supervised_training import train


def test_baseline_bandit_and_supervised_reject_weak_research_configuration(tmp_path):
    path = str(tmp_path / "empty.db")
    baseline = baseline_report(path, train_minimum=10)
    assert baseline["status"] == "INVALID_INPUT"
    assert baseline["execution_authority"] is False

    bandit = bandit_report(path, train_minimum=10)
    assert bandit["status"] == "INVALID_INPUT"
    assert bandit["execution_authority"] is False

    supervised = train(path, minimum_samples=10)
    assert supervised["status"] == "INVALID_INPUT"
    assert supervised["promotion_safe"] is False
    assert supervised["execution_authority"] is False


def test_research_model_reports_reject_malformed_timing_settings(tmp_path):
    path = str(tmp_path / "empty.db")
    assert baseline_report(path, test_size=0)["status"] == "INVALID_INPUT"
    assert bandit_report(path, min_context_samples=0)["status"] == "INVALID_INPUT"
    assert train(path, embargo_seconds=-1)["status"] == "INVALID_INPUT"
