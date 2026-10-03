from contextual_bandit import bandit_report


def test_bandit_report_records_dataset_provenance(tmp_path):
    result = bandit_report(str(tmp_path / "missing.db"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["out_of_sample_status"] == "NOT_AVAILABLE"
    assert result["dataset"]["point_in_time"] is False
    assert result["dataset"]["cost_model"] == "unknown"
    assert result["dataset"]["training_data_hash"]
