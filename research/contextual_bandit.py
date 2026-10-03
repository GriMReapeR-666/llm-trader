"""Offline contextual take/skip bandit for setup ranking.

The bandit observes only reconciled, point-in-time labelled trades.  It is a
research report, not a permission gate, and has no MT5/provider dependencies.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from typing import Any, Dict, List

try:
    from research_validation import labelled_dataset, purged_walk_forward_splits
except ImportError:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits


def _config_error(train_minimum: Any, test_size: Any, step: Any,
                  min_context_samples: Any) -> str | None:
    if (not isinstance(train_minimum, int) or isinstance(train_minimum, bool)
            or train_minimum < 100):
        return "train_minimum must be an integer >= 100"
    if (not isinstance(test_size, int) or isinstance(test_size, bool) or test_size <= 0
            or not isinstance(step, int) or isinstance(step, bool) or step <= 0):
        return "test_size and step must be positive integers"
    if (not isinstance(min_context_samples, int) or isinstance(min_context_samples, bool)
            or min_context_samples < 1):
        return "min_context_samples must be a positive integer"
    return None


def _arm_stats(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    wins = sum(1 for row in rows if float(row.get("r_multiple", 0.0)) > 0)
    losses = sum(1 for row in rows if float(row.get("r_multiple", 0.0)) <= 0)
    r = [float(row.get("r_multiple", 0.0)) for row in rows]
    # Beta(1,1) shrinkage prevents one lucky trade from becoming certainty.
    return {"samples": len(rows), "posterior_win_rate": (wins + 1) / (wins + losses + 2),
            "mean_r": sum(r) / len(r) if r else 0.0,
            "take_score": (sum(r) / len(r) if r else 0.0) * ((len(r) + 2) / (len(r) + 10))}


def bandit_report(path: str = "trading_state.db", *, train_minimum: int = 100,
                  test_size: int = 25, step: int = 25, min_context_samples: int = 5) -> Dict[str, Any]:
    configuration_error = _config_error(train_minimum, test_size, step, min_context_samples)
    if configuration_error:
        return {"status": "INVALID_INPUT", "out_of_sample_status": "NOT_AVAILABLE",
                "execution_authority": False, "folds": [], "reason": configuration_error,
                "configuration": {"train_minimum": train_minimum, "test_size": test_size,
                                   "step": step, "min_context_samples": min_context_samples}}
    dataset = labelled_dataset(path)
    rows = dataset.get("rows", [])
    report: Dict[str, Any] = {"dataset": {
                                  "sample_size": len(rows),
                                  "excluded": dataset.get("excluded", {}),
                                  "point_in_time": dataset.get("point_in_time", False),
                                  "cost_model": dataset.get("cost_model", "unknown"),
                                  "training_data_hash": hashlib.sha256(json.dumps(
                                      [row.get("ticket") for row in rows], sort_keys=True
                                  ).encode("utf-8")).hexdigest(),
                              }, "execution_authority": False, "folds": []}
    folds = purged_walk_forward_splits(rows, train_minimum=train_minimum, test_size=test_size, step=step) if rows else []
    if not folds:
        report.update({"status": "INSUFFICIENT_DATA", "out_of_sample_status": "NOT_AVAILABLE",
                       "reason": "Need a sufficiently large labelled journal for chronological bandit evaluation."})
        return report
    for fold_number, fold in enumerate(folds, start=1):
        train = [rows[i] for i in fold["train"]]
        test = [rows[i] for i in fold["test"]]
        contexts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in train:
            contexts[str(row.get("setup_id", "unknown"))].append(row)
        taken, skipped, taken_r = 0, 0, []
        decisions = []
        for row in test:
            context = str(row.get("setup_id", "unknown"))
            stats = _arm_stats(contexts.get(context, []))
            take = stats["samples"] >= min_context_samples and stats["take_score"] > 0
            if take:
                taken += 1
                taken_r.append(float(row.get("r_multiple", 0.0)))
            else:
                skipped += 1
            decisions.append({"ticket": row.get("ticket"), "setup_id": context, "action": "TAKE" if take else "SKIP",
                              "train_context": stats})
            # Online update happens only after the test outcome; this is an
            # offline simulation and cannot leak future values into selection.
            contexts[context].append(row)
        report["folds"].append({"fold": fold_number, "train_size": len(train), "test_size": len(test),
                                "taken": taken, "skipped": skipped,
                                "coverage": taken / len(test) if test else 0.0,
                                "selected_expectancy_r": sum(taken_r) / len(taken_r) if taken_r else None,
                                "selected_net_r": sum(taken_r), "decisions": decisions})
    report.update({"status": "READY_FOR_REVIEW", "out_of_sample_status": "EVALUATED_PURGED_WALK_FORWARD",
                   "promotion_safe": False,
                   "reason": "Bandit output is an offline ranking artifact; it needs baseline superiority, stability, and operator approval before any confidence adjustment."})
    return report
