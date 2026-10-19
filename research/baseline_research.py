"""Leakage-safe deterministic research baselines.

This module is deliberately offline-only.  It consumes the point-in-time
dataset exported by :mod:`research_validation` and produces walk-forward
scores for no-skill, fixed-random, and EMA-alignment baselines.  Nothing here
can approve an order or change position size.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np

try:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits
except ImportError:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits


MIN_BASELINE_TRAIN = 100


def _config_error(train_minimum: Any, test_size: Any, step: Any, embargo_seconds: Any) -> str | None:
    if (not isinstance(train_minimum, int) or isinstance(train_minimum, bool)
            or train_minimum < MIN_BASELINE_TRAIN):
        return f"train_minimum must be an integer >= {MIN_BASELINE_TRAIN}"
    if (not isinstance(test_size, int) or isinstance(test_size, bool) or test_size <= 0
            or not isinstance(step, int) or isinstance(step, bool) or step <= 0
            or not isinstance(embargo_seconds, int) or isinstance(embargo_seconds, bool)
            or embargo_seconds < 0):
        return "test_size, step, and embargo_seconds must be valid non-negative/integer research settings"
    return None


def _win(row: Dict[str, Any]) -> int:
    label = str(row.get("label", "")).upper()
    if label in {"WIN", "PROFIT", "POSITIVE", "TP"} or label.startswith("WIN_"):
        return 1
    if label in {"LOSS", "LOSSFUL", "NEGATIVE", "SL"} or label.startswith("LOSS_"):
        return 0
    if label in {"BREAKEVEN_EXIT", "TIME_EXIT", "EVENT_EXIT", "MANUAL_EXIT"}:
        # Baseline classification treats non-directional exits as neutral
        # outcomes and excludes them from win/loss accuracy below.
        raise ValueError(f"non-directional label is not a binary target: {label!r}")
    # The deterministic label is authoritative, but this guard makes a
    # malformed future label fail closed rather than silently become a loss.
    raise ValueError(f"unsupported deterministic label: {label!r}")


def _is_binary(row: Dict[str, Any]) -> bool:
    label = str(row.get("label", "")).upper()
    return label in {"WIN", "PROFIT", "POSITIVE", "TP", "LOSS", "LOSSFUL", "NEGATIVE", "SL"} or label.startswith(("WIN_", "LOSS_"))


def _probability_metrics(rows: Iterable[Dict[str, Any]], probabilities: Iterable[float], threshold: float = 0.5) -> Dict[str, Any]:
    rows = list(rows)
    probabilities = [min(1.0 - 1e-9, max(1e-9, float(p))) for p in probabilities]
    if not rows:
        return {"sample_size": 0, "accuracy": None, "brier": None, "log_loss": None,
                "expectancy_r": None, "net_return_r": None}
    outcomes = [_win(row) for row in rows]
    predictions = [int(p >= threshold) for p in probabilities]
    r_values = [float(row.get("r_multiple", 0.0)) for row in rows]
    return {
        "sample_size": len(rows),
        "threshold": threshold,
        "accuracy": sum(int(a == b) for a, b in zip(outcomes, predictions)) / len(rows),
        "brier": sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(rows),
        "log_loss": -sum(y * math.log(p) + (1 - y) * math.log(1 - p)
                          for p, y in zip(probabilities, outcomes)) / len(rows),
        # The outcome is already broker-reconciled net R.  Keeping this beside
        # classification metrics prevents accuracy from hiding cost drag.
        "expectancy_r": sum(r_values) / len(r_values),
        "net_return_r": sum(r_values),
    }


def _select_threshold(rows: List[Dict[str, Any]], probabilities: List[float]) -> float:
    """Choose a conservative take/skip threshold using training rows only."""
    if not rows:
        return 0.5
    candidates = [round(0.10 + i * 0.05, 2) for i in range(17)]
    best = (float("-inf"), 0.5)
    for threshold in candidates:
        selected = [row for row, probability in zip(rows, probabilities) if probability >= threshold]
        if not selected:
            score = float("-inf")
        else:
            score = sum(float(row.get("r_multiple", 0.0)) for row in selected) / len(selected)
        # Prefer a higher threshold when scores tie: a stricter gate is safer
        # until later validation demonstrates that more frequency is useful.
        if score > best[0] or (score == best[0] and threshold > best[1]):
            best = (score, threshold)
    return best[1]


def _group_probability(train: List[Dict[str, Any]], aligned: bool) -> float:
    group = []
    for row in train:
        if not _is_binary(row):
            continue
        features = row.get("features") or {}
        side = 1 if str(row.get("side", "")).upper() == "BUY" else -1
        ema_alignment = float(features.get("ema_alignment", 0) or 0)
        is_aligned = ema_alignment * side > 0
        if is_aligned == aligned:
            group.append(_win(row))
    return sum(group) / len(group) if group else (sum(_win(row) for row in train) / len(train) if train else 0.5)


def baseline_report(path: str = "trading_state.db", *, train_minimum: int = 100,
                    test_size: int = 25, step: int = 25, embargo_seconds: int = 0) -> Dict[str, Any]:
    """Score fixed baselines on purged chronological folds only."""
    configuration_error = _config_error(train_minimum, test_size, step, embargo_seconds)
    if configuration_error:
        return {"status": "INVALID_INPUT", "execution_authority": False,
                "configuration": {"train_minimum": train_minimum, "test_size": test_size,
                                   "step": step, "embargo_seconds": embargo_seconds},
                "folds": [], "models": {}, "threshold_selection": [],
                "reason": configuration_error}
    dataset = labelled_dataset(path)
    all_rows = dataset.get("rows", [])
    rows = [row for row in all_rows if _is_binary(row)]
    report: Dict[str, Any] = {
        "dataset": {"sample_size": dataset.get("sample_size", 0),
                    "excluded": dataset.get("excluded", {}),
                    "point_in_time": dataset.get("point_in_time", False),
                    "cost_model": dataset.get("cost_model")},
        "configuration": {"train_minimum": train_minimum, "test_size": test_size,
                           "step": step, "embargo_seconds": embargo_seconds},
        "excluded_non_binary_labels": len(all_rows) - len(rows),
        "folds": [], "models": {}, "threshold_selection": [], "execution_authority": False,
    }
    if not rows:
        report["status"] = "INSUFFICIENT_DATA"
        report["reason"] = "No closed trades passed deterministic labels, current feature schema, and broker-reconciled risk checks."
        return report
    folds = purged_walk_forward_splits(rows, train_minimum=train_minimum,
                                       test_size=test_size, step=step,
                                       embargo_seconds=embargo_seconds)
    if not folds:
        report["status"] = "INSUFFICIENT_DATA"
        report["reason"] = f"Need at least {train_minimum + test_size} labelled rows for one chronological test fold."
        return report
    model_rows: Dict[str, List[Dict[str, Any]]] = {"no_skill_prior": [], "random_50": [], "ema_alignment": []}
    model_probs: Dict[str, List[float]] = {key: [] for key in model_rows}
    for fold_number, fold in enumerate(folds, start=1):
        train = [rows[i] for i in fold["train"]]
        test = [rows[i] for i in fold["test"]]
        prior = sum(_win(row) for row in train) / len(train) if train else 0.5
        ema_probs = [_group_probability(train, (float((row.get("features") or {}).get("ema_alignment", 0) or 0) *
                                             (1 if str(row.get("side", "")).upper() == "BUY" else -1) > 0))
                     for row in test]
        fold_models = {
            "no_skill_prior": [prior] * len(test),
            "random_50": [0.5] * len(test),
            "ema_alignment": ema_probs,
        }
        train_models = {
            "no_skill_prior": [prior] * len(train),
            "random_50": [0.5] * len(train),
            "ema_alignment": [
                _group_probability(train, (float((row.get("features") or {}).get("ema_alignment", 0) or 0) *
                                    (1 if str(row.get("side", "")).upper() == "BUY" else -1) > 0))
                for row in train
            ],
        }
        selected_thresholds = {name: _select_threshold(train, probs) for name, probs in train_models.items()}
        report["threshold_selection"].append({"fold": fold_number, "thresholds": selected_thresholds,
                                               "selection_data": "train_only", "objective": "mean_net_R_on_selected_rows"})
        fold_result = {
            "fold": fold_number, "train_size": len(train), "test_size": len(test),
            "purged_size": len(fold.get("purged", [])), "embargoed_size": len(fold.get("embargoed", [])),
            "test_start": test[0].get("opened_at"), "test_end": test[-1].get("closed_at"),
            "selected_threshold_test_metrics": {
                name: _probability_metrics(test, probs, selected_thresholds[name])
                for name, probs in fold_models.items()
            },
        }
        report["folds"].append(fold_result)
        for name, probs in fold_models.items():
            model_rows[name].extend(test)
            model_probs[name].extend(probs)
    report["models"] = {name: _probability_metrics(model_rows[name], model_probs[name]) for name in model_rows}
    report["status"] = "READY_FOR_REVIEW"
    report["promotion_safe"] = False
    report["reason"] = "Baselines are research evidence only; a candidate still needs repeated out-of-sample superiority, model registry review, and operator approval."
    return report


def run_null_hypothesis_trade_permutation_test(
    r_values: Iterable[float] | Sequence[float],
    n_permutations: int = 1000,
    seed: int = 42,
    minimum_trades: int = 20,
) -> Dict[str, Any]:
    """Run non-parametric null hypothesis permutation test on trade outcomes.

    The null hypothesis is that the strategy has zero directional edge, meaning
    positive and negative trade returns are equally likely coin flips across the
    observed trade magnitude distribution (|R_i|).

    In each permutation step, random Rademacher signs (+1 or -1 with probability 0.5)
    are applied to absolute trade payoffs. The fraction of permuted sample means that
    equal or exceed the observed strategy mean R provides an empirical one-sided p-value.

    Offline research only: execution_authority is strictly False.
    """
    if isinstance(r_values, (str, bytes)) or not isinstance(r_values, (list, tuple, np.ndarray)):
        return {
            "status": "INVALID_INPUT",
            "reason": "r_values must be an iterable of numeric trade returns",
            "execution_authority": False,
        }

    clean_r: List[float] = []
    for r in r_values:
        if r is None or isinstance(r, bool):
            continue
        try:
            val = float(r)
            if math.isfinite(val):
                clean_r.append(val)
        except (ValueError, TypeError):
            continue

    if len(clean_r) < minimum_trades:
        return {
            "status": "INSUFFICIENT_DATA",
            "trade_count": len(clean_r),
            "minimum_required": minimum_trades,
            "reason": f"Observed trades ({len(clean_r)}) below required threshold ({minimum_trades})",
            "execution_authority": False,
        }

    n_perms = max(100, int(n_permutations))
    observed_mean = sum(clean_r) / len(clean_r)
    abs_mags = np.array([abs(x) for x in clean_r], dtype=float)
    n_trades = len(clean_r)

    rng = np.random.default_rng(seed)
    random_signs = rng.choice(np.array([-1.0, 1.0]), size=(n_perms, n_trades))
    permuted_means = (random_signs * abs_mags).mean(axis=1)

    p_value = float(np.mean(permuted_means >= observed_mean))
    p95 = float(np.percentile(permuted_means, 95.0))
    is_significant = bool(p_value < 0.05 and observed_mean > 0)

    return {
        "status": "READY_FOR_REVIEW",
        "trade_count": n_trades,
        "observed_expectancy_r": round(observed_mean, 6),
        "n_permutations": n_perms,
        "p_value": round(p_value, 6),
        "is_statistically_significant": is_significant,
        "permuted_mean_95th_percentile": round(p95, 6),
        "execution_authority": False,
    }
