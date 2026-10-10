"""Offline model-family selection gate.

This module deliberately does not train a model.  It determines whether the
current labelled journal is sufficient to compare simple probability models,
and records a reproducible candidate plan.  The gate prevents a complex model
from being introduced merely because a dependency is installed or a small
sample looks impressive.
"""
from __future__ import annotations

from typing import Any, Dict, List

try:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits
except ImportError:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits


FAMILY_ORDER = (
    ("logistic_regression", "linear_probability_baseline", "preferred_first"),
    ("gradient_boosting", "nonlinear_tabular_candidate", "second_after_logistic"),
    ("random_forest", "interaction_sanity_check", "only_with_hypothesis"),
    ("xgboost_lightgbm", "optional_gradient_boosting", "dependency_and_cost_review_required"),
    ("sequence_model", "temporal_model", "only_after_large_nonoverlap_sample"),
    ("ensemble", "model_combination", "only_after_individual_oos_wins"),
)


def evaluation_plan(path: str = "trading_state.db", *, minimum_train: int = 100,
                    test_size: int = 25, minimum_folds: int = 2) -> Dict[str, Any]:
    if (not isinstance(minimum_train, int) or isinstance(minimum_train, bool) or minimum_train < 100
            or not isinstance(test_size, int) or isinstance(test_size, bool) or test_size <= 0
            or not isinstance(minimum_folds, int) or isinstance(minimum_folds, bool) or minimum_folds < 2):
        return {"status": "INVALID_INPUT", "sample_size": 0, "folds": 0,
                "minimum_train": minimum_train, "test_size": test_size,
                "minimum_folds": minimum_folds, "families": [],
                "execution_authority": False,
                "reason": "model-family plan requires train>=100, positive test size, and at least two folds"}
    dataset = labelled_dataset(path)
    rows = list(dataset.get("rows", []))
    folds = purged_walk_forward_splits(rows, train_minimum=minimum_train, test_size=test_size, step=test_size)
    families: List[Dict[str, Any]] = []
    for key, purpose, rule in FAMILY_ORDER:
        families.append({"family": key, "purpose": purpose, "selection_rule": rule,
                         "status": "NOT_EVALUATED", "execution_authority": False})
    if len(rows) < minimum_train + test_size or len(folds) < minimum_folds:
        return {
            "status": "INSUFFICIENT_DATA", "sample_size": len(rows), "folds": len(folds),
            "minimum_train": minimum_train, "test_size": test_size, "minimum_folds": minimum_folds,
            "families": families, "dataset": {"excluded": dataset.get("excluded", {}),
                                                  "point_in_time": dataset.get("point_in_time", False),
                                                  "cost_model": dataset.get("cost_model")},
            "execution_authority": False,
            "reason": "No family is trained or promoted until labelled point-in-time rows and purged walk-forward folds exist.",
        }
    return {
        "status": "READY_FOR_OFFLINE_EVALUATION", "sample_size": len(rows), "folds": len(folds),
        "minimum_train": minimum_train, "test_size": test_size,
        "families": families, "execution_authority": False,
        "reason": "Evaluate in listed order against no-skill/random/EMA baselines; registry review remains required.",
    }
