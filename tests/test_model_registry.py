import pytest

from research.model_registry import register, review


def _register(path, metrics):
    return register(
        path, model_key="baseline", version="v1", kind="supervised",
        feature_schema_version="feature_snapshot_v1", training_data_hash="a" * 64,
        metrics=metrics, artifact_uri="artifacts/baseline.json",
    )


def test_model_approval_requires_all_promotion_evidence_flags(tmp_path):
    path = str(tmp_path / "models.db")
    record = _register(path, {"walk_forward_passed": True})
    with pytest.raises(ValueError, match="purged_validation_passed"):
        review(path, model_id=record["id"], status="APPROVED", reviewer="operator")


def test_model_approval_accepts_explicit_evidence_bundle(tmp_path):
    path = str(tmp_path / "models.db")
    record = _register(path, {
        "walk_forward_passed": True,
        "purged_validation_passed": True,
        "point_in_time": True,
        "sample_gate_passed": True,
        "cost_model_validated": True,
    })
    approved = review(path, model_id=record["id"], status="APPROVED", reviewer="operator")
    assert approved["status"] == "APPROVED"
