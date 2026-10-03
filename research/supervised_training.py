"""Leakage-safe supervised baseline training.

The trainer is intentionally conservative: it consumes only rows accepted by
``research_validation.labelled_dataset``; standardization and coefficients are
fit inside each chronological training fold; and no artifact is written or
promoted unless the minimum sample and purged walk-forward gates pass.  The
result is research evidence, never an order permission.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

try:
    from baseline_research import _is_binary, _probability_metrics
except ImportError:
    from research.baseline_research import _is_binary, _probability_metrics

try:
    from feature_schema import REQUIRED_VALUES
except ImportError:
    from research.feature_schema import REQUIRED_VALUES

try:
    from research_validation import labelled_dataset, purged_walk_forward_splits
except ImportError:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits


MODEL_KIND = "logistic_regression_v1"


def _config_error(minimum_samples: Any, test_size: Any, step: Any, embargo_seconds: Any) -> str | None:
    if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
            or minimum_samples < 100):
        return "minimum_samples must be an integer >= 100"
    if (not isinstance(test_size, int) or isinstance(test_size, bool) or test_size <= 0
            or not isinstance(step, int) or isinstance(step, bool) or step <= 0
            or not isinstance(embargo_seconds, int) or isinstance(embargo_seconds, bool)
            or embargo_seconds < 0):
        return "test_size, step, and embargo_seconds must be valid research settings"
    return None


def _rows(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    dataset = labelled_dataset(path)
    rows = [row for row in dataset.get("rows", []) if _is_binary(row)]
    return dataset, rows


def _matrix(rows: Sequence[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    usable: List[Dict[str, Any]] = []
    values: List[List[float]] = []
    labels: List[float] = []
    for row in rows:
        feature_values = (row.get("features") or {}).get("feature_values") or {}
        try:
            vector = [float(feature_values[name]) for name in REQUIRED_VALUES]
        except (KeyError, TypeError, ValueError):
            continue
        label = str(row.get("label", "")).upper()
        if label.startswith("WIN") or label in {"PROFIT", "POSITIVE", "TP"}:
            target = 1.0
        elif label.startswith("LOSS") or label in {"NEGATIVE", "SL"}:
            target = 0.0
        else:
            continue
        if not all(math.isfinite(item) for item in vector):
            continue
        values.append(vector)
        labels.append(target)
        usable.append(row)
    return np.asarray(values, dtype=float), np.asarray(labels, dtype=float), usable


def _fit_logistic(x: np.ndarray, y: np.ndarray, *, epochs: int = 600, lr: float = 0.05, l2: float = 1e-3) -> Tuple[np.ndarray, float, Dict[str, Any]]:
    """Fit a small deterministic L2 logistic model without external ML state."""
    if len(x) == 0 or len(set(y.tolist())) < 2:
        raise ValueError("training fold must contain both outcome classes")
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    z = (x - mean) / scale
    weights = np.zeros(z.shape[1], dtype=float)
    bias = 0.0
    for _ in range(epochs):
        logits = np.clip(z @ weights + bias, -30.0, 30.0)
        probs = 1.0 / (1.0 + np.exp(-logits))
        error = probs - y
        weights -= lr * ((z.T @ error) / len(z) + l2 * weights)
        bias -= lr * float(error.mean())
    return weights, float(bias), {"mean": mean.tolist(), "scale": scale.tolist(), "feature_names": list(REQUIRED_VALUES)}


def _predict(x: np.ndarray, weights: np.ndarray, bias: float, transform: Dict[str, Any]) -> List[float]:
    if len(x) == 0:
        return []
    mean = np.asarray(transform["mean"], dtype=float)
    scale = np.asarray(transform["scale"], dtype=float)
    logits = np.clip(((x - mean) / scale) @ weights + bias, -30.0, 30.0)
    return (1.0 / (1.0 + np.exp(-logits))).tolist()


def train(path: str = "trading_state.db", *, minimum_samples: int = 100,
          test_size: int = 25, step: int = 25, embargo_seconds: int = 0,
          artifact_path: str = "") -> Dict[str, Any]:
    configuration_error = _config_error(minimum_samples, test_size, step, embargo_seconds)
    if configuration_error:
        return {"status": "INVALID_INPUT", "model_kind": MODEL_KIND,
                "execution_authority": False, "promotion_safe": False,
                "artifact_written": False, "out_of_sample_status": "NOT_AVAILABLE",
                "minimum_samples": minimum_samples,
                "configuration": {"test_size": test_size, "step": step,
                                   "embargo_seconds": embargo_seconds},
                "folds": [], "reason": configuration_error}
    dataset, accepted_rows = _rows(path)
    x, y, rows = _matrix(accepted_rows)
    training_data_hash = hashlib.sha256(json.dumps(
        [row.get("ticket") for row in rows], sort_keys=True
    ).encode("utf-8")).hexdigest()
    report: Dict[str, Any] = {
        "status": "INSUFFICIENT_DATA", "model_kind": MODEL_KIND,
        "dataset_sample_size": int(dataset.get("sample_size", 0)),
        "usable_numeric_sample_size": int(len(rows)),
        "dataset": {"point_in_time": bool(dataset.get("point_in_time", False)),
                     "cost_model": dataset.get("cost_model", "unknown"),
                     "excluded": dataset.get("excluded", {})},
        "training_data_hash": training_data_hash,
        "out_of_sample_status": "NOT_AVAILABLE",
        "minimum_samples": int(minimum_samples),
        "configuration": {"test_size": test_size, "step": step, "embargo_seconds": embargo_seconds},
        "folds": [], "metrics": {}, "artifact_written": False,
        "promotion_safe": False, "execution_authority": False,
    }
    if len(rows) < minimum_samples + test_size:
        report["reason"] = f"Need at least {minimum_samples + test_size} usable labelled rows before training."
        return report
    folds = purged_walk_forward_splits(rows, train_minimum=minimum_samples,
                                       test_size=test_size, step=step,
                                       embargo_seconds=embargo_seconds)
    if not folds:
        report["reason"] = "No purged chronological walk-forward fold is available."
        return report
    all_test_rows: List[Dict[str, Any]] = []
    all_test_probs: List[float] = []
    last_model: Dict[str, Any] | None = None
    for fold_no, fold in enumerate(folds, start=1):
        train_idx, test_idx = fold["train"], fold["test"]
        train_x, train_y = x[train_idx], y[train_idx]
        test_x = x[test_idx]
        try:
            weights, bias, transform = _fit_logistic(train_x, train_y)
        except ValueError as exc:
            report["folds"].append({"fold": fold_no, "status": "SKIPPED", "reason": str(exc)})
            continue
        probabilities = _predict(test_x, weights, bias, transform)
        test_rows = [rows[index] for index in test_idx]
        all_test_rows.extend(test_rows)
        all_test_probs.extend(probabilities)
        last_model = {"weights": weights.tolist(), "bias": bias, "transform": transform}
        report["folds"].append({
            "fold": fold_no, "status": "EVALUATED", "train_size": len(train_idx),
            "test_size": len(test_idx), "purged_size": len(fold.get("purged", [])),
            "embargoed_size": len(fold.get("embargoed", [])),
            "metrics": _probability_metrics(test_rows, probabilities),
            "selection_data": "train_only_fit_test_only_evaluation",
        })
    if not all_test_rows or last_model is None:
        report["reason"] = "No fold contained both outcome classes; no model was trained."
        return report
    report["metrics"] = _probability_metrics(all_test_rows, all_test_probs)
    report["status"] = "READY_FOR_REVIEW"
    report["out_of_sample_status"] = "EVALUATED_PURGED_WALK_FORWARD"
    report["reason"] = "Out-of-sample evidence exists, but registry review and operator approval are still required."
    if artifact_path:
        destination = Path(artifact_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {"model_kind": MODEL_KIND, "training_data_hash": training_data_hash,
                   "model": last_model, "report": report, "execution_authority": False}
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        report["artifact_written"] = True
        report["artifact_path"] = str(destination)
    return report


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
