import pytest

from research.research_validation import (labelled_dataset, promotion_gate, walk_forward_splits,
                                  _setup_floor_reasons, _stress_floor_reasons)
from research.setup_governance import change_gate, retirement_gate


def test_leakage_safe_dataset_extraction(tmp_path):
    db_file = str(tmp_path / "test_state.db")
    dataset = labelled_dataset(db_file)
    
    assert isinstance(dataset, dict)
    assert "sample_size" in dataset
    assert dataset["sample_size"] == 0
    assert dataset["rows"] == []


def test_walk_forward_purged_splits():
    rows = [{"id": i} for i in range(150)]
    folds = walk_forward_splits(rows, train_minimum=100, test_size=25, step=25)
    
    assert len(folds) == 2
    assert folds[0]["train"] == list(range(0, 100))
    assert folds[0]["test"] == list(range(100, 125))
    assert folds[1]["train"] == list(range(0, 125))
    assert folds[1]["test"] == list(range(125, 150))


def test_research_split_and_floor_helpers_fail_closed_on_malformed_inputs():
    assert walk_forward_splits([], train_minimum=0, test_size=25, step=25) == []
    assert walk_forward_splits([], train_minimum=100, test_size=25.5, step=25) == []
    assert _setup_floor_reasons({"closed_trades": "bad"}, minimum_closed=100) == ["metric_sample_count_malformed"]
    assert _stress_floor_reasons({"status": "READY_FOR_REVIEW", "scenarios": ["bad"]}) == ["stress_monte_carlo_malformed"]
    assert _stress_floor_reasons({"status": "READY_FOR_REVIEW", "scenarios": [{"monte_carlo": "bad"}]}) == ["stress_monte_carlo_malformed"]


def test_promotion_gate_and_sample_blocking():
    # Insufficient closed samples scenario -> Blocked
    insufficient = change_gate(
        closed_samples=10,
        out_of_sample_passed=False,
        previous_version_preserved=True,
        minimum_samples=100,
    )
    assert insufficient["allowed"] is False
    assert insufficient["execution_authority"] is False
    assert any("need 100 closed samples" in r for r in insufficient["reasons"])

    # Sufficient samples and passed OOS validation scenario -> Allowed for setup change (authority remains False)
    sufficient = change_gate(
        closed_samples=120,
        out_of_sample_passed=True,
        previous_version_preserved=True,
        minimum_samples=100,
    )
    assert sufficient["allowed"] is True
    assert sufficient["execution_authority"] is False

    # Retirement gate on degradation
    retire = retirement_gate(
        live_expectancy_r=-0.5,
        oos_expectancy_r=0.2,
        degradation_fraction=0.40,
        closed_samples=150,
    )
    assert retire["retire_or_freeze"] is True
    assert retire["automatic_action"] == "FREEZE_AND_REQUIRE_OPERATOR_REVIEW"


def test_research_threshold_cannot_be_lowered_below_policy_floor(tmp_path):
    report = promotion_gate(str(tmp_path / "threshold.db"), minimum_closed_per_setup=1)
    assert report["configuration_valid"] is False
    assert report["configuration_error"]
    assert report["minimum_closed_per_setup"] == 100
    assert report["eligible_setups"] == []

    change = change_gate(
        closed_samples=200,
        out_of_sample_passed=True,
        previous_version_preserved=True,
        minimum_samples=1,
    )
    assert change["allowed"] is False
    assert any("policy floor" in reason for reason in change["reasons"])
