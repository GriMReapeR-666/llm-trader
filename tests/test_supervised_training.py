from supervised_training import train


def test_supervised_training_fails_closed_without_journal(tmp_path):
    result = train(str(tmp_path / "missing.db"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["artifact_written"] is False
    assert result["promotion_safe"] is False
    assert result["execution_authority"] is False
    assert result["out_of_sample_status"] == "NOT_AVAILABLE"
    assert result["dataset"]["point_in_time"] is False
    assert result["training_data_hash"]
